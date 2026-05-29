"""Skills catalog, bodies, samples, and promotion decisions."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mongo import queries
from shared import mongo_tools

router = APIRouter()


@router.get("/api/skills")
def list_skills():
    return list(mongo_tools.db()["skills"].find({}))


@router.get("/api/skills/{skill_id}")
def get_skill(skill_id: str):
    s = queries.skill(skill_id)
    if not s:
        raise HTTPException(404, f"skill {skill_id} not found")
    return s


@router.get("/api/skills/{skill_id}/body")
def get_skill_body(skill_id: str, version: str | None = None) -> dict:
    """Return the SKILL.md / playbook body for a skill at a given version.

    Two file layouts to support:
      - playbook skills (skill_kind absent): bodies live at
        ``prompts/<dir>/<version>.txt`` where the version field IS the
        filename (e.g., 'linkedin_post_v3.txt'). The right ``<dir>`` is
        derived from the skill_id prefix.
      - agent skills (skill_kind == 'agent_skill'): bodies live at
        ``skills/<skill_id>/SKILL.md``. Version is always 'v1' in our
        registry today; on-disk content IS the version.

    Returns {body, format, version_label, source_path}. format is 'markdown'
    or 'text'; the UI renders both as preformatted text but markdown gets
    syntax-style emphasis.
    """
    s = queries.skill(skill_id)
    if not s:
        raise HTTPException(404, f"skill {skill_id} not found")

    # Repo root is three levels up from this file: routers/ -> web_api/ ->
    # services/ -> <repo root>.
    repo_root = Path(__file__).resolve().parents[3]
    v = version or s.get("current_version") or ""
    skill_kind = s.get("skill_kind")

    if skill_kind == "agent_skill":
        candidate = repo_root / "skills" / skill_id / "SKILL.md"
        if not candidate.is_file():
            raise HTTPException(404, f"SKILL.md not found for {skill_id}")
        return {
            "body": candidate.read_text(encoding="utf-8"),
            "format": "markdown",
            "version_label": v or "v1",
            "source_path": str(candidate.relative_to(repo_root)),
        }

    # Playbook skill — derive prompts subdir from the skill_id family.
    # The convention is: prompts/<family>/<version>.txt where family is
    # 'content' for drafting skills, 'review', 'research', 'cmo_planner'.
    # We probe a small set of known subdirs to find the file.
    subdirs = ["content", "review", "research", "cmo_planner"]
    candidates = [repo_root / "prompts" / d / v for d in subdirs]
    found = next((c for c in candidates if c.is_file()), None)
    if not found:
        raise HTTPException(
            404,
            f"playbook body not found for {skill_id}@{v} — looked in "
            + ", ".join(str(c.relative_to(repo_root)) for c in candidates),
        )
    return {
        "body": found.read_text(encoding="utf-8"),
        "format": "text",
        # Clean label: strip the '.txt' and the leading '<skill_id>_' so
        # 'linkedin_post_v3.txt' renders as 'v3'.
        "version_label": (
            v.removesuffix(".txt").replace(f"{skill_id}_", "")
            if v.startswith(skill_id) else v.removesuffix(".txt")
        ),
        "source_path": str(found.relative_to(repo_root)),
    }


class SampleDraft(BaseModel):
    telemetry_id: str
    ts: datetime
    skill_version: str
    channel: str | None
    draft_text: str
    eval_scores: dict[str, float]


@router.get("/api/skills/{skill_id}/samples")
def skill_samples(skill_id: str, version: str, n: int = 3) -> list[SampleDraft]:
    """Last N drafts for (skill_id, skill_version) with eval scores.

    Used by the Skills page to render incumbent vs candidate sample drafts
    side-by-side — the qualitative proof behind the quantitative lift.
    """
    # Imported here (not at module top) so the router can be imported
    # under LOCAL_DEV without google-cloud-bigquery installed. The actual
    # BQ client lives on services.web_api.main as ``BQ`` and is None in
    # LOCAL_DEV.
    from services.web_api.main import BQ, PROJECT_ID, bigquery
    if BQ is None:
        return []  # LOCAL_DEV: no BQ → empty samples
    sql = f"""
    SELECT telemetry_id, ts, skill_version, channel, eval_scores, raw
    FROM `{PROJECT_ID}.telemetry.actions`
    WHERE skill_id = @sk
      AND skill_version = @v
      AND action_type LIKE 'draft_%'
      AND eval_scores IS NOT NULL
    ORDER BY ts DESC
    LIMIT @n
    """
    params = [
        bigquery.ScalarQueryParameter("sk", "STRING", skill_id),
        bigquery.ScalarQueryParameter("v", "STRING", version),
        bigquery.ScalarQueryParameter("n", "INT64", n),
    ]
    rows = BQ.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()

    out: list[SampleDraft] = []
    for r in rows:
        raw = r.raw or {}
        if isinstance(raw, str):
            import json
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        draft_blob = raw.get("draft") or raw.get("output_text") or ""
        if isinstance(draft_blob, dict):
            headline = draft_blob.get("headline") or ""
            body = draft_blob.get("body_markdown") or draft_blob.get("body") or ""
            draft_blob = f"# {headline}\n\n{body}" if headline else body
        out.append(SampleDraft(
            telemetry_id=r.telemetry_id,
            ts=r.ts,
            skill_version=r.skill_version,
            channel=r.channel,
            draft_text=draft_blob,
            eval_scores=r.eval_scores or {},
        ))
    return out


class PromotionDecision(BaseModel):
    decision: str  # "approve" | "reject"


@router.post("/api/skills/{skill_id}/promotion")
def decide_promotion(skill_id: str, body: PromotionDecision):
    from services.promotion_gate.main import promote_after_approval

    s = mongo_tools.find_one("skills", {"_id": skill_id})
    if not s or not s.get("promotion_request"):
        raise HTTPException(404, f"no promotion_request on {skill_id}")

    if body.decision == "approve":
        candidate = s["promotion_request"]["candidate"]
        promote_after_approval(skill_id, candidate)
        return {"ok": True, "promoted_to": candidate}
    elif body.decision == "reject":
        # Drop the request AND terminalize the originating proposal so the
        # promotion gate doesn't immediately re-escalate the same candidate.
        # rejected_by_gate + a timestamp puts it inside the miner's decision
        # cooldown (see self_critique_runner._recently_decided).
        from datetime import datetime

        from agents._schema_constants import Status
        mongo_tools.db()["skills"].update_one(
            {"_id": skill_id},
            {"$unset": {"promotion_request": ""},
             "$set": {
                 "self_critique_proposal.status": Status.REJECTED_BY_GATE,
                 "self_critique_proposal.gated_at": datetime.now(UTC),
             }},
        )
        return {"ok": True, "rejected": True}
    raise HTTPException(400, "decision must be approve or reject")
