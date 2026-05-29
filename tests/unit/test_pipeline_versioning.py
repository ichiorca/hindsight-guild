"""A1: the drafting pipeline must stamp the skill's real version and, when a
candidate rollout is enabled, render + stamp a renderable candidate so the
promotion gate accrues candidate-vs-incumbent telemetry."""
from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("LOCAL_DEV", "1")

from agents import pipeline


def _doc(**kw):
    base = {"_id": "linkedin_post", "current_version": "linkedin_post_v3.txt"}
    base.update(kw)
    return base


def test_incumbent_is_stamped_with_no_injected_body():
    """Default (rollout off): version == current_version, body stays empty so
    the live drafting prompt is unchanged."""
    with patch("shared.mongo_tools.find_one", return_value=_doc()):
        with patch.dict(os.environ, {"SKILL_CANDIDATE_ROLLOUT_PCT": "0"}):
            version, body = pipeline._resolve_skill_version_and_body(
                "linkedin_post", "act_abc")
    assert version == "linkedin_post_v3.txt"
    assert body == ""


def test_disk_candidate_is_rendered_when_rollout_full():
    """rollout=1.0 + a candidate whose prompt file exists on disk → that
    candidate version is selected AND its file body returned for rendering."""
    # linkedin_post_v3.txt is a real prompt file; use it as the 'candidate'
    # so the disk-render path resolves to a non-empty body.
    doc = _doc(current_version="linkedin_post_vX.txt",
               candidates=["linkedin_post_v3.txt"])
    with patch("shared.mongo_tools.find_one", return_value=doc):
        with patch.dict(os.environ, {"SKILL_CANDIDATE_ROLLOUT_PCT": "1"}):
            version, body = pipeline._resolve_skill_version_and_body(
                "linkedin_post", "act_xyz")
    assert version == "linkedin_post_v3.txt"
    assert len(body) > 50  # the actual prompt file content


def test_mongo_body_candidate_is_rendered():
    """A candidate whose body lives in Mongo versions[].body_md renders too
    (the agent_skill storage shape)."""
    doc = _doc(
        current_version="v1",
        candidates=["v2"],
        versions={"v1": {"body_md": "old"}, "v2": {"body_md": "NEW CANDIDATE BODY"}},
    )
    with patch("shared.mongo_tools.find_one", return_value=doc):
        with patch.dict(os.environ, {"SKILL_CANDIDATE_ROLLOUT_PCT": "1"}):
            version, body = pipeline._resolve_skill_version_and_body("x", "act_1")
    assert version == "v2"
    assert body == "NEW CANDIDATE BODY"


def test_non_renderable_candidate_stays_on_incumbent():
    """rollout on, but the candidate has neither a Mongo body nor a disk file
    → never select it (we must not mislabel incumbent text as the candidate)."""
    doc = _doc(candidates=["linkedin_post_v999_missing.txt"])
    with patch("shared.mongo_tools.find_one", return_value=doc):
        with patch.dict(os.environ, {"SKILL_CANDIDATE_ROLLOUT_PCT": "1"}):
            version, body = pipeline._resolve_skill_version_and_body("x", "act_1")
    assert version == "linkedin_post_v3.txt"
    assert body == ""


def test_unreadable_skill_doc_falls_through():
    with patch("shared.mongo_tools.find_one", return_value=None):
        version, body = pipeline._resolve_skill_version_and_body("x", "act_1")
    assert version is None
    assert body == ""
