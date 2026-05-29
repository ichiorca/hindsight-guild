# Agent Skills — Claude Code-compatible, ADK-native

This directory implements the [Agent Skills Open Standard](https://github.com/anthropics/skills)
(Anthropic, Dec 2025) using the same filesystem layout Claude Code,
Gemini CLI, and Codex CLI consume. The SKILL.md files in this directory
work unchanged in any of those runtimes — they're plain text following
the open spec.

## Layout

```
skills/
├── README.md                       # this file
├── house-style/                    # our native skill, ships with the system
│   └── SKILL.md
├── copywriting/                    # external skills imported with MIT attribution
│   ├── SKILL.md
│   └── references/
│       └── copy-frameworks.md
├── ab-testing/
│   └── SKILL.md
├── cro/
│   └── SKILL.md
├── customer-research/
│   └── SKILL.md
├── ads-meta/
│   └── SKILL.md
├── ads-creative/
│   └── SKILL.md
└── ads-attribution/
    └── SKILL.md
```

## How loading works

Three-tier progressive disclosure, identical to Claude Code:

| Tier | What | When |
|---|---|---|
| **1 — Discovery** | YAML frontmatter (name + description) | Loaded into every agent's system prompt at construction (~80 tokens/skill) |
| **2 — Activation** | Full SKILL.md body | Loaded into context when the model calls `read_skill(name)` |
| **3 — Deep dive** | References under `references/*.md` | Loaded when SKILL.md body asks the model to read them |

Skills are never embedded or vectorized. They're plain markdown loaded
into the LLM's context exactly when relevant. See `shared/skills.py` for
the loader.

## Per-agent allowlists

Each of our 12 agents has a curated list of skills it can invoke. The
allowlist is enforced at the FunctionTool level — an agent that calls
`read_skill("a-skill-not-on-its-list")` gets a `PermissionError`. This
keeps the Tier 1 metadata block lean per agent (we don't surface every
skill to every agent) and gives us a hard governance boundary.

See `agents/_skills_config.py` for the allowlists.

## Adding a skill

1. Create `skills/<name>/SKILL.md` with valid YAML frontmatter:

   ```markdown
   ---
   name: my-skill
   description: When the user wants to <X>. Use when they say <Y> or <Z>.
   metadata:
     version: 1.0.0
   ---

   # Skill body

   Procedure, frameworks, examples, ...
   ```

2. Optional: add `references/*.md` deep-dive files. The SKILL.md body
   should explicitly point the model at them ("See `references/foo.md`").

3. Add the skill name to the appropriate agent allowlists in
   `agents/_skills_config.py`.

4. Restart the agent. The new skill is discovered at startup.

## Importing skills from external repos

The MIT-licensed skill repos at
[marketingskills](https://github.com/iannuttall/marketingskills) and
[claude-ads](https://github.com/AgriciDaniel/claude-ads) are
spec-compatible. To import a subset:

```bash
./scripts/import-skills.sh \
  --from <path-or-url> \
  --skills copywriting,ab-testing,cro,ads-meta
```

The script copies the chosen skill directories into `skills/`, preserves
the MIT attribution headers, and runs the registry's frontmatter
validation so you fail-fast on malformed imports.

## Telemetry

Every Tier 2/3 skill load emits a `state.skill_usage` document so you
can answer:

- Which skills get used most? `db.skill_usage.aggregate([{$group: {_id: "$skill_name", n: {$sum: 1}}}])`
- Which agents reach for which skills?
- What's the loaded-token cost of skills over the last week?

This is observability Claude Code doesn't have natively — a side benefit
of building it ourselves.

## Why this layout (and not Mongo)

Filesystem-resident skills:
- Work unchanged in Claude Code, Gemini CLI, Codex CLI, and 30+ other
  tools that consume the open spec
- Are reviewable in git history alongside the agent prompts
- Have zero retrieval latency at Tier 2/3 (local file read)
- Are easy to author (just markdown)

Trade-off: skill updates require redeploy of the agent container. This
matches Claude Code's behavior (which loads skills at startup) and is
acceptable because skills change much less frequently than the data
they operate on.
