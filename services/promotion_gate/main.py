"""Promotion gate — the formal self-learning loop closure.

For each skill that has a candidate version, this job:
  1. Pulls every action from telemetry.actions for the candidate AND the
     incumbent in the last 30 days.
  2. Computes mean rubric scores per version (brand_voice + claim_support
     as the success metric for content skills; conversion_intent for
     conversion-focused skills).
  3. If the candidate has >= MIN_ACTIONS and beats the incumbent by >= MDE
     on the success metric AND on the guardrail rubrics doesn't drop, raises
     a `promotion_request` on the skill doc with status='awaiting_approval'.
  4. The founder reviews promotion requests during the weekly learning
     review and approves (which flips current_version and pushes the old to
     history) or rejects.

This is what makes the skill library a real versioned artifact rather than a
filename pattern.

Scheduled weekly (Sunday 23:00 UTC) so the founder sees fresh proposals
Monday morning.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from google.cloud import bigquery

from agents._schema_constants import Coll, Status
from agents.self_critique_runner import load_proposals, primary_pending
from shared import mongo_tools
from shared.clients import bigquery_client

mongo_tools.use_secret("mongo_uri_writer")

PROJECT_ID = os.environ.get("PROJECT_ID", "hindsight-guild-mvp")
log = logging.getLogger(__name__)


# BQ client is lazy. Constructing it at module-import-time crashes in
# LOCAL_DEV (no ADC) and takes out unrelated paths — the UI's
# promote_after_approval call hits this module via Mongo writes only,
# never needs BQ. The proxy defers the client until a function actually
# wants it, routing through the central provider in shared.clients.
class _BQProxy:
    """Backwards-compat shim — code accesses ``BQ.query(...)``; the lazy
    central provider builds the client on first attribute access."""

    def __getattr__(self, name):
        return getattr(bigquery_client(), name)


BQ = _BQProxy()

MIN_ACTIONS = 50
MDE = 0.05  # 5pp lift on success metric
GUARDRAIL_MAX_DROP = 0.03  # candidate can't drop more than 3pp on any guardrail
# Two-sided z for the significance gate: the lift must clear MDE *and* be
# statistically distinguishable from noise. 1.96 ≈ 95% confidence.
SIGNIFICANCE_Z = 1.96

# Agent-Skill cross-channel knobs. Tuned looser than playbook MDE because
# we have no A/B data on Skills — we're gating on whether the proposed
# change actually addresses a multi-channel pattern, not on lift.
MIN_CHANNELS_AFFECTED = 2          # below this, it's not a Skill problem,
                                    # it's a channel-specific playbook issue
MIN_ACTIONS_PER_CHANNEL = 15        # each affected channel must have enough
                                    # recent volume to detect post-promotion
                                    # regression
ISSUE_RUBRIC = "brand_voice"        # the rubric the issue is expressed in
ISSUE_DIP_THRESHOLD = 0.05          # how much below project baseline counts
                                    # as "the issue actually shows up here"

SUCCESS_METRIC = "brand_voice"
GUARDRAIL_METRICS = ["claim_support", "claim_risk", "icp_relevance"]
# Skills whose success is conversion, not voice. Gated on conversion_intent.
# A skill doc may also set ``success_metric`` explicitly to override.
_CONVERSION_SKILL_PREFIXES = ("paid_variant", "nurture_email", "cro")


def _success_metric_for(skill: dict) -> str:
    """The rubric that defines success for this skill. Honors an explicit
    ``success_metric`` field; otherwise conversion-focused skills gate on
    conversion_intent and everyone else on brand_voice. (Previously this was
    hardcoded to brand_voice for every skill — wrong for conversion skills.)"""
    explicit = skill.get("success_metric")
    if explicit in (
        "brand_voice", "claim_support", "claim_risk",
        "icp_relevance", "originality", "conversion_intent",
    ):
        return explicit
    sid = str(skill.get("_id", ""))
    if sid.startswith(_CONVERSION_SKILL_PREFIXES):
        return "conversion_intent"
    return SUCCESS_METRIC


def main():
    db = mongo_tools.db()

    # Playbooks: A/B-style evaluation (existing path).
    playbooks = list(db[Coll.SKILLS].find({
        "skill_kind": {"$ne": "agent_skill"},
        "candidates": {"$exists": True, "$ne": []},
    }))
    for skill in playbooks:
        incumbent = skill["current_version"]
        for candidate in skill.get("candidates", []):
            _evaluate_candidate(skill, incumbent, candidate)

    # Agent Skills: cross-channel proposal evaluation (new path).
    # The Self-Critique agent writes a candidate body + proposal; the gate
    # decides whether to escalate it into a promotion_request the founder
    # reviews. The check is "is this actually cross-channel?" rather than
    # "did the candidate beat incumbent" because we can't A/B a Skill without
    # rebuilding the read_skill loader.
    #
    # We gate proposals that are still PENDING (no founder action yet) AND
    # those the founder explicitly ACCEPTED — accepting moves the entry into
    # the self_critique_proposals array and clears the pending singleton
    # mirror, so a singleton-only query would miss accepted ones. The
    # ``promotion_request`` guard stops us re-gating an already-escalated
    # proposal each tick.
    gateable = [Status.AWAITING_HUMAN_REVIEW, Status.ACCEPTED]
    agent_skills = list(db[Coll.SKILLS].find({
        "skill_kind": "agent_skill",
        "promotion_request": {"$exists": False},
        "$or": [
            {"self_critique_proposal.status": {"$in": gateable}},
            {"self_critique_proposals": {"$elemMatch": {"status": {"$in": gateable}}}},
        ],
    }))
    for skill in agent_skills:
        _evaluate_agent_skill_proposal(skill)


def _evaluate_candidate(skill: dict, incumbent: str, candidate: str) -> None:
    """Pull telemetry for both versions, decide whether to raise a promotion request."""
    skill_id = skill["_id"]
    success_metric = _success_metric_for(skill)
    # Don't let the success metric also act as its own guardrail.
    guardrails = [g for g in GUARDRAIL_METRICS if g != success_metric]

    stats = _version_stats(skill_id, [incumbent, candidate])
    inc = stats.get(incumbent)
    cand = stats.get(candidate)

    if not cand or cand["n"] < MIN_ACTIONS:
        log.info("candidate %s for skill %s has %d actions, below MIN_ACTIONS=%d",
                 candidate, skill_id, (cand or {}).get("n", 0), MIN_ACTIONS)
        return
    if not inc or inc["n"] < MIN_ACTIONS:
        log.info("incumbent %s for skill %s has insufficient telemetry; skipping",
                 incumbent, skill_id)
        return

    # The success metric must be present on BOTH versions. A NULL AVG (no
    # rows carried that rubric) is not a 0 — comparing against it would be
    # meaningless, so we skip rather than treat missing as failing.
    cand_succ = cand.get(success_metric)
    inc_succ = inc.get(success_metric)
    if cand_succ is None or inc_succ is None:
        log.info("skill %s missing %s on candidate/incumbent; cannot evaluate",
                 skill_id, success_metric)
        return

    success_lift = cand_succ - inc_succ
    if success_lift < MDE:
        log.info("candidate %s lift %.3f < MDE %.3f; no promotion",
                 candidate, success_lift, MDE)
        return

    # Significance: the lift must also be distinguishable from noise given
    # the per-version variance + sample sizes. A 0.05 lift on n=50 with high
    # variance is not a real win.
    sig_ok, z = _is_significant(cand, inc, success_metric)
    if not sig_ok:
        log.info("candidate %s lift %.3f not significant (z=%.2f < %.2f); no promotion",
                 candidate, success_lift, z, SIGNIFICANCE_Z)
        return

    guardrail_breach = []
    for g in guardrails:
        cv, iv = cand.get(g), inc.get(g)
        if cv is None or iv is None:
            # Can't assess this guardrail (rubric absent on a version) —
            # don't crash, just note we couldn't check it.
            log.info("skill %s guardrail %s unmeasurable (None); skipping check",
                     skill_id, g)
            continue
        drop = iv - cv
        if drop > GUARDRAIL_MAX_DROP:
            guardrail_breach.append({"metric": g, "drop": drop})

    if guardrail_breach:
        log.info("candidate %s breaches guardrails: %s; no promotion",
                 candidate, guardrail_breach)
        return

    _raise_promotion_request(skill_id, incumbent, candidate, inc, cand,
                             success_lift, success_metric=success_metric, z=z)


def _is_significant(cand: dict, inc: dict, metric: str) -> tuple[bool, float]:
    """Welch-style z on the metric means. Returns (significant, z).

    Uses the per-version stddev (``sd_<metric>``) + sample sizes from
    _version_stats. When variance data is unavailable we fall back to
    "significant" (MDE alone gates) but report z=inf so the log shows the
    check was not truly applied."""
    import math
    lift = (cand.get(metric) or 0) - (inc.get(metric) or 0)
    sd_c = cand.get(f"sd_{metric}")
    sd_i = inc.get(f"sd_{metric}")
    n_c, n_i = cand.get("n") or 0, inc.get("n") or 0
    if sd_c is None or sd_i is None or n_c < 2 or n_i < 2:
        return True, float("inf")  # no variance data → MDE-only fallback
    se = math.sqrt((sd_c ** 2) / n_c + (sd_i ** 2) / n_i)
    if se == 0:
        # Zero variance on both sides: any positive lift is fully separable.
        return (lift > 0), float("inf")
    z = lift / se
    return z >= SIGNIFICANCE_Z, z


def _evaluate_agent_skill_proposal(skill: dict) -> None:
    """Cross-channel gate for an Agent Skill self_critique_proposal.

    The Self-Critique Agent already declared which channels it thinks the
    issue affects (proposal["channels_affected"]) and wrote the FULL new
    body at versions[<candidate_id>].body_md. We:
      1. Re-verify the cross-channel signal against telemetry.
      2. Compute the unified diff server-side via difflib so it's
         deterministic, not LLM-generated.
      3. Either downgrade the proposal (gated out) or escalate it to a
         promotion_request the founder reviews.

    Checks (all must pass to escalate):
      A. >= MIN_CHANNELS_AFFECTED channels listed in proposal.
      B. The candidate's body_md actually exists in versions[<candidate>].
      C. Each listed channel has >= MIN_ACTIONS_PER_CHANNEL recent drafts.
      D. Each listed channel's rubric mean dips >= ISSUE_DIP_THRESHOLD
         below the Skill's project baseline.
    """
    skill_id = skill["_id"]
    # The self_critique_proposals array is the source of truth; load_proposals
    # wraps a legacy singleton when no array exists. We act on the gateable
    # entry (pending or founder-accepted) and persist status changes back to
    # the array, keeping the singleton mirror in sync — same contract the
    # accept handler uses.
    proposals = load_proposals(skill)
    entry = _gateable_entry(proposals)
    if entry is None:
        return
    affected = entry.get("channels_affected") or []

    if len(affected) < MIN_CHANNELS_AFFECTED:
        _downgrade_proposal(skill_id, proposals, entry,
                             reason="below_min_channels",
                             detail=(f"only {len(affected)} channel(s) "
                                      f"affected; need ≥{MIN_CHANNELS_AFFECTED} "
                                      f"to count as a Skill-level issue"))
        return

    # B. The candidate body_md must exist — Self-Critique should have
    # written it. If it's missing, the proposal is incomplete and there's
    # nothing to promote.
    candidate_id = entry.get("candidate_id")
    current_version = skill.get("current_version", "v1")
    versions = skill.get("versions") or {}
    if not candidate_id or not (versions.get(candidate_id) or {}).get("body_md"):
        _downgrade_proposal(
            skill_id, proposals, entry,
            reason="missing_candidate_body",
            detail=(f"versions[{candidate_id!r}].body_md is missing; "
                     "Self-Critique must write the full new body alongside "
                     "the proposal"),
        )
        return

    per_channel = _agent_skill_per_channel_stats(skill_id, channels=affected)
    if not per_channel:
        _downgrade_proposal(skill_id, proposals, entry,
                             reason="no_telemetry",
                             detail="no actions found loading this Skill")
        return

    # Compute baseline = simple mean of every channel's mean. Each channel
    # weighed equally — keeps a high-volume channel from dominating.
    valid_channels = [c for c in affected if per_channel.get(c, {}).get("n", 0)
                       >= MIN_ACTIONS_PER_CHANNEL]
    missing_volume = sorted(set(affected) - set(valid_channels))
    if missing_volume:
        _downgrade_proposal(skill_id, proposals, entry,
                             reason="insufficient_per_channel_volume",
                             detail=(f"channels below MIN_ACTIONS_PER_CHANNEL="
                                      f"{MIN_ACTIONS_PER_CHANNEL}: {missing_volume}"))
        return

    metric_values = [per_channel[c][ISSUE_RUBRIC] for c in valid_channels
                      if per_channel[c].get(ISSUE_RUBRIC) is not None]
    if not metric_values:
        _downgrade_proposal(skill_id, proposals, entry,
                             reason="no_rubric_data",
                             detail=f"no {ISSUE_RUBRIC} scores across channels")
        return
    project_baseline = sum(metric_values) / len(metric_values)

    breaches_baseline = [
        c for c in valid_channels
        if per_channel[c][ISSUE_RUBRIC] <= project_baseline - ISSUE_DIP_THRESHOLD
    ]
    if len(breaches_baseline) < MIN_CHANNELS_AFFECTED:
        _downgrade_proposal(
            skill_id, proposals, entry,
            reason="not_actually_cross_channel",
            detail=(f"only {len(breaches_baseline)} of {len(valid_channels)} "
                     f"channel(s) dip ≥{ISSUE_DIP_THRESHOLD} below baseline "
                     f"({project_baseline:.3f}); proposal looked cross-channel "
                     f"but telemetry says it's localized"),
        )
        return

    # Compute the unified diff server-side. difflib output starts with
    # ---/+++ file headers + one or more @@ hunks. MarkdownDiff in the UI
    # already renders this format.
    current_body = (versions.get(current_version) or {}).get("body_md", "")
    candidate_body = versions[candidate_id]["body_md"]
    proposed_diff = _compute_unified_diff(
        skill_id=skill_id,
        old=current_body, old_label=current_version,
        new=candidate_body, new_label=candidate_id,
    )

    # All checks pass — escalate to a promotion_request with the per-channel
    # baseline + computed diff baked in so the founder sees what they're
    # approving.
    _raise_agent_skill_promotion_request(
        skill=skill,
        proposals=proposals,
        entry=entry,
        per_channel=per_channel,
        project_baseline=project_baseline,
        channels_at_risk=breaches_baseline,
        candidate_id=candidate_id,
        incumbent=current_version,
        proposed_diff=proposed_diff,
    )


def _compute_unified_diff(*, skill_id: str, old: str, old_label: str,
                          new: str, new_label: str) -> str:
    """Build a unified diff string compatible with the UI's MarkdownDiff
    renderer. ``n=3`` is the standard context size; tweaks can show more
    surrounding lines per hunk if reviews need it.
    """
    import difflib
    lines = difflib.unified_diff(
        old.splitlines(keepends=False),
        new.splitlines(keepends=False),
        fromfile=f"{skill_id}/SKILL.md ({old_label})",
        tofile=f"{skill_id}/SKILL.md ({new_label})",
        n=3,
        lineterm="",
    )
    return "\n".join(lines)


def _agent_skill_per_channel_stats(skill_id: str,
                                    channels: list[str]) -> dict[str, dict]:
    """Per-channel rubric means for actions that loaded this Agent Skill in
    the last 14 days. Uses UNNEST(skills_loaded) so attribution survives
    cross-channel skill usage."""
    if not channels:
        return {}
    placeholders = ",".join([f"@c{i}" for i in range(len(channels))])
    params = [bigquery.ScalarQueryParameter("sk", "STRING", skill_id)]
    params.extend(
        bigquery.ScalarQueryParameter(f"c{i}", "STRING", c)
        for i, c in enumerate(channels)
    )
    sql = f"""
    SELECT channel,
           AVG(CAST(JSON_VALUE(eval_scores, '$.brand_voice')      AS FLOAT64)) AS brand_voice,
           AVG(CAST(JSON_VALUE(eval_scores, '$.claim_support')    AS FLOAT64)) AS claim_support,
           AVG(CAST(JSON_VALUE(eval_scores, '$.claim_risk')       AS FLOAT64)) AS claim_risk,
           AVG(CAST(JSON_VALUE(eval_scores, '$.icp_relevance')    AS FLOAT64)) AS icp_relevance,
           AVG(CAST(JSON_VALUE(eval_scores, '$.originality')      AS FLOAT64)) AS originality,
           AVG(CAST(JSON_VALUE(eval_scores, '$.conversion_intent') AS FLOAT64)) AS conversion_intent,
           COUNT(*) AS n
    FROM `{PROJECT_ID}.telemetry.actions`,
         UNNEST(skills_loaded) AS sk_name
    WHERE sk_name = @sk
      AND channel IN ({placeholders})
      AND ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 14 DAY)
      AND eval_scores IS NOT NULL
    GROUP BY channel
    """
    rows = BQ.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
    return {r.channel: dict(r) for r in rows}


def _gateable_entry(proposals: list[dict]) -> dict | None:
    """The proposal the gate should act on: a founder-ACCEPTED entry if one
    exists (explicit endorsement takes priority), else the highest-confidence
    PENDING one. ``None`` when there's nothing to gate."""
    accepted = [p for p in proposals
                if p and p.get("status") == Status.ACCEPTED]
    if accepted:
        return accepted[0]
    return primary_pending(proposals)


