"""Unit tests for the pymongo-backed FunctionTool helpers.

Focus: _normalize_to_operator_update, which guards against the three update
shapes an LLM tool call can produce. The MIXED case is a regression guard —
it was the latent bug surfaced by tests/e2e/e2e_skill_evolution.py (Mongo
rejected the weekly Self-Critique agent's update with "Unknown modifier:
self_critique_proposal")."""
from __future__ import annotations

from agents._mongodb_tools import _normalize_to_operator_update as norm


def test_all_operator_update_is_unchanged():
    u = {"$set": {"a": 1}, "$push": {"xs": 2}}
    assert norm(u) == u


def test_all_bare_update_is_wrapped_in_set():
    assert norm({"current_version": "v2"}) == {"$set": {"current_version": "v2"}}


def test_mixed_update_folds_bare_keys_into_set():
    # The exact shape that crashed the Self-Critique agent: a $set block plus
    # a sibling bare key. The bare key must be folded into $set, not left to
    # trip "Unknown modifier".
    u = {
        "$set": {"versions.v2_candidate": {"body_md": "..."}},
        "self_critique_proposal": {"issue": "x", "status": "awaiting_human_review"},
    }
    out = norm(u)
    assert set(out.keys()) == {"$set"}
    assert out["$set"]["versions.v2_candidate"] == {"body_md": "..."}
    assert out["$set"]["self_critique_proposal"]["issue"] == "x"


def test_mixed_update_preserves_other_operators():
    u = {"$push": {"candidates": "v2"}, "current_version": "v2"}
    out = norm(u)
    assert out["$push"] == {"candidates": "v2"}
    assert out["$set"] == {"current_version": "v2"}


def test_empty_or_nondict_update_passthrough():
    assert norm({}) == {}
    assert norm(None) is None


# --- LLM operand-corruption repairs (observed in real Self-Critique tool
#     calls; see tests/e2e/_probe_persist.py) -------------------------------

def test_demangles_quote_wrapped_set_key():
    # Model emits the dotted path as a quoted string literal — Mongo would
    # otherwise create a literal field named '"versions.v2_critique"'.
    out = norm({"$set": {'"versions.v2_critique"': {"body_md": "x"}}})
    assert out == {"$set": {"versions.v2_critique": {"body_md": "x"}}}


def test_demangles_bracket_wrapped_set_key():
    out = norm({"$set": {'["versions.v2_critique"]': {"body_md": "x"}}})
    assert out == {"$set": {"versions.v2_critique": {"body_md": "x"}}}


def test_demangles_backtick_wrapped_set_key():
    # Markdown-style backtick wrapping, observed in a real agent tool call.
    out = norm({"$set": {"`versions.v2_critique`": {"body_md": "x"}}})
    assert out == {"$set": {"versions.v2_critique": {"body_md": "x"}}}


def test_drops_hallucinated_pseudo_unset_keys():
    # _unset / _unset_malformed are the model fumbling for $unset; they must
    # NOT be written as literal fields on the canonical doc.
    out = norm({
        "self_critique_proposal": {"issue": "x"},
        "_unset": {"scratch": 1},
        "_unset_malformed": "junk",
    })
    assert out == {"$set": {"self_critique_proposal": {"issue": "x"}}}


def test_resolves_parent_child_path_conflict():
    # Both a full-object `versions` replace AND a dotted child → Mongo would
    # abort with a path conflict. Keep the dotted child, drop the parent.
    out = norm({"$set": {
        "versions": {"v2_critique": {"body_md": "x"}},
        "versions.v2_critique": {"body_md": "x"},
        "self_critique_proposal": {"issue": "y"},
    }})
    assert "versions" not in out["$set"]
    assert out["$set"]["versions.v2_critique"] == {"body_md": "x"}
    assert out["$set"]["self_critique_proposal"] == {"issue": "y"}


def test_pure_pseudo_unset_falls_back_to_original():
    # Nothing actionable survives cleaning → return original so the caller's
    # own error/zero-match handling runs (we don't invent a write).
    u = {"_unset": {"a": 1}}
    assert norm(u) == u
