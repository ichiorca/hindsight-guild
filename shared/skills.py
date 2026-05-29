"""Agent Skills loader — Claude Code-compatible, ADK-native.

Implements the open Agent Skills standard (Anthropic, Dec 2025) with the same
three-tier progressive disclosure model Claude Code uses, adapted to ADK's
LlmAgent runtime.

Tier 1 — Discovery (always loaded):
  Skill `name` + one-line `description` from YAML frontmatter are injected
  into the agent's `instruction` string at construction. ~80 tokens/skill.
  The model sees skills exist and decides when to invoke them.

Tier 2 — Activation (on demand):
  When the model calls `read_skill(name)`, we return the full SKILL.md body.
  The model loads it into context for the duration of the turn.

Tier 3 — Deep dives (on body reference):
  SKILL.md body may reference `references/<file>.md` (and assets/scripts).
  Model calls `read_skill_reference(name, path)` — we resolve safely under
  the skill's directory and return contents.

Storage shape mirrors the spec exactly:
    skills/
      <skill_name>/
        SKILL.md                    # YAML frontmatter + body
        references/*.md             # optional deep-dive files
        assets/*                    # optional binary assets
        scripts/*                   # optional executable scripts (not run by us)

Where the spec says "filesystem", we mean the same — `skills/` directory
inside the deployed container, or repo-local for dev. No embeddings. No
vectorization. Verbatim text loaded into context only when relevant.

Why mirror Claude Code: the open spec is portable across runtimes (Claude
Code, Gemini CLI, Codex CLI, etc.) so the SKILL.md files we author here
also work in those tools without modification.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import yaml

# google.adk.tools.FunctionTool is imported lazily inside make_skill_tools()
# so web_api callers that only need registry() / read_body() (no agent
# construction) can run without google-adk installed. The web_api's
# /api/capabilities endpoint is one such caller.
from shared import mongo_tools
from shared.provenance import attach_provenance

log = logging.getLogger(__name__)

# Configured via env so the container can mount a different path; defaults
# to the repo-local skills/ directory at the project root.
SKILLS_ROOT = Path(os.environ.get(
    "SKILLS_ROOT",
    str(Path(__file__).resolve().parent.parent / "skills"),
))

# Safety: skill names must be filesystem-safe slugs. Matches Claude Code's
# constraint.
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Sample size for the content-hash leg of the file signature. SKILL.md
# files are small; 4 KB is plenty to disambiguate same-mtime same-size
# rewrites on filesystems with 1-second mtime resolution (FAT, some NFS
# mounts, certain ext4 configs). This is detection, not data integrity —
# blake2b is just a fast, dep-free, non-cryptographic-grade hash.
_SIG_HASH_SAMPLE_BYTES = 4096


# ---------------------------------------------------------------------------
# Data model — mirrors the Agent Skills spec's YAML frontmatter
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillFrontmatter:
    name: str
    description: str
    version: str = "1.0.0"
    # Spec-optional fields; we surface them so prompts can reference them.
    user_invokable: bool = True
    tested_date: str | None = None
    tested_with: str | None = None


@dataclass(frozen=True)
class Skill:
    frontmatter: SkillFrontmatter
    skill_dir: Path        # absolute path to <skills_root>/<name>
    body_path: Path        # absolute path to SKILL.md

    @property
    def name(self) -> str:
        return self.frontmatter.name


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

_FRONTMATTER_DELIM = "---"


def _parse_skill_md(text: str) -> tuple[dict, str]:
    """Parse a SKILL.md file. Returns (frontmatter_dict, body_text).

    Spec requires the file to begin with `---\\n`, end the frontmatter with
    `\\n---\\n`, and contain valid YAML between. Malformed files raise.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != _FRONTMATTER_DELIM:
        raise ValueError("SKILL.md must begin with YAML frontmatter (---)")

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONTMATTER_DELIM:
            end_idx = i
            break
    if end_idx is None:
        raise ValueError("SKILL.md frontmatter missing closing ---")

    yaml_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1:]).lstrip("\n")
    fm = yaml.safe_load(yaml_text) or {}
    return fm, body


