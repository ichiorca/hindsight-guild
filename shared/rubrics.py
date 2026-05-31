"""Rubric harness using Vertex AI Gen AI Evaluation Service.

All six rubrics are real PointwiseMetric instances. Same module exposes:
  - `score_draft(text, channel, category)` — synchronous single-draft eval used
    by the agent after-callback at draft time.
  - `evaluate_batch(rows)` — batch eval used by services/eval_harness.
  - GROUNDED_RUBRIC family — rubrics that pull recent negatives from MongoDB
    to anchor the judge prompt.

Reference: vertexai.evaluation.{EvalTask, PointwiseMetric, PointwiseMetricPromptTemplate}.
See https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/eval-python-sdk/determine-eval

Why this over the hand-rolled Gemini-as-judge in the lean spec: Vertex AI Eval
Service gives us (1) consistent scoring with retries and structured output, (2)
batch evaluation for nightly re-grading, (3) pairwise mode for candidate-vs-
incumbent skill comparisons in the promotion gate, (4) judge-model drift
tracking via the configure-judge-model surface.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import pandas as pd
import vertexai
from vertexai.evaluation import (
    EvalTask,
    PointwiseMetric,
    PointwiseMetricPromptTemplate,
)

from shared import mongo_tools

log = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "hindsight-guild-mvp")
LOCATION = os.environ.get("REGION", "us-central1")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "gemini-3.1-flash-lite")

vertexai.init(project=PROJECT_ID, location=LOCATION)


# ---------------------------------------------------------------------------
# Rubric definitions — each becomes a PointwiseMetric
# ---------------------------------------------------------------------------

RATING_RUBRIC_5PT = {
    "5": "Indistinguishable from our best approved pieces.",
    "4": "Strong match; minor polish needed.",
    "3": "On-brand but not exemplary.",
    "2": "Noticeably off in tone, voice, or word choice.",
    "1": "Off-brand or jarring.",
}

RATING_RUBRIC_CLAIM = {
    "5": "Every claim has explicit grounding in approved evidence.",
    "4": "Strong grounding; one minor unsupported phrase.",
    "3": "Mostly grounded; a few directional claims.",
    "2": "Several unsupported claims.",
    "1": "Pervasive unsupported claims.",
}


@dataclass
class RubricDef:
    name: str
    criteria: dict[str, str]      # criterion_id -> description
    rating_rubric: dict[str, str]
    input_variables: list[str]    # what the prompt template expects
    needs_grounding: bool = False  # if True, pull negatives from Mongo
    rejection_category: str | None = None  # which negatives to pull
    judge_model: str = JUDGE_MODEL


BRAND_VOICE = RubricDef(
    name="brand_voice",
    criteria={
        "concise": "Concise, no jargon stack, no fluff.",
        "evidence_led": "Specific evidence rather than abstract claims.",
        "confident_not_overclaiming": "Confident but avoids absolutes (eliminates, 100%, guaranteed).",
    },
    rating_rubric=RATING_RUBRIC_5PT,
    input_variables=["candidate", "negative_examples"],
    needs_grounding=True,
    rejection_category="tone",
)

CLAIM_SUPPORT = RubricDef(
    name="claim_support",
    criteria={
        "grounding": "Every factual claim must be tied to evidence we can cite.",
        "absence_of_overclaim": "No absolute or unverifiable claims.",
    },
    rating_rubric=RATING_RUBRIC_CLAIM,
    input_variables=["candidate", "negative_examples"],
    needs_grounding=True,
    rejection_category="claim_risk",
)

CLAIM_RISK = RubricDef(
    name="claim_risk",
    criteria={
        "legal_safety": "Avoids legally fraught claims (guarantees, ranking #1, only X).",
        "category_appropriateness": "Stays inside what our category and proof support.",
    },
    rating_rubric={
        "5": "Zero risky claims; directional language with evidence.",
        "4": "One mild absolute that could be softened.",
        "3": "A few directional claims that need evidence.",
        "2": "Multiple absolute or risky claims.",
        "1": "Pervasive overclaim or legally risky.",
    },
    input_variables=["candidate", "negative_examples"],
    needs_grounding=True,
    rejection_category="claim_risk",
)

ICP_RELEVANCE = RubricDef(
    name="icp_relevance",
    criteria={
        "persona_signals": "Speaks to the specific role's pain points and vocabulary.",
        "non_generic": "Could only have been written for this ICP.",
    },
    rating_rubric={
        "5": "Could only have been written for this ICP.",
        "4": "Clearly targeted; one place feels generic.",
        "3": "Right industry but generic.",
        "2": "Mismatched persona signals.",
        "1": "Wrong ICP entirely.",
    },
    input_variables=["candidate", "icp_description", "negative_examples"],
    needs_grounding=True,
    rejection_category="icp_relevance",
)

ORIGINALITY = RubricDef(
    name="originality",
    criteria={
        "distinct_voice": "Distinct from competitor messaging in our category.",
        "non_recycled": "Does not echo our prior posts or generic listicle patterns.",
    },
    rating_rubric={
        "5": "Distinctive angle and phrasing.",
        "4": "Mostly original; one familiar formulation.",
        "3": "Decent but recycles standard category language.",
        "2": "Reads like competitor copy.",
        "1": "Indistinguishable from category boilerplate.",
    },
    input_variables=["candidate", "negative_examples"],
    needs_grounding=True,
    rejection_category="originality",
)

CONVERSION_INTENT = RubricDef(
    name="conversion_intent",
    criteria={
        "soft_cta": "Single soft CTA appropriate to channel and funnel stage.",
        "non_hard_sell": "Not a hard sell or multi-CTA forest.",
    },
    rating_rubric={
        "5": "Soft, channel-appropriate CTA that invites engagement.",
        "4": "Clear CTA, slightly weak hook.",
        "3": "CTA present but generic.",
        "2": "Hard sell or unclear next action.",
        "1": "No CTA or off-channel CTA.",
    },
    input_variables=["candidate", "channel", "negative_examples"],
    needs_grounding=True,
    rejection_category="conversion_intent",
)

ALL_RUBRICS: list[RubricDef] = [
    BRAND_VOICE, CLAIM_SUPPORT, CLAIM_RISK,
    ICP_RELEVANCE, ORIGINALITY, CONVERSION_INTENT,
]


# ---------------------------------------------------------------------------
# Build PointwiseMetric instances (cached)
# ---------------------------------------------------------------------------

_metric_cache: dict[str, PointwiseMetric] = {}


def _build_metric(r: RubricDef) -> PointwiseMetric:
    if r.name in _metric_cache:
        return _metric_cache[r.name]

    template = PointwiseMetricPromptTemplate(
        criteria=r.criteria,
        rating_rubric=r.rating_rubric,
        input_variables=r.input_variables,
    )
    # Vertex AI Eval SDK renamed the first kwarg from `name` to `metric`
    # in a recent release. Pass via the new name; runtime still surfaces
    # the value as the metric's identifier on the result rows.
    metric = PointwiseMetric(
        metric=r.name,
        metric_prompt_template=template,
    )
    _metric_cache[r.name] = metric
    return metric


# ---------------------------------------------------------------------------
# Grounding: pull recent negatives from MongoDB for a (channel, category)
# ---------------------------------------------------------------------------

def _recent_negatives(channel: str, category: str, limit: int = 3) -> str:
    # The whole body is wrapped so a grounding hiccup can NEVER fail the
    # draft's eval scoring — score_draft calls this inline and an exception
    # here would null out every rubric for the draft.
    try:
        negs = mongo_tools.find_sorted(
            "negative_examples",
            {"channel": channel, "rejection_category": category},
            sort=[("ts", -1)],
            limit=limit,
            secret_name="mongo_uri_readonly",
        )
        if not negs:
            return "(none on file)"
        # negative_examples rows are written by THREE paths with two
        # different field names for the offending text:
        #   - edit_capture_handler (cloud)        → draft_text
        #   - queue reject + self_critique miner  → rejected_phrase
        # Read whichever is present; skip rows that carry neither rather
        # than KeyError (which previously aborted all grounding + scoring).
        lines = []
        for n in negs:
            text = n.get("draft_text") or n.get("rejected_phrase") or n.get("reason")
            if text:
                lines.append(f"- {text}")
        return "\n".join(lines) if lines else "(none on file)"
    except Exception as e:
        log.warning("negatives lookup failed: %s", e)
        return "(none on file)"


# ---------------------------------------------------------------------------
# Public scoring API
# ---------------------------------------------------------------------------

def score_draft(candidate: str, channel: str | None = None,
                 icp_description: str | None = None,
                 rubrics: list[RubricDef] | None = None) -> dict[str, float]:
    """Synchronous single-draft eval. Used at draft time by the agent callback.

    Returns {rubric_name: score 0..1}.
    """
    rubrics = rubrics or ALL_RUBRICS

    # Vertex's PointwiseMetricPromptTemplate always renders a "## Response\n
    # {response}" section, so EvalTask REQUIRES a `response` column even when
    # our criteria reference {candidate}. Without it every metric fails with
    # "Cannot find the `response` column ..." and the draft gets NO eval_scores
    # (which starves rubric-trend + the promotion gate). The candidate IS the
    # response under evaluation, so mirror it into `response`.
    row = {"candidate": candidate, "response": candidate}
    if channel:
        row["channel"] = channel
    if icp_description:
        row["icp_description"] = icp_description

    # Per-rubric negative grounding
    for r in rubrics:
        if r.needs_grounding and channel and r.rejection_category:
            row.setdefault(
                "negative_examples",
                _recent_negatives(channel, r.rejection_category)
            )

    # EvalTask aborts the ENTIRE evaluation (all six rubrics → no scores) if a
    # single metric references a column the dataset lacks — e.g. icp_relevance
    # needs `icp_description`, conversion_intent needs `channel`, and most need
    # `negative_examples`. Guarantee every referenced column exists with a safe
    # placeholder so a draft missing one input still gets scored on the rest.
    needed_cols = {"response"}
    for r in rubrics:
        needed_cols.update(r.input_variables)
    for col in needed_cols:
        row.setdefault(col, "(unspecified)")

    dataset = pd.DataFrame([row])

    metrics = [_build_metric(r) for r in rubrics]
    task = EvalTask(dataset=dataset, metrics=metrics)
    result = task.evaluate()

    scores: dict[str, float] = {}
    # Vertex AI Eval Service writes per-row scores into result.metrics_table
    # under columns named `<metric_name>/score` (raw 1-5 int).
    table = result.metrics_table
    for r in rubrics:
        col = f"{r.name}/score"
        if col in table.columns:
            try:
                raw = float(table.iloc[0][col])
                scores[r.name] = raw / 5.0  # normalize 1-5 → 0..1
            except (ValueError, TypeError):
                log.warning("rubric %s returned non-numeric score", r.name)
    return scores


# ---------------------------------------------------------------------------
# Quality floor — opt-in ship/hold gate over a draft's scores
# ---------------------------------------------------------------------------

# Rubrics that gate shipping. A draft must clear the floor on each of these to
# auto-pass; the others (originality, conversion_intent) are tracked but not
# blocking, mirroring the promotion-gate guardrail set.
FLOOR_RUBRICS: tuple[str, ...] = (
    "brand_voice", "claim_support", "claim_risk", "icp_relevance",
)
# Default normalized (0..1) floor. Env-overridable so prod can tune without a
# code change. A draft scoring below this on any FLOOR_RUBRIC is held.
QUALITY_FLOOR = float(os.environ.get("EVAL_QUALITY_FLOOR", "0.5"))


def passes_quality_floor(scores: dict[str, float],
                          floor: float | None = None,
                          required: tuple[str, ...] = FLOOR_RUBRICS) -> bool:
    """Return True if a draft's scores clear the ship/hold floor.

    Pure and side-effect free — safe to call anywhere. A rubric that wasn't
    scored (sampling skipped it, or the judge returned non-numeric) is treated
    as *not failing*: absence of a score is not evidence of a bad draft, and we
    never want the floor to silently hold every unsampled draft. Only a present
    score below the floor holds the draft.
    """
    bar = QUALITY_FLOOR if floor is None else floor
    for name in required:
        val = scores.get(name)
        if val is not None and val < bar:
            return False
    return True


def evaluate_batch(rows: list[dict],
                    rubrics: list[RubricDef] | None = None) -> pd.DataFrame:
    """Batch evaluation used by services/eval_harness.

    Each row should contain at least `candidate` and optionally `channel`,
    `icp_description`. Grounding negatives are pulled per-row.

    Returns the full metrics_table from EvalTask, with `<rubric>/score` and
    `<rubric>/explanation` columns per rubric.
    """
    rubrics = rubrics or ALL_RUBRICS

    enriched: list[dict] = []
    for row in rows:
        out = dict(row)
        ch = row.get("channel")
        for r in rubrics:
            if r.needs_grounding and ch and r.rejection_category:
                out.setdefault(
                    "negative_examples",
                    _recent_negatives(ch, r.rejection_category)
                )
        enriched.append(out)

    dataset = pd.DataFrame(enriched)
    metrics = [_build_metric(r) for r in rubrics]
    task = EvalTask(dataset=dataset, metrics=metrics)
    return task.evaluate().metrics_table