def _write_proposal_decision(skill_id: str, proposals: list[dict], *,
                              change_kind: str, extra_set: dict | None = None) -> None:
    """Persist the proposals array + refresh the singleton mirror, plus any
    extra $set (e.g. the promotion_request). Same array+mirror contract the
    accept handler uses: the singleton holds the primary *pending* proposal,
    so once an entry is gated (no longer pending) the mirror is cleared."""
    from mongo.history import update_with_history

    set_block: dict = {"self_critique_proposals": proposals}
    if extra_set:
        set_block.update(extra_set)
    update: dict = {"$set": set_block}
    mirror = primary_pending(proposals)
    if mirror is not None:
        set_block["self_critique_proposal"] = mirror
    else:
        update["$unset"] = {"self_critique_proposal": ""}
    update_with_history(
        Coll.SKILLS, {"_id": skill_id}, update,
        actor_id="promotion_gate", change_kind=change_kind,
    )


def _downgrade_proposal(skill_id: str, proposals: list[dict], entry: dict, *,
                         reason: str, detail: str) -> None:
    """Mark the proposal as rejected by the cross-channel gate so the founder
    isn't asked to approve a noisy proposal. The entry stays in the array
    (auditable); status flips so the weekly review filters it out."""
    entry["status"] = Status.REJECTED_BY_GATE
    entry["gate_reason"] = reason
    entry["gate_detail"] = detail
    entry["gated_at"] = datetime.now(UTC)
    _write_proposal_decision(
        skill_id, proposals,
        change_kind=f"agent_skill_proposal_gated:{reason}",
    )
    log.info("agent_skill %s proposal downgraded (%s): %s",
              skill_id, reason, detail)