def _frontmatter_from_dict(fm: dict) -> SkillFrontmatter:
    if "name" not in fm or "description" not in fm:
        raise ValueError("frontmatter must include `name` and `description`")
    name = str(fm["name"]).strip()
    if not SKILL_NAME_RE.match(name):
        raise ValueError(f"skill name {name!r} is not a valid slug")

    # Spec allows version under `metadata.version` (marketingskills format)
    # OR top-level `version` (claude-ads format). Accept both.
    version = "1.0.0"
    if isinstance(fm.get("metadata"), dict) and "version" in fm["metadata"]:
        version = str(fm["metadata"]["version"])
    elif "version" in fm:
        version = str(fm["version"])

    return SkillFrontmatter(
        name=name,
        description=str(fm["description"]).strip(),
        version=version,
        user_invokable=bool(fm.get("user-invokable", True)),
        tested_date=fm.get("tested_date"),
        tested_with=fm.get("tested_with"),
    )


def _file_signature(path: Path) -> tuple[float, int, bytes]:
    """Return a (mtime, size, hash) signature for change detection.

    The hash is a 16-byte blake2b digest of the first
    ``_SIG_HASH_SAMPLE_BYTES`` of the file. Sampling rather than hashing
    the whole file is intentional: this signature is only consulted when
    mtime and size already match, so we're disambiguating a narrow case
    (same-second rewrite, same byte length) where even a prefix sample
    is overwhelmingly likely to differ between authored versions of a
    SKILL.md.
    """
    st = path.stat()
    h = hashlib.blake2b(digest_size=16)
    with path.open("rb") as f:
        h.update(f.read(_SIG_HASH_SAMPLE_BYTES))
    return (st.st_mtime, st.st_size, h.digest())


# ---------------------------------------------------------------------------
# Mongo lookup — Mongo is the source of truth; disk is a derived cache
# ---------------------------------------------------------------------------

def _mongo_body_for_current(name: str) -> str | None:
    """Return ``skills.<name>.versions[current_version].body_md`` from Mongo,
    or ``None`` for playbook skills, missing skills, or any Mongo failure.

    ``None`` means "use whatever's on disk" — the disk cache stays valid
    when Mongo can't give us a definitive answer. This keeps agent reads
    resilient to transient DB hiccups: the Mongo source of truth is
    authoritative when reachable, the on-disk cache is a graceful fallback
    when it isn't.
    """
    try:
        doc = mongo_tools.find_one("skills", {"_id": name})
    except Exception as e:
        log.debug("mongo lookup for skill %s failed: %s", name, e)
        return None
    if not doc:
        return None
    # Playbook skills don't store body_md in versions; only agent_skill kind
    # is the source-of-truth-in-Mongo. Other kinds degrade to disk.
    if doc.get("skill_kind") != "agent_skill":
        return None
    current = doc.get("current_version")
    if not current:
        return None
    versions = doc.get("versions") or {}
    body = (versions.get(current) or {}).get("body_md")
    if not isinstance(body, str) or not body:
        return None
    return body


# ---------------------------------------------------------------------------
# Registry — scans a root dir at startup
# ---------------------------------------------------------------------------

