"""Unit tests for the Self-Critique agent's propose_skill_revision tool.

These exercise the input-validation guards, which all return before any Mongo
write — so no DB is needed. They lock the contract that a malformed proposal
is rejected with a recoverable {ok: False, error} the LLM can act on, rather
than persisting a bad candidate that crashes a downstream reader.
"""
from __future__ import annotations

from agents.self_critique import propose_skill_revision

_GOOD_BODY = "---\nname: x\nversion: 2.1.0\n---\n\n# Body\nsome content\n"
_CHANS = ["linkedin", "email"]


def test_rejects_empty_body():
    r = propose_skill_revision("s", "v2", "", "issue", "high", 3, _CHANS)
    assert r["ok"] is False and "body_md is empty" in r["error"]


def test_rejects_body_without_frontmatter():
    # The exact failure surfaced by the real agent: a body with no YAML
    # frontmatter would crash read_body's on-disk reconcile.
    r = propose_skill_revision("s", "v2", "no frontmatter here", "issue",
                               "high", 3, _CHANS)
    assert r["ok"] is False and "valid SKILL.md" in r["error"]


def test_rejects_bad_confidence():
    r = propose_skill_revision("s", "v2", _GOOD_BODY, "issue", "superhigh",
                               3, _CHANS)
    assert r["ok"] is False and "confidence" in r["error"]


def test_rejects_under_two_channels():
    r = propose_skill_revision("s", "v2", _GOOD_BODY, "issue", "high", 3,
                               ["linkedin"])
    assert r["ok"] is False and "channels_affected" in r["error"]


def test_rejects_missing_ids():
    r = propose_skill_revision("", "v2", _GOOD_BODY, "issue", "high", 3, _CHANS)
    assert r["ok"] is False and "required" in r["error"]