def _raise_agent_skill_promotion_request(*, skill: dict, proposals: list[dict],
                                          entry: dict,
                                          per_channel: dict[str, dict],
                                          project_baseline: float,
                                          channels_at_risk: list[str],
                                          candidate_id: str,
                                          incumbent: str,
                                          proposed_diff: str) -> None:
    """Mint a promotion_request on the Agent Skill doc. The founder reviews
    this in the weekly UI; approval flows through promote_after_approval,
    which flips ``current_version`` in Mongo. The on-disk SKILL.md is a
    derived cache — ``shared.skills.read_body`` reconciles it lazily on
    the next ``read_skill(name)`` call.

    incumbent is the current_version from the skill doc (not from the
    proposal). The diff was computed server-side via difflib.
    """
    skill_id = skill["_id"]
    # Move the proposal OUT of the gateable pool now that it's escalated.
    # Without this the gate re-runs the same proposal every tick, raising a
    # duplicate promotion_request — and after promotion, a no-op request with
    # an empty diff — indefinitely. The promotion_request itself also guards
    # the scan, but flipping the entry status keeps the audit trail honest.
    entry["status"] = Status.ESCALATED
    entry["escalated_at"] = datetime.now(UTC)
    promotion_request = {
        "candidate": candidate_id,
        "incumbent": incumbent,
        "kind": "agent_skill",
        "issue": entry.get("issue"),
        "proposed_diff": proposed_diff,
        "channels_at_risk": channels_at_risk,
        "per_channel_baseline": {
            c: {k: v for k, v in s.items() if k != "channel"}
            for c, s in per_channel.items()
        },
        "project_baseline_brand_voice": project_baseline,
        "evidence_count": entry.get("evidence_count", 0),
        "confidence": entry.get("confidence"),
        "proposed_at": datetime.now(UTC),
        "status": Status.AWAITING_APPROVAL,
    }
    _write_proposal_decision(
        skill_id, proposals,
        change_kind="agent_skill_promotion_request_raised",
        extra_set={"promotion_request": promotion_request},
    )
    log.warning(
        "agent_skill promotion request raised: %s %s→%s "
        "channels_at_risk=%s baseline=%.3f",
        skill_id, incumbent, candidate_id, channels_at_risk, project_baseline,
    )


