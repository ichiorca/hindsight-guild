"""Mongo fallbacks for BigQuery telemetry reads — LOCAL_DEV only.

BigQuery is the system of record for ``telemetry.actions`` in every deployed
(gcloud) environment, and it stays the primary store there: nothing in this
module runs when a BigQuery client exists. These helpers exist solely for
LOCAL_DEV, where there is no BigQuery (``bigquery_client()`` returns ``None``)
and ``shared.telemetry.emit_action``'s dual-write has already mirrored every
action into the Mongo ``actions`` collection with an identical row shape
(``eval_scores`` as a nested object instead of a JSON string).

Each function reproduces one specific BigQuery aggregation as a Mongo
``$group`` so the self-learning loop (promotion gate, track-record rollups,
self-critique) executes its real production code path locally instead of a
test stub. Callers gate on ``bigquery_client() is None`` — when BigQuery is
available, telemetry is read from BigQuery and these are never touched.

Faithfulness notes:
  - BigQuery ``STDDEV`` is sample stddev → Mongo ``$stdDevSamp`` (so the
    promotion gate's significance z-test is identical local vs prod).
  - BigQuery ``AVG``/``STDDEV`` ignore NULLs; Mongo ``$avg``/``$stdDevSamp``
    ignore missing/non-numeric values the same way (a rubric absent on a
    version → ``None`` on both sides, not 0).
  - Windows match the SQL: 30d for version stats, 14d for per-channel stats,
    configurable (0 = all-time) for the track-record rollups.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from shared import mongo_tools

# The six rubric sub-scores carried on every drafting action's eval_scores.
# Single source of truth so every fallback aggregates the same set the BQ
# SELECTs do.
RUBRICS = (
    "brand_voice", "claim_support", "claim_risk",
    "icp_relevance", "originality", "conversion_intent",
)


def _since(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _db(db):
    # pymongo Database has no truthiness — explicit None check.
    return db if db is not None else mongo_tools.db()


def _draft_text(action: dict) -> str:
    """Pull a draft body from an action row, matching how the PRD-03 miners
    extract it. Real telemetry stores the draft under ``raw.draft`` /
    ``raw.output_text`` (``raw`` is a dict); tolerate a bare-string ``raw`` or
    ``draft`` field for hand-seeded rows."""
    raw = action.get("raw")
    if isinstance(raw, dict):
        body = raw.get("draft") or raw.get("output_text") or ""
        if isinstance(body, dict):
            body = body.get("body_markdown") or body.get("body") or ""
        if isinstance(body, str) and body:
            return body
    if isinstance(raw, str) and raw:
        return raw
    d = action.get("draft")
    return d if isinstance(d, str) else ""


def version_stats(skill_id: str, versions: list[str], *,
                  days: int = 30, db=None) -> dict[str, dict]:
    """Mongo equivalent of ``promotion_gate._version_stats``.

    Per ``skill_version`` rubric means + sample stddev (``sd_<metric>``) +
    ``n`` over the last ``days``, for ``skill_id``. Mirrors the BQ SELECT
    exactly so the gate's MDE + significance checks behave identically.
    """
    db = _db(db)
    group: dict = {"_id": "$skill_version", "n": {"$sum": 1}}
    for r in RUBRICS:
        group[r] = {"$avg": f"$eval_scores.{r}"}
        group[f"sd_{r}"] = {"$stdDevSamp": f"$eval_scores.{r}"}
    rows = db["actions"].aggregate([
        {"$match": {
            "skill_id": skill_id,
            "skill_version": {"$in": list(versions)},
            "ts": {"$gte": _since(days)},
            "eval_scores": {"$ne": None},
        }},
        {"$group": group},
    ])
    out: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        ver = d.pop("_id")
        out[ver] = {**d, "n": d.get("n", 0)}
    return out


def agent_skill_per_channel_stats(skill_id: str, channels: list[str], *,
                                  days: int = 14, db=None) -> dict[str, dict]:
    """Mongo equivalent of ``promotion_gate._agent_skill_per_channel_stats``.

    Per-channel rubric means for actions that loaded this Agent Skill
    (``skills_loaded`` array contains ``skill_id``) in the last ``days``.
    The array-containment match is the Mongo equivalent of BQ's
    ``UNNEST(skills_loaded) AS sk WHERE sk = @sk``.
    """
    channels = list(channels)
    if not channels:
        return {}
    db = _db(db)
    group: dict = {"_id": "$channel", "n": {"$sum": 1}}
    for r in RUBRICS:
        group[r] = {"$avg": f"$eval_scores.{r}"}
    rows = db["actions"].aggregate([
        {"$match": {
            "skills_loaded": skill_id,
            "channel": {"$in": channels},
            "ts": {"$gte": _since(days)},
            "eval_scores": {"$ne": None},
        }},
        {"$group": group},
    ])
    return {r["_id"]: {k: v for k, v in dict(r).items() if k != "_id"}
            for r in rows}


def skill_track_records_rollup(*, window_days: int = 0, db=None) -> list[dict]:
    """Mongo equivalent of ``derive_track_records``'s per-(skill_id,
    skill_version) rollup. ``window_days=0`` → all-time (the default the
    /skills lifetime view uses). Returns dicts keyed like the BQ row aliases.
    """
    db = _db(db)
    match: dict = {"eval_scores": {"$ne": None}}
    if window_days > 0:
        match["ts"] = {"$gte": _since(window_days)}
    group: dict = {
        "_id": {"skill_id": "$skill_id", "skill_version": "$skill_version"},
        "action_count": {"$sum": 1},
        "first_seen": {"$min": "$ts"},
        "last_seen": {"$max": "$ts"},
    }
    for r in RUBRICS:
        group[f"mean_{r}"] = {"$avg": f"$eval_scores.{r}"}
    rows = db["actions"].aggregate([{"$match": match}, {"$group": group}])
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        key = d.pop("_id")
        out.append({"skill_id": key.get("skill_id"),
                    "skill_version": key.get("skill_version"), **d})
    return out


def agent_skill_track_records_rollup(*, window_days: int = 0,
                                     db=None) -> list[dict]:
    """Mongo equivalent of ``derive_track_records``'s per-Agent-Skill rollup
    (``UNNEST(skills_loaded)``). Returns dicts keyed like the BQ row aliases,
    one per Skill, with ``channels_seen``.
    """
    db = _db(db)
    match: dict = {"eval_scores": {"$ne": None}, "skills_loaded": {"$ne": []}}
    if window_days > 0:
        match["ts"] = {"$gte": _since(window_days)}
    group: dict = {
        "_id": "$skills_loaded",
        "action_count": {"$sum": 1},
        "channels_seen": {"$addToSet": "$channel"},
        "first_seen": {"$min": "$ts"},
        "last_seen": {"$max": "$ts"},
    }
    for r in RUBRICS:
        group[f"mean_{r}"] = {"$avg": f"$eval_scores.{r}"}
    rows = db["actions"].aggregate([
        {"$match": match},
        {"$unwind": "$skills_loaded"},
        {"$match": {"skills_loaded": {"$ne": None}}},
        {"$group": group},
    ])
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        name = d.pop("_id")
        channels = [c for c in (d.pop("channels_seen", []) or []) if c]
        out.append({"skill_name": name, "channels_seen": channels, **d})
    return out


def action_experiment_for(telemetry_id: str, *, db=None):
    """Mongo equivalent of outcome_attach's ``SELECT experiment_id, variant_id
    FROM actions WHERE telemetry_id=@id``. Returns ``(experiment_id,
    variant_id)`` or ``None``."""
    db = _db(db)
    a = db["actions"].find_one(
        {"telemetry_id": telemetry_id},
        {"experiment_id": 1, "variant_id": 1})
    if not a:
        return None
    return (a.get("experiment_id"), a.get("variant_id"))


def experiment_variant_stats(experiment_id: str, success_metric: str, *,
                             db=None) -> dict[str, tuple]:
    """Mongo equivalent of outcome_attach's per-variant decision aggregation.

    Returns ``{variant_id: (mean_val, n)}``. For ``*_score`` success metrics
    it averages ``actions.eval_scores.<rubric>`` (matching the BQ score path);
    otherwise it joins ``actions`` → filled ``outcomes`` on telemetry_id and
    averages the outcome value for that slot (matching the BQ outcomes join).
    """
    db = _db(db)
    if success_metric.endswith("_score"):
        rubric = success_metric[: -len("_score")]
        rows = db["actions"].aggregate([
            {"$match": {"experiment_id": experiment_id,
                        "eval_scores": {"$ne": None}}},
            {"$group": {"_id": "$variant_id",
                        "mean_val": {"$avg": f"$eval_scores.{rubric}"},
                        "n": {"$sum": 1}}},
        ])
    else:
        rows = db["actions"].aggregate([
            {"$match": {"experiment_id": experiment_id}},
            {"$lookup": {"from": "outcomes", "localField": "telemetry_id",
                         "foreignField": "telemetry_id", "as": "o"}},
            {"$unwind": "$o"},
            {"$match": {"o.slot_name": success_metric, "o.status": "filled"}},
            {"$group": {"_id": "$variant_id",
                        "mean_val": {"$avg": "$o.value"},
                        "n": {"$sum": 1}}},
        ])
    return {r["_id"]: (r["mean_val"], r["n"]) for r in rows if r.get("_id")}


def drift_cells(rubric: str, *, threshold: float = 0.10, min_sample: int = 20,
                db=None) -> list[dict]:
    """Mongo equivalent of drift_detect._detect_drift. Per channel, compares
    the recent window (last 3 days) against the trailing baseline (4-31 days
    ago) and returns cells whose mean dropped >= ``threshold`` with >=
    ``min_sample`` recent actions. Mirrors the BQ DATE() day-window SQL.
    """
    from datetime import time as _time
    db = _db(db)
    today = datetime.now(UTC).date()
    # recent: DATE(ts) >= today-3  ·  baseline: today-31 <= DATE(ts) <= today-4
    recent_lo = datetime.combine(today - timedelta(days=3), _time.min, tzinfo=UTC)
    base_lo = datetime.combine(today - timedelta(days=31), _time.min, tzinfo=UTC)
    field = f"$eval_scores.{rubric}"
    rows = db["actions"].aggregate([
        {"$match": {"eval_scores": {"$ne": None}, "channel": {"$exists": True}}},
        {"$group": {
            "_id": "$channel",
            "recent_sum": {"$sum": {"$cond": [
                {"$gte": ["$ts", recent_lo]}, field, 0]}},
            "recent_n": {"$sum": {"$cond": [{"$gte": ["$ts", recent_lo]}, 1, 0]}},
            "baseline_sum": {"$sum": {"$cond": [
                {"$and": [{"$gte": ["$ts", base_lo]},
                          {"$lt": ["$ts", recent_lo]}]}, field, 0]}},
            "baseline_n": {"$sum": {"$cond": [
                {"$and": [{"$gte": ["$ts", base_lo]},
                          {"$lt": ["$ts", recent_lo]}]}, 1, 0]}},
        }},
    ])
    out: list[dict] = []
    for r in rows:
        if r["recent_n"] < min_sample or r["baseline_n"] == 0:
            continue
        mean_today = r["recent_sum"] / r["recent_n"]
        mean_baseline = r["baseline_sum"] / r["baseline_n"]
        drop = mean_baseline - mean_today
        if drop >= threshold:
            out.append({"channel": r["_id"], "day": str(today),
                        "mean_today": mean_today, "mean_baseline": mean_baseline,
                        "drop": drop, "n": r["recent_n"]})
    return out


def recent_skill_evidence(skill_id: str, *, lookback_days: int = 14,
                          max_rows: int = 10,
                          low_score_below: float = 0.65,
                          db=None) -> dict:
    """Recent low-scoring drafts + founder edits for a Skill, from Mongo.

    The LOCAL_DEV source for the Self-Critique Agent's evidence gathering,
    matching what it reads from BigQuery (``telemetry.actions`` low-score
    drafts) + the edit log (founder ``approvals`` with ``decision='edit'``)
    in prod. Matches on either ``skill_id`` (playbook) OR ``skills_loaded``
    containing the id (agent_skill) so it serves both branches.

    Returns ``{"low_score_drafts": [...], "founder_edits": [...]}``.
    """
    db = _db(db)
    cutoff = _since(lookback_days)

    drafts = list(db["actions"].find(
        {
            "$or": [{"skill_id": skill_id}, {"skills_loaded": skill_id}],
            "eval_scores.brand_voice": {"$lt": low_score_below},
            "ts": {"$gte": cutoff},
        },
        {"telemetry_id": 1, "channel": 1, "skill_version": 1,
         "eval_scores": 1, "_id": 0},
    ).sort("ts", -1).limit(max_rows))

    # Founder edits: approvals(decision=edit) joined to their action so we
    # can surface the BEFORE (draft) → AFTER (approved_text) the model needs
    # to cluster a correction pattern. Mirrors the voice miner's read.
    edits: list[dict] = []
    appr = list(db["approvals"].find(
        {"decision": "edit", "decided_at": {"$gte": cutoff}},
        {"telemetry_id": 1, "approved_text": 1, "edit_categories": 1, "_id": 0},
    ).sort("decided_at", -1).limit(max_rows * 3))
    tids = [a["telemetry_id"] for a in appr if a.get("telemetry_id")]
    actions_by_tid: dict[str, dict] = {}
    if tids:
        for a in db["actions"].find(
            {"telemetry_id": {"$in": tids},
             "$or": [{"skill_id": skill_id}, {"skills_loaded": skill_id}]},
            {"telemetry_id": 1, "channel": 1, "raw": 1, "draft": 1, "_id": 0},
        ):
            actions_by_tid[a["telemetry_id"]] = a
    for a in appr:
        act = actions_by_tid.get(a.get("telemetry_id"))
        if act is None:
            continue  # edit on an action that didn't load this skill
        before = _draft_text(act)
        edits.append({
            "telemetry_id": a.get("telemetry_id"),
            "channel": act.get("channel"),
            "before_text": before if isinstance(before, str) else "",
            "after_text": a.get("approved_text") or "",
            "edit_categories": a.get("edit_categories") or [],
        })
        if len(edits) >= max_rows:
            break

    return {"low_score_drafts": drafts, "founder_edits": edits}