class SkillRegistry:
    """In-process index of available skills. Built once at agent construction.

    Mirrors what Claude Code does on startup: walk the skills dir, parse
    every SKILL.md's frontmatter, keep a name→Skill map. The bodies are
    NOT loaded until the model asks for them.
    """

    def __init__(self, root: Path):
        self.root = root
        self.skills: dict[str, Skill] = {}
        self._scan_errors: list[tuple[str, str]] = []
        # Per-skill (mtime, size, hash) snapshot so read_body can detect
        # external writes (e.g., another container's promotion_gate flipped
        # the file) and re-parse frontmatter without a process restart.
        # The size + content-hash legs guard against same-second rewrites
        # on filesystems with 1-second mtime resolution where the mtime
        # alone would not change.
        self._sigs: dict[str, tuple[float, int, bytes]] = {}
        self._scan()

    def _scan(self) -> None:
        if not self.root.exists():
            log.warning("skills root %s does not exist; registry empty", self.root)
            return
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue  # not a skill directory
            try:
                fm_dict, _ = _parse_skill_md(skill_md.read_text(encoding="utf-8"))
                fm = _frontmatter_from_dict(fm_dict)
                if fm.name != entry.name:
                    raise ValueError(
                        f"skill `name` ({fm.name!r}) must match directory ({entry.name!r})"
                    )
                self.skills[fm.name] = Skill(
                    frontmatter=fm,
                    skill_dir=entry.resolve(),
                    body_path=skill_md.resolve(),
                )
                self._sigs[fm.name] = _file_signature(skill_md)
            except Exception as e:
                self._scan_errors.append((entry.name, str(e)))
                log.warning("failed to load skill %s: %s", entry.name, e)

    def _maybe_refresh_one(self, name: str) -> None:
        """Re-scan a single skill if its SKILL.md (mtime, size, hash)
        signature changed since the last scan. The signature is compared
        leg-by-leg to keep the fast path cheap: a single stat() yields
        mtime + size, and the content sample hash is only computed when
        both of those match the cached value.

        Catches promotions written by a *different* container — common in
        production where promotion_gate runs in its own Cloud Run job and
        the a2a-* containers are separate processes — and is robust to
        filesystems with 1-second mtime resolution where two writes inside
        the same second leave mtime unchanged.
        """
        skill = self.skills.get(name)
        if not skill:
            return
        try:
            st = skill.body_path.stat()
        except OSError:
            return
        cached = self._sigs.get(name)
        cur_mtime, cur_size = st.st_mtime, st.st_size

        # Fast path: mtime differs — definitely changed, skip the hash
        # until we're committed to reloading anyway.
        if cached is not None:
            cached_mtime, cached_size, cached_hash = cached
            if cur_mtime == cached_mtime and cur_size == cached_size:
                # Same mtime + size — only now is a content sample worth
                # computing. Could be a same-second rewrite of equal length.
                try:
                    h = hashlib.blake2b(digest_size=16)
                    with skill.body_path.open("rb") as f:
                        h.update(f.read(_SIG_HASH_SAMPLE_BYTES))
                    if h.digest() == cached_hash:
                        return  # nothing changed
                except OSError:
                    return

        try:
            fm_dict, _ = _parse_skill_md(skill.body_path.read_text(encoding="utf-8"))
            fm = _frontmatter_from_dict(fm_dict)
            self.skills[name] = Skill(
                frontmatter=fm,
                skill_dir=skill.skill_dir,
                body_path=skill.body_path,
            )
            self._sigs[name] = _file_signature(skill.body_path)
            log.info("auto-refreshed skill %s (signature changed)", name)
        except Exception as e:
            log.warning("auto-refresh of %s failed: %s", name, e)

    def names(self) -> list[str]:
        return sorted(self.skills.keys())

    def metadata_block(self, allowed: list[str] | None = None) -> str:
        """Render the Tier 1 system-prompt section: a list of available
        skills with descriptions, instructing the model how to invoke them.

        If `allowed` is provided, only those skills are surfaced — same
        progressive-disclosure mechanism but with per-agent governance.
        """
        skills = (
            [self.skills[n] for n in allowed if n in self.skills]
            if allowed is not None
            else list(self.skills.values())
        )
        if not skills:
            return ""

        lines = [
            "",
            "## Available Skills",
            "",
            "You have access to the following capability packs. **The full body",
            "of a skill is NOT in your context** — only the metadata below is.",
            "When a user request matches one of these descriptions, call",
            "`read_skill(name)` to load the full SKILL.md. If that body",
            "references a `references/*.md` file you need, call",
            "`read_skill_reference(name, path)` to load it.",
            "",
        ]
        for s in skills:
            lines.append(
                f"- **{s.name}** (v{s.frontmatter.version}) — {s.frontmatter.description}"
            )
        lines.append("")
        return "\n".join(lines)

    def read_body(self, name: str) -> str:
        """Return the body of the current version of skill `name`.

        Self-heals from Mongo if on-disk content is stale or missing.
        Mongo (``skills.<name>.versions[current_version].body_md``) is the
        source of truth for agent_skill kinds; the on-disk
        ``skills/<name>/SKILL.md`` is a derived cache. Each call:

          1. Looks up the Mongo current_version's body_md (if any).
          2. If present AND it differs from what's on disk (or no file
             exists yet), atomically rewrites SKILL.md from Mongo using
             a temp file + ``os.replace`` so a partial write can never
             be observed.
          3. Refreshes the in-process registry's parsed Skill so the
             signature cache picks up the new file.
          4. Returns the body (with frontmatter stripped).

        Playbook skills and any skill missing from Mongo degrade
        gracefully: the on-disk file is served as-is. Mongo failures
        also degrade — we never want a transient DB hiccup to break
        agent reads of skills that are already correct on disk.
        """
        # Pull Mongo's current body and reconcile disk against it so
        # multi-container deployments self-heal without cross-process
        # plumbing (any container's next read fixes its own disk cache).
        self._reconcile_disk_with_mongo(name)
        # Catch any other source of disk drift — e.g. a directly-edited
        # SKILL.md in dev — without a process restart.
        self._maybe_refresh_one(name)
        skill = self._require(name)
        text = skill.body_path.read_text(encoding="utf-8")
        # Strip frontmatter from what we hand to the model — it already saw
        # the description; the body is what matters now.
        _, body = _parse_skill_md(text)
        return body

    def _reconcile_disk_with_mongo(self, name: str) -> None:
        """If Mongo has a current_version body_md for ``name`` that differs
        from the on-disk SKILL.md (or no file exists), atomically rewrite
        the on-disk file from Mongo. No-op for playbook skills, missing
        Mongo docs, or any Mongo failure.
        """
        mongo_body = _mongo_body_for_current(name)
        if mongo_body is None:
            return  # playbook skill, missing, or Mongo unavailable

        skill_dir = SKILLS_ROOT / name
        target = skill_dir / "SKILL.md"

        # Fast path: read on-disk content and compare. If they match, no
        # write needed.
        try:
            on_disk = target.read_text(encoding="utf-8")
            if on_disk == mongo_body:
                return
        except (FileNotFoundError, OSError):
            on_disk = None  # disk file missing — write it

        # Atomic temp-file + os.replace so partial writes are never visible
        # to a concurrent reader. Same pattern the old
        # _sync_agent_skill_to_disk used. Uses SKILLS_ROOT exactly so
        # sandbox tmpdir overrides still take effect.
        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("could not create skill dir %s: %s", skill_dir, e)
            return

        fd, tmp_path = tempfile.mkstemp(dir=str(skill_dir),
                                         prefix=".SKILL.md.",
                                         suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(mongo_body)
            os.replace(tmp_path, str(target))
        except Exception:
            # Cleanup orphan temp file. Then re-raise — Mongo is still
            # correct, so the next read will retry; surfacing the error
            # is better than silent staleness.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        log.info("self-healed skill %s from Mongo (current_version body_md "
                  "differed from disk)", name)

    def read_reference(self, name: str, reference_path: str) -> str:
        """Resolve a reference path safely under the skill's directory.

        Path traversal (../, absolute paths) is rejected so a misbehaving
        agent can't read arbitrary files via this tool.
        """
        skill = self._require(name)
        # Disallow absolute paths + parent traversal
        ref = Path(reference_path)
        if ref.is_absolute() or ".." in ref.parts:
            raise ValueError(
                f"reference_path {reference_path!r} must be relative and "
                f"contained within the skill directory"
            )
        resolved = (skill.skill_dir / ref).resolve()
        # Re-check after resolution that we're still inside the skill dir
        if skill.skill_dir not in resolved.parents and resolved != skill.skill_dir:
            raise ValueError(
                f"reference_path escapes skill directory: {reference_path!r}"
            )
        if not resolved.is_file():
            raise FileNotFoundError(
                f"skill {name!r} has no reference at {reference_path!r}"
            )
        return resolved.read_text(encoding="utf-8")

    def _require(self, name: str) -> Skill:
        skill = self.skills.get(name)
        if not skill:
            raise KeyError(
                f"skill {name!r} is not installed. "
                f"Available: {', '.join(self.names()) or '(none)'}"
            )
        return skill

    def refresh(self) -> None:
        """Re-scan the skills directory in place.

        Call this after writing a new SKILL.md to disk so long-running
        agent processes pick up the change without a restart. Clears any
        stale Skill objects + their cached bodies (each ``read_body`` /
        ``read_reference`` re-reads from disk, so no body cache to flush,
        but the ``skills`` dict's frontmatter and body_path metadata need
        to be re-derived).
        """
        self.skills = {}
        self._scan_errors = []
        self._scan()
        log.info("SkillRegistry refreshed: %d skills, %d scan errors",
                  len(self.skills), len(self._scan_errors))