def _version_stats(skill_id: str, versions: list[str]) -> dict[str, dict]:
    """Aggregate per-version rubric means over the last 30 days."""
    placeholders = ",".join([f"@v{i}" for i in range(len(versions))])
    params = [bigquery.ScalarQueryParameter("sk", "STRING", skill_id)] + [
        bigquery.ScalarQueryParameter(f"v{i}", "STRING", v) for i, v in enumerate(versions)
    ]
    sql = f"""
    SELECT skill_version,
           AVG(CAST(JSON_VALUE(eval_scores, '$.brand_voice')      AS FLOAT64)) AS brand_voice,
           AVG(CAST(JSON_VALUE(eval_scores, '$.claim_support')    AS FLOAT64)) AS claim_support,
           AVG(CAST(JSON_VALUE(eval_scores, '$.claim_risk')       AS FLOAT64)) AS claim_risk,
           AVG(CAST(JSON_VALUE(eval_scores, '$.icp_relevance')    AS FLOAT64)) AS icp_relevance,
           AVG(CAST(JSON_VALUE(eval_scores, '$.originality')      AS FLOAT64)) AS originality,
           AVG(CAST(JSON_VALUE(eval_scores, '$.conversion_intent') AS FLOAT64)) AS conversion_intent,
           STDDEV(CAST(JSON_VALUE(eval_scores, '$.brand_voice')      AS FLOAT64)) AS sd_brand_voice,
           STDDEV(CAST(JSON_VALUE(eval_scores, '$.claim_support')    AS FLOAT64)) AS sd_claim_support,
           STDDEV(CAST(JSON_VALUE(eval_scores, '$.claim_risk')       AS FLOAT64)) AS sd_claim_risk,
           STDDEV(CAST(JSON_VALUE(eval_scores, '$.icp_relevance')    AS FLOAT64)) AS sd_icp_relevance,
           STDDEV(CAST(JSON_VALUE(eval_scores, '$.originality')      AS FLOAT64)) AS sd_originality,
           STDDEV(CAST(JSON_VALUE(eval_scores, '$.conversion_intent') AS FLOAT64)) AS sd_conversion_intent,
           COUNT(*) AS n
    FROM `{PROJECT_ID}.telemetry.actions`
    WHERE skill_id = @sk
      AND skill_version IN ({placeholders})
      AND ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
      AND eval_scores IS NOT NULL
    GROUP BY skill_version
    """
    result = BQ.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
    return {r.skill_version: {**dict(r), "n": r.n} for r in result}


