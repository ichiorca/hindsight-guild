"""Unit tests for shared/skills.py — the Agent Skills loader.

Covers the three-tier progressive disclosure mechanism, the
filesystem layout convention, frontmatter parsing, per-agent allowlist
enforcement, and path-traversal safety.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("PROJECT_ID", "test-project")

from shared import skills as skills_mod

# ---------------------------------------------------------------------------
# Fixtures — write fake skill directories to a tmp path
# ---------------------------------------------------------------------------

VALID_SKILL_MD = """---
name: copywriting
description: When the user wants to write or improve marketing copy.
metadata:
  version: 2.0.0
---

# Copywriting

Body of the skill. Frameworks, examples, etc.

See `references/copy-frameworks.md` for headline formulas.
"""

VALID_SKILL_AD_FORMAT = """---
name: ads-meta
description: "Meta Ads audit for the Andromeda era."
version: 1.5.0
user-invokable: false
tested_date: 2026-05-17
---

# Meta Ads body
"""

MISSING_DESCRIPTION = """---
name: broken-skill
metadata:
  version: 1.0.0
---
"""

NAME_DIR_MISMATCH = """---
name: actually-different-name
description: Description.
---
"""


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    """Build a temp skills/ directory with three valid skills + one broken
    + one with a reference file."""
    root = tmp_path / "skills"

    # Valid: copywriting (with references)
    (root / "copywriting" / "references").mkdir(parents=True)
    (root / "copywriting" / "SKILL.md").write_text(VALID_SKILL_MD)
    (root / "copywriting" / "references" / "copy-frameworks.md").write_text(
        "# Frameworks\n\n12 headline patterns here."
    )

    # Valid: ads-meta (claude-ads frontmatter style — version at top level)
    (root / "ads-meta").mkdir()
    (root / "ads-meta" / "SKILL.md").write_text(VALID_SKILL_AD_FORMAT)

    # Broken: no description
    (root / "broken-skill").mkdir()
    (root / "broken-skill" / "SKILL.md").write_text(MISSING_DESCRIPTION)

    # Broken: dir name doesn't match frontmatter name
    (root / "mismatch-dir").mkdir()
    (root / "mismatch-dir" / "SKILL.md").write_text(NAME_DIR_MISMATCH)

    return root


@pytest.fixture
def registry(skills_root: Path) -> skills_mod.SkillRegistry:
    return skills_mod.SkillRegistry(skills_root)


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def test_parse_skill_md_valid():
    fm, body = skills_mod._parse_skill_md(VALID_SKILL_MD)
    assert fm["name"] == "copywriting"
    assert fm["description"].startswith("When the user wants")
    assert fm["metadata"]["version"] == "2.0.0"
    assert "Copywriting" in body
    assert "frontmatter" not in body.lower()  # frontmatter stripped


def test_parse_skill_md_no_frontmatter_raises():
    with pytest.raises(ValueError, match="must begin with"):
        skills_mod._parse_skill_md("# Just a heading\n\nNo frontmatter.")


def test_parse_skill_md_unclosed_frontmatter_raises():
    with pytest.raises(ValueError, match="closing"):
        skills_mod._parse_skill_md("---\nname: x\ndescription: y\n# Body")


def test_frontmatter_supports_both_version_styles():
    # marketingskills style: metadata.version
    fm_ms = skills_mod._frontmatter_from_dict({
        "name": "x", "description": "y", "metadata": {"version": "3.0.0"},
    })
    assert fm_ms.version == "3.0.0"

    # claude-ads style: version at top level
    fm_ca = skills_mod._frontmatter_from_dict({
        "name": "x", "description": "y", "version": "1.5.0",
    })
    assert fm_ca.version == "1.5.0"

    # default if neither
    fm_default = skills_mod._frontmatter_from_dict({
        "name": "x", "description": "y",
    })
    assert fm_default.version == "1.0.0"


def test_frontmatter_rejects_invalid_skill_name():
    with pytest.raises(ValueError, match="not a valid slug"):
        skills_mod._frontmatter_from_dict({
            "name": "Has Spaces", "description": "y",
        })
    with pytest.raises(ValueError, match="not a valid slug"):
        skills_mod._frontmatter_from_dict({
            "name": "../escape", "description": "y",
        })


# ---------------------------------------------------------------------------
# Registry — scanning
# ---------------------------------------------------------------------------

def test_registry_skips_broken_skills(registry: skills_mod.SkillRegistry):
    names = registry.names()
    assert "copywriting" in names
    assert "ads-meta" in names
    assert "broken-skill" not in names
    assert "mismatch-dir" not in names


def test_registry_records_scan_errors(registry: skills_mod.SkillRegistry):
    error_skills = {name for name, _ in registry._scan_errors}
    assert "broken-skill" in error_skills
    assert "mismatch-dir" in error_skills


# ---------------------------------------------------------------------------
# Tier 1 — metadata block injection
# ---------------------------------------------------------------------------

def test_metadata_block_lists_all_skills(registry: skills_mod.SkillRegistry):
    block = registry.metadata_block()
    assert "Available Skills" in block
    assert "copywriting" in block
    assert "ads-meta" in block
    assert "broken-skill" not in block


def test_metadata_block_respects_allowlist(registry: skills_mod.SkillRegistry):
    block = registry.metadata_block(allowed=["copywriting"])
    assert "copywriting" in block
    assert "ads-meta" not in block


def test_metadata_block_empty_when_no_match(registry: skills_mod.SkillRegistry):
    block = registry.metadata_block(allowed=["nonexistent"])
    assert block == ""


def test_metadata_block_instructs_progressive_disclosure(registry: skills_mod.SkillRegistry):
    block = registry.metadata_block()
    # The block must tell the model: full body NOT loaded; call read_skill(name)
    assert "not in your context" in block.lower()
    assert "read_skill" in block


# ---------------------------------------------------------------------------
# Tier 2 — body loading
# ---------------------------------------------------------------------------

def test_read_body_returns_body_without_frontmatter(registry: skills_mod.SkillRegistry):
    body = registry.read_body("copywriting")
    assert "Copywriting" in body
    # The body should NOT contain the frontmatter delimiters
    assert "---" not in body.split("\n")[0]


def test_read_body_unknown_skill_raises(registry: skills_mod.SkillRegistry):
    with pytest.raises(KeyError, match="not installed"):
        registry.read_body("nonexistent-skill")


# ---------------------------------------------------------------------------
# Tier 3 — reference loading + path traversal safety
# ---------------------------------------------------------------------------

def test_read_reference_returns_file_contents(registry: skills_mod.SkillRegistry):
    content = registry.read_reference("copywriting", "references/copy-frameworks.md")
    assert "Frameworks" in content
    assert "12 headline patterns" in content


def test_read_reference_rejects_absolute_paths(registry: skills_mod.SkillRegistry):
    # Two valid error messages: "must be relative and contained within..."
    # (early absolute-path guard) or "escapes skill directory" (post-resolve
    # containment check). Either is a security-correct rejection.
    with pytest.raises(ValueError, match="contained within|escapes skill directory"):
        registry.read_reference("copywriting", "/etc/passwd")


def test_read_reference_rejects_parent_traversal(registry: skills_mod.SkillRegistry):
    with pytest.raises(ValueError, match="contained within|escapes skill directory"):
        registry.read_reference("copywriting", "../ads-meta/SKILL.md")


def test_read_reference_missing_file_raises(registry: skills_mod.SkillRegistry):
    with pytest.raises(FileNotFoundError):
        registry.read_reference("copywriting", "references/does-not-exist.md")


# ---------------------------------------------------------------------------
# FunctionTools — agent-facing API + allowlist enforcement
# ---------------------------------------------------------------------------

def _with_registry(reg):
    """Patch the registry singleton + disable telemetry for tool tests."""
    return [
        patch.object(skills_mod, "registry", return_value=reg),
        patch.object(skills_mod, "_emit_usage"),
    ]


def test_make_skill_tools_returns_three_tools(registry):
    patches = _with_registry(registry)
    for p in patches:
        p.start()
    try:
        tools = skills_mod.make_skill_tools(agent_name="content_agent")
        assert len(tools) == 3
        names = {t.name for t in tools}
        assert names == {"list_skills", "read_skill", "read_skill_reference"}
    finally:
        for p in patches:
            p.stop()


def test_list_skills_respects_allowlist(registry):
    patches = _with_registry(registry)
    for p in patches:
        p.start()
    try:
        tools = skills_mod.make_skill_tools(
            agent_name="content_agent", allowed=["copywriting"]
        )
        list_skills = next(t for t in tools if t.name == "list_skills")
        items = list_skills.func()
        names = [item["name"] for item in items]
        assert names == ["copywriting"]
    finally:
        for p in patches:
            p.stop()


def test_read_skill_denied_when_not_in_allowlist(registry):
    patches = _with_registry(registry)
    for p in patches:
        p.start()
    try:
        tools = skills_mod.make_skill_tools(
            agent_name="content_agent", allowed=["copywriting"]
        )
        read_skill = next(t for t in tools if t.name == "read_skill")
        # A denied read returns a recoverable error string (not a raise): an
        # exception inside an ADK FunctionTool aborts the whole agent run,
        # so the tool hands the model a message it can recover from instead.
        out = read_skill.func(name="ads-meta")
        assert "not permitted" in out
        assert "ads-meta" in out
    finally:
        for p in patches:
            p.stop()


def test_read_skill_emits_usage_telemetry(registry):
    with patch.object(skills_mod, "registry", return_value=registry):
        with patch.object(skills_mod, "_emit_usage") as m_emit:
            tools = skills_mod.make_skill_tools(agent_name="content_agent")
            read_skill = next(t for t in tools if t.name == "read_skill")
            read_skill.func(name="copywriting")
            m_emit.assert_called_once()
            call = m_emit.call_args
            # _emit_usage signature evolved to keyword-only; the test was
            # asserting on positional args. Accept either shape so the test
            # tracks the current API.
            tier = call.kwargs.get("tier")
            if tier is None and len(call.args) >= 3:
                tier = call.args[2]
            assert tier == 2, f"expected tier=2 for body load, got {tier!r}"


def test_read_skill_reference_emits_tier_3_telemetry(registry):
    with patch.object(skills_mod, "registry", return_value=registry):
        with patch.object(skills_mod, "_emit_usage") as m_emit:
            tools = skills_mod.make_skill_tools(agent_name="content_agent")
            read_ref = next(t for t in tools if t.name == "read_skill_reference")
            read_ref.func(name="copywriting",
                          reference_path="references/copy-frameworks.md")
            m_emit.assert_called_once()
            # Tier 3 should be the third positional arg or kwarg
            call = m_emit.call_args
            tier_arg = call.kwargs.get("tier", call.args[2] if len(call.args) > 2 else None)
            assert tier_arg == 3


# ---------------------------------------------------------------------------
# with_skills — instruction string injection
# ---------------------------------------------------------------------------

def test_with_skills_appends_metadata_block(registry):
    with patch.object(skills_mod, "registry", return_value=registry):
        out = skills_mod.with_skills(
            "You are a marketing agent.",
            allowed=["copywriting"],
        )
        assert out.startswith("You are a marketing agent.")
        assert "Available Skills" in out
        assert "copywriting" in out


def test_with_skills_returns_original_when_no_match(registry):
    with patch.object(skills_mod, "registry", return_value=registry):
        original = "You are a marketing agent."
        out = skills_mod.with_skills(original, allowed=["nonexistent"])
        assert out == original