@lru_cache(maxsize=1)
def registry() -> SkillRegistry:
    """Process-wide singleton. Built lazily on first access."""
    return SkillRegistry(SKILLS_ROOT)


def refresh_registry() -> None:
    """Force a full re-scan of the process-wide SkillRegistry.

    Most refresh needs are handled automatically: ``read_body`` reconciles
    against Mongo's current_version body_md on every call and writes a new
    SKILL.md if the on-disk content is stale (or missing). This means
    multi-container deployments self-heal — the writing container doesn't
    need to fan out a refresh notification.

    This full re-scan is for the rarer case: a new skill *directory* has
    appeared, or you want to surface frontmatter changes via the
    system-prompt metadata block (which is captured once at agent
    construction and only re-renders on full scan).

    Cheap to call — the scan is O(installed_skills), bounded ~30.
    """
    registry().refresh()


# ---------------------------------------------------------------------------
# Telemetry — every Tier 2/3 load lands in state.skill_usage so we can
# answer "which skills get used most? by which agents?"
# ---------------------------------------------------------------------------

def _stamp_session_load(tool_context, skill_name: str) -> None:
    """Push the loaded Skill onto the session's ``_skills_loaded`` list.

    The agent's after_agent_callback reads this and stamps it onto the
    TelemetryRecord, so every Agent Skill that contributed to an action
    can be credited/blamed in derived.agent_skill_track_records.

    ``tool_context`` is None if ADK didn't inject one (older versions or
    direct invocation in tests) — we skip silently rather than fail.
    """
    if tool_context is None:
        return
    try:
        state = tool_context.state
        loads = state.get("_skills_loaded") or []
        if skill_name not in loads:
            loads.append(skill_name)
        state["_skills_loaded"] = loads
    except Exception as e:
        log.debug("could not stamp session load for %s: %s", skill_name, e)