def _raise_promotion_request(skill_id: str, incumbent: str, candidate: str,
                              inc: dict, cand: dict, lift: float, *,
                              success_metric: str = SUCCESS_METRIC,
                              z: float = float("inf")) -> None:
    """Write a promotion_request onto the skill doc.

    Routed through update_with_history so history.skills captures every
    promotion-request flip — both the initial raise and any subsequent
    refresh when the candidate's stats shift.
    """
    from mongo.history import update_with_history

    update_with_history(
        Coll.SKILLS,
        {"_id": skill_id},
        {"$set": {
            "promotion_request": {
                "candidate": candidate,
                "incumbent": incumbent,
                "success_metric": success_metric,
                "lift": lift,
                "significance_z": None if z == float("inf") else round(z, 3),
                "candidate_stats": {k: v for k, v in cand.items() if k != "n"},
                "candidate_n": cand["n"],
                "incumbent_stats": {k: v for k, v in inc.items() if k != "n"},
                "incumbent_n": inc["n"],
                "proposed_at": datetime.now(UTC),
                "status": Status.AWAITING_APPROVAL,
            }
        }},
        actor_id="promotion_gate",
        change_kind="promotion_request_raised",
    )
    log.warning("promotion request raised: %s candidate=%s metric=%s lift=%.3f "
                "z=%s over %d actions",
                skill_id, candidate, success_metric, lift,
                "inf" if z == float("inf") else f"{z:.2f}", cand["n"])


def promote_after_approval(skill_id: str, candidate: str) -> None:
    """Called by the founder (or weekly review CLI) when a promotion request
    is approved. Flips current_version and archives the old one.

    Goes through update_with_history so history.skills carries the version
    flip — this is the single biggest "what changed and why" question the
    founder asks during weekly review.

    Pure Mongo write: Mongo is the source of truth for
    ``skills.<id>.versions[<vN>].body_md``. For ``agent_skill`` kinds, the
    on-disk ``SKILL.md`` is a derived cache that ``shared.skills.read_body``
    reconciles lazily on the next ``read_skill(name)`` call. No disk
    touching here, no rollback path needed — if disk reconciliation later
    fails, Mongo stays correct and the next read retries.
    """
    from mongo.history import update_with_history

    skill = mongo_tools.find_one(Coll.SKILLS, {"_id": skill_id})
    if not skill:
        raise ValueError(f"skill {skill_id} not found")
    old = skill["current_version"]

    update_with_history(
        Coll.SKILLS,
        {"_id": skill_id},
        {
            "$set": {
                "current_version": candidate,
                "promoted_at": datetime.now(UTC),
            },
            "$push": {"history": candidate},
            "$pull": {"candidates": candidate},
            # Clear both the request AND the originating proposal slot so the
            # gate can't re-escalate an already-promoted candidate.
            "$unset": {"promotion_request": "", "self_critique_proposal": ""},
        },
        actor_id="founder",
        change_kind="promotion_approved",
    )
    log.info("promoted %s/%s (was %s)", skill_id, candidate, old)


if __name__ == "__main__":
    main()