def _emit_usage(agent_name: str, skill_name: str, tier: int,
                 reference_path: str | None = None,
                 tokens_estimated: int | None = None) -> None:
    try:
        doc = attach_provenance(
            {
                "skill_name": skill_name,
                "agent_name": agent_name,
                "tier": tier,
                "reference_path": reference_path,
                "tokens_estimated": tokens_estimated,
                "ts": datetime.now(UTC),
            },
            kind="agent",
            actor_id=agent_name,
            source_kind="skill_load",
            source_ref_id=skill_name,
            source_ref_collection="skills/",
            trust_tier="verified",
        )
        mongo_tools.db()["skill_usage"].insert_one(doc)
    except Exception as e:
        # Telemetry must never fail the agent run
        log.warning("skill_usage telemetry emit failed: %s", e)


# ---------------------------------------------------------------------------
# FunctionTools — what the agent actually calls
# ---------------------------------------------------------------------------

def make_skill_tools(*, agent_name: str,
                      allowed: list[str] | None = None) -> list:
    """Return the skill tools scoped to an agent. The `allowed` list, if
    provided, enforces per-agent governance: the agent can only read skills
    on the list. Tool docstrings tell the model the constraint.

    Returns 3 tools: list_skills, read_skill, read_skill_reference. We
    expose list_skills even though the metadata is in the system prompt —
    it's useful when the agent wants to programmatically enumerate (e.g.
    in a longer multi-turn session where metadata may have rolled out of
    attention).

    ``FunctionTool`` is imported here (not at module top) so callers that
    only need registry() / read_body() — like the web_api's /api/capabilities
    endpoint — don't need google-adk installed in their environment.
    """
    from google.adk.tools import FunctionTool

    reg = registry()
    allow_set = set(allowed) if allowed is not None else None

    def _denied(name: str) -> str | None:
        """Recoverable permission message for ``name``, or None if allowed.

        Returns rather than raises: an exception raised inside an ADK
        FunctionTool aborts the *entire* agent run, whereas a returned string
        is handed back to the model — which can then recover (e.g. read the
        skill's Mongo document via ``mongodb_find`` instead of this
        file-backed tool). The denial itself is unchanged: out-of-allowlist
        skills are still refused. This matters for meta-agents like
        self_critique that reason over arbitrary skills they don't load —
        a stray ``read_skill`` on the skill under review must not nuke the
        whole pass.
        """
        if allow_set is not None and name not in allow_set:
            return (
                f"ERROR: agent {agent_name!r} is not permitted to read skill "
                f"{name!r} via this tool (allowed: {sorted(allow_set)}). To "
                f"read this skill's body, query its document instead: "
                f'mongodb_find("skills", {{"_id": "{name}"}}) and use '
                f"versions[current_version].body_md."
            )
        return None

    def list_skills() -> list[dict]:
        """List installed skills available to this agent. Returns
        [{name, description, version}, ...].

        Use this if you've forgotten what skills you have access to. The
        same information was provided in your system prompt at startup
        but enumerating it directly is sometimes useful in long sessions.
        """
        out = []
        for name in reg.names():
            if allow_set is not None and name not in allow_set:
                continue
            fm = reg.skills[name].frontmatter
            out.append({
                "name": fm.name,
                "description": fm.description,
                "version": fm.version,
            })
        return out

    def read_skill(name: str, tool_context=None) -> str:
        """Load the full body of a skill into your context. Call this when
        a user request matches a skill's description (you saw the
        descriptions in your system prompt at startup, or via list_skills).

        The body contains the operating procedure for the skill — frameworks,
        rubrics, checklists, examples. Follow it for the rest of this turn.
        If it references additional files under references/, call
        read_skill_reference(name, path) for those.
        """
        denied = _denied(name)
        if denied:
            return denied
        body = reg.read_body(name)
        _emit_usage(agent_name, name, tier=2,
                    tokens_estimated=len(body) // 4)  # rough estimate
        _stamp_session_load(tool_context, name)
        return body

    def read_skill_reference(name: str, reference_path: str,
                              tool_context=None) -> str:
        """Load a specific reference file under a skill's directory.

        Use when the skill body told you to read e.g.
        `references/copy-frameworks.md` — call this with
        name="copywriting", reference_path="references/copy-frameworks.md".

        Path must be relative to the skill directory; absolute paths and
        parent traversal (..) are rejected.
        """
        denied = _denied(name)
        if denied:
            return denied
        body = reg.read_reference(name, reference_path)
        _emit_usage(agent_name, name, tier=3,
                    reference_path=reference_path,
                    tokens_estimated=len(body) // 4)
        _stamp_session_load(tool_context, name)
        return body

    return [
        FunctionTool(func=list_skills),
        FunctionTool(func=read_skill),
        FunctionTool(func=read_skill_reference),
    ]


# ---------------------------------------------------------------------------
# Helpers for building an agent's instruction string
# ---------------------------------------------------------------------------

def with_skills(instruction: str, *,
                allowed: list[str] | None = None,
                required: list[str] | None = None) -> str:
    """Append the Tier 1 skill metadata block to an instruction string,
    and optionally prepend a REQUIRED-INVOCATIONS block.

    `required` is the list of skill names the agent MUST invoke via
    ``read_skill(name)`` before producing its first output. Gemini reads
    "REQUIRED" instructions reliably; without this lever, agents
    happily draft from Tier 1 description alone and ``skill_usage``
    stays empty.

    Use at LlmAgent construction:
        content_agent = LlmAgent(
            instruction=with_skills(
                CONTENT_INSTRUCTIONS,
                allowed=allowed_for("content_agent"),
                required=required_for("content_agent"),
            ),
            tools=[..., *make_skill_tools(agent_name="content_agent", allowed=[...])],
            ...
        )
    """
    block = registry().metadata_block(allowed=allowed)
    parts = []

    # Required-invocation preamble — prepended so the LLM sees it before
    # any task-specific instructions. CRITICAL: the block must frame the
    # calls as PREAMBLE to the agent's normal task, not as the task
    # itself — otherwise Gemini stops after the tool calls and never
    # produces the synthesized output the rest of the pipeline expects.
    if required:
        lines = [
            "",
            "## REQUIRED preamble — load these BEFORE doing your task",
            "",
            "These are PREAMBLE STEPS, not your task. Your actual task is",
            "described below. The preamble loads procedures and schemas",
            "that your task output must follow:",
            "",
        ]
        for i, name in enumerate(required, start=1):
            lines.append(f"  {i}. read_skill(\"{name}\")")
        lines.append("")
        lines.append(
            "After loading, CONTINUE TO YOUR TASK and produce its full "
            "output (research findings JSON, draft, review JSON, memo, "
            "etc. — whatever your task section below specifies). The "
            "preamble loads CONSTRAIN your output; they do NOT replace "
            "it. Outputs WITHOUT the preamble calls are rejected; outputs "
            "that contain ONLY the preamble calls without the task output "
            "are ALSO rejected — both are required."
        )
        lines.append("")
        parts.append("\n".join(lines))

    parts.append(instruction.rstrip())
    if block:
        parts.append(block)

    return "\n\n".join(parts)
