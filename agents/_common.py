"""Callback machinery: model-armor capture + telemetry emission + eval scoring.

Two callbacks per agent:
  after_model_callback — captures Model Armor decision from the LLM response
                          and caches the response text into state['_last_output'].
                          Telemetry's after_agent_callback reads both.
  after_agent_callback — runs once per agent invocation. Reads state, computes
                          rubric scores on drafting actions via Vertex AI Eval
                          Service, emits one row to telemetry.actions + N
                          outcome slots to telemetry.outcomes.

Callback bodies are wrapped in try/except: telemetry must NEVER fail an
agent run.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from shared.rubrics import ALL_RUBRICS, RubricDef, score_draft
from shared.telemetry import (
    EvalScores,
    ModelArmorResult,
    OutcomeSlot,
    TelemetryRecord,
    emit_action,
)


def _should_eval(telemetry_id: str | None) -> bool:
    """Cost control for rubric scoring.

    score_draft runs the Vertex Eval Service over 7 rubrics — ~7 judge LLM
    calls per draft, the single biggest LLM multiplier in the pipeline (it
    roughly DOUBLES the LLM calls of a draft). Sample a deterministic fraction
    of drafts keyed on telemetry_id (so retries score consistently) instead of
    scoring every one. The promotion gate + rubric-trend still get a
    representative stream at a fraction of the cost.

    Controlled by EVAL_SAMPLE_RATE (default 0.25 = score 1-in-4). Set to 1 to
    score every draft (the old behaviour), or 0 to disable inline scoring
    entirely (e.g. score in a nightly batch instead).
    """
    try:
        rate = float(os.environ.get("EVAL_SAMPLE_RATE", "0.25"))
    except ValueError:
        rate = 0.25
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    if not telemetry_id:
        return True
    bucket = int(hashlib.blake2b(telemetry_id.encode(), digest_size=8).hexdigest(), 16)
    return (bucket % 10_000) / 10_000.0 < rate


def _coerce_draft(value):
    """Best-effort: parse the draft into a dict if it's a JSON string,
    otherwise return as-is. ADK's LlmAgent often returns structured output
    as a string (raw JSON or ```json-fenced JSON) rather than a parsed dict;
    we want raw.draft to carry the structured form so the UI's Queue +
    substack publisher don't each have to re-parse.
    """
    if not isinstance(value, str):
        return value
    s = value.strip()
    # Strip ```json ... ``` fences. Regex-based to handle multi-line JSON.
    import re
    m = re.search(r"```(?:json|)\s*(\{.*\})\s*```", s, re.DOTALL)
    if m:
        s = m.group(1)
    elif not s.lstrip().startswith("{"):
        return value  # not JSON-shaped; keep the original
    try:
        import json as _json
        parsed = _json.loads(s)
        return parsed if isinstance(parsed, dict) else value
    except Exception:
        return value


def ensure_telemetry_id(state) -> str:
    """Get or create the session-wide telemetry_id.

    A SequentialAgent pipeline runs multiple sub-agents and emits one telemetry
    row per sub-agent. They must all share the same telemetry_id so attribution
    + edit-capture + image-upload-paths reference one canonical draft. The
    pipeline entry-points (demo/run_pipeline.py, services/web_api /api/draft)
    seed this; this helper provides the fallback if a sub-agent runs without
    one in scope (e.g., standalone Content invocation outside the pipeline).
    """
    tid = state.get("telemetry_id")
    if not tid:
        tid = f"act_{uuid.uuid4().hex[:12]}"
        state["telemetry_id"] = tid
    return tid

log = logging.getLogger(__name__)

TELEMETRY_DISABLED = os.environ.get("TELEMETRY_DISABLED", "").lower() in ("1", "true", "yes")


def make_after_callback(agent_name: str, skill_id: str, action_type: str,
                         channel: str | None = None,
                         rubrics: list[RubricDef] | None = None) -> Callable:
    """Return an after_agent_callback that emits a telemetry row + outcome slots.

    For drafting actions, runs Vertex AI Eval Service against the draft for
    all configured rubrics (default: all 6).
    """
    rubrics = rubrics or ALL_RUBRICS

    def callback(callback_context):  # type: ignore[no-untyped-def]
        if TELEMETRY_DISABLED:
            return None
        try:
            state = callback_context.state

            skill_version = state.get("skill_version", "v1")
            experiment_id = state.get("experiment_id")
            variant_id = state.get("variant_id")
            approval_id = state.get("approval_id")

            # output_key on LlmAgent wrote the agent's output to
            # state[<output_key>]. For Content this is state['draft'].
            # Substack drafts are structured dicts ({headline, subtitle,
            # body_markdown}); other channels are plain strings. ADK
            # commonly delivers structured output as a fenced-JSON STRING
            # ("```json\n{...}\n```") rather than as a parsed dict, so
            # we attempt JSON parse + fence strip before deciding the
            # shape. Persisting raw.draft as a dict is what lets the UI
            # show headline/subtitle separately instead of a wall of
            # markdown.
            draft_value = (
                state.get("draft")
                or state.get("_last_output")
                or ""
            )
            draft_value = _coerce_draft(draft_value)
            if isinstance(draft_value, dict):
                output_text = (
                    draft_value.get("body_markdown")
                    or draft_value.get("body")
                    or draft_value.get("text")
                    or ""
                )
            else:
                output_text = draft_value

            eval_scores = None
            if action_type.startswith("draft_") and output_text and _should_eval(state.get("telemetry_id")):
                try:
                    scores = score_draft(
                        candidate=output_text,
                        channel=channel,
                        icp_description=state.get("icp_description"),
                        rubrics=rubrics,
                    )
                    if scores:
                        from shared.rubrics import JUDGE_MODEL
                        eval_scores = EvalScores(
                            brand_voice=scores.get("brand_voice"),
                            claim_support=scores.get("claim_support"),
                            claim_risk=scores.get("claim_risk"),
                            icp_relevance=scores.get("icp_relevance"),
                            originality=scores.get("originality"),
                            conversion_intent=scores.get("conversion_intent"),
                            judge_model=JUDGE_MODEL,
                            scored_at=datetime.now(UTC),
                        )
                except Exception as e:
                    log.warning("eval scoring failed: %s", e)

            ma = state.get("model_armor")
            model_armor = ModelArmorResult(**ma) if ma else None

            telemetry_id = ensure_telemetry_id(state)

            # Resolve channel + skill_id + action_type from state so a single
            # Content agent serving every channel doesn't stamp every row as
            # 'draft_linkedin'. State takes precedence over the agent-level
            # default passed in at construction time.
            resolved_channel = state.get("channel") or channel
            resolved_skill_id = state.get("skill_id") or skill_id
            resolved_action_type = action_type
            if action_type.startswith("draft_") and resolved_channel:
                resolved_action_type = f"draft_{resolved_channel}"

            # raw.* carries the human-readable artifacts the downstream
            # consumers (eval_harness re-grading, substack_publisher, Queue UI)
            # need to reconstruct what this draft actually said. Substack
            # publishes from the structured form (headline + body_markdown),
            # so persist `draft_value` verbatim — not the flattened
            # `output_text` we used for scoring.
            raw_payload: dict = {}
            if action_type.startswith("draft_") and (output_text or draft_value):
                raw_payload["draft"] = draft_value or output_text
                if state.get("icp_segment"):
                    raw_payload["icp_segment"] = state["icp_segment"]
                rf = state.get("research_findings")
                if isinstance(rf, dict):
                    voice = rf.get("customer_voice")
                    if voice:
                        raw_payload["customer_voice_used"] = [
                            v.get("text") if isinstance(v, dict) else v
                            for v in voice
                        ]
                review = state.get("review")
                if isinstance(review, dict) and review.get("flags"):
                    raw_payload["review_flags"] = review["flags"]
                image = state.get("image")
                if isinstance(image, dict):
                    raw_payload["image"] = image

            record = TelemetryRecord(
                telemetry_id=telemetry_id,
                agent=agent_name,
                skill_id=resolved_skill_id,
                # Skills the agent loaded via read_skill / read_skill_reference
                # during this run — populated by _stamp_session_load in
                # shared/skills.py. Empty list is fine (Tier 1 metadata only).
                skills_loaded=list(state.get("_skills_loaded") or []),
                skill_version=skill_version,
                action_type=resolved_action_type,
                channel=resolved_channel,
                experiment_id=experiment_id,
                variant_id=variant_id,
                approval_id=approval_id,
                prompt_file=state.get("prompt_file"),
                eval_scores=eval_scores,
                model_armor=model_armor,
                trace_id=getattr(callback_context, "invocation_id", None),
                raw=raw_payload or None,
            )

            outcomes = _declared_outcomes(resolved_action_type)
            emit_action(record, outcomes=outcomes)
        except Exception as e:
            log.exception("emit_action failed: %s", e)
        return None

    return callback


def make_model_armor_callback() -> Callable:
    """after_model_callback: capture Model Armor decision + cache output text.

    With Model Armor enabled at the project level (gcloud model-armor
    floorsettings update --add-integrated-services=VERTEX_AI), blocked Gemini
    calls return a response with block_reason == 'MODEL_ARMOR'. We persist a
    structured record into state for the after_agent_callback.
    """

    def callback(callback_context, llm_response):  # type: ignore[no-untyped-def]
        try:
            block_reason = getattr(llm_response, "block_reason", None)
            if block_reason == "MODEL_ARMOR":
                msg = getattr(llm_response, "block_reason_message", "") or ""
                categories = [c.strip() for c in msg.split(",") if c.strip()]
                callback_context.state["model_armor"] = {
                    "decision": "block",
                    "categories": categories or ["unspecified"],
                }
            else:
                callback_context.state["model_armor"] = {
                    "decision": "allow", "categories": []
                }

            text = ""
            try:
                if llm_response.content and llm_response.content.parts:
                    text = "\n".join(p.text for p in llm_response.content.parts
                                     if getattr(p, "text", None))
            except Exception:
                pass
            if text:
                callback_context.state["_last_output"] = text
        except Exception as e:
            log.exception("model_armor callback failed: %s", e)
        return None

    return callback


# ---------------------------------------------------------------------------
# Tool-name repair — tolerate model tool-name hallucinations
# ---------------------------------------------------------------------------
#
# Gemini intermittently calls a tool by a slightly-wrong name — most often
# dropping the MCP ``mongodb_`` prefix and/or swapping the hyphen for an
# underscore (``list_collections`` instead of ``mongodb_list-collections``).
# ADK treats an unknown tool name as a hard ValueError, the agent run dies,
# and the error text leaks out AS THE DRAFT. We repair the name in the
# after_model_callback (before ADK dispatches) by canonical match against the
# real tool names, so a near-miss resolves instead of crashing the run.

_MONGODB_TOOL_NAMES = (
    "mongodb_find", "mongodb_aggregate", "mongodb_count",
    "mongodb_collection-schema", "mongodb_collection-indexes",
    "mongodb_list-collections", "mongodb_vector_search",
    "mongodb_insert_one", "mongodb_insert_many", "mongodb_update_one",
)


def _canonical_tool(name: str) -> str:
    """Normalize a tool name for fuzzy matching: drop a leading ``mongodb``
    prefix, lowercase, strip every non-alphanumeric char. So
    ``list_collections``, ``mongodb_list-collections`` and
    ``mongodbListCollections`` all collapse to ``listcollections``."""
    base = (name or "").lower()
    if base.startswith("mongodb"):
        base = base[len("mongodb"):]
    return re.sub(r"[^a-z0-9]", "", base)


_CANONICAL_TO_TOOL = {_canonical_tool(n): n for n in _MONGODB_TOOL_NAMES}


def repair_tool_name(name: str) -> str:
    """Return the real tool name for a (possibly hallucinated) ``name``.

    Exact matches pass through. A near-miss whose canonical form matches a
    known MongoDB tool is remapped; anything else is returned unchanged (so
    skill tools, web_search, etc. are never touched)."""
    if not name or name in _MONGODB_TOOL_NAMES:
        return name
    return _CANONICAL_TO_TOOL.get(_canonical_tool(name), name)


def make_tool_name_repair_callback() -> Callable:
    """after_model_callback that rewrites hallucinated tool-call names to the
    real registered tool before ADK dispatches them."""

    def callback(callback_context, llm_response):  # type: ignore[no-untyped-def]
        try:
            content = getattr(llm_response, "content", None)
            parts = getattr(content, "parts", None) or []
            changed = False
            for p in parts:
                fc = getattr(p, "function_call", None)
                nm = getattr(fc, "name", None) if fc else None
                if nm:
                    fixed = repair_tool_name(nm)
                    if fixed != nm:
                        log.warning("repaired hallucinated tool name %r -> %r",
                                    nm, fixed)
                        fc.name = fixed
                        changed = True
            if changed:
                return llm_response
        except Exception as e:  # noqa: BLE001 — never break the run on repair
            log.warning("tool-name repair callback failed: %s", e)
        return None

    return callback


def make_tool_result_sanitizer_callback() -> Callable:
    """after_tool_callback that BSON→JSON-sanitizes every tool result before it
    becomes a function_response in the LLM history.

    This is the defensive backstop for the ObjectId-serialization class of bug:
    even if some tool returns a raw ObjectId/datetime/Decimal128 deep inside a
    document, this strips it here so ADK's request serializer
    (``google_llm._build_request_log`` / the next model call) can't blow up the
    run with ``PydanticSerializationError`` (which surfaces as an empty draft).
    Source-layer sanitizers (``_mongodb_tools``, ``_evidence_tool``) still run;
    this guarantees coverage for any tool that forgets to.

    Returns a new dict only when a coercion was actually needed (so it's a
    no-op for the common all-JSON-clean case).
    """
    from shared.bson_json import jsonable

    def callback(tool, args, tool_context, tool_response):  # type: ignore[no-untyped-def]
        try:
            if isinstance(tool_response, dict):
                cleaned = jsonable(tool_response)
                if cleaned != tool_response:
                    return cleaned
        except Exception as e:  # noqa: BLE001 — never break a run on sanitize
            log.warning("tool-result sanitizer failed for %s: %s",
                        getattr(tool, "name", tool), e)
        return None

    return callback


def chain_after_model_callbacks(*callbacks: Callable) -> Callable:
    """Compose several after_model_callbacks into one. Each runs in order on the
    (possibly already-modified) response; the last non-None return wins as the
    response ADK proceeds with."""

    def combined(callback_context, llm_response):  # type: ignore[no-untyped-def]
        result = None
        for cb in callbacks:
            r = cb(callback_context, llm_response)
            if r is not None:
                llm_response = r
                result = r
        return result

    return combined


def _declared_outcomes(action_type: str) -> list[OutcomeSlot]:
    """Outcome slots declared at action time. Filled async by outcome_attach."""
    now = datetime.now(UTC)
    if action_type == "send_email" or action_type == "draft_email":
        return [
            OutcomeSlot(slot_name="open_rate_24h", metric="open_rate", source="hubspot",
                        expected_by=now + timedelta(hours=24)),
            OutcomeSlot(slot_name="click_rate_72h", metric="click_rate", source="hubspot",
                        expected_by=now + timedelta(hours=72)),
            OutcomeSlot(slot_name="reply_rate_7d", metric="reply_rate", source="hubspot",
                        expected_by=now + timedelta(days=7)),
        ]
    if action_type == "publish_linkedin" or action_type == "draft_linkedin":
        return [
            OutcomeSlot(slot_name="impressions_24h", metric="impressions",
                        source="linkedin_ads",
                        expected_by=now + timedelta(hours=24)),
            OutcomeSlot(slot_name="engagement_72h", metric="engagement",
                        source="linkedin_ads",
                        expected_by=now + timedelta(hours=72)),
        ]
    if action_type in ("publish_substack", "draft_substack"):
        # Substack has the dual web+email shape — we score both surfaces.
        return [
            OutcomeSlot(slot_name="open_rate_24h", metric="open_rate",
                        source="substack",
                        expected_by=now + timedelta(hours=24)),
            OutcomeSlot(slot_name="click_rate_72h", metric="click_rate",
                        source="substack",
                        expected_by=now + timedelta(hours=72)),
            OutcomeSlot(slot_name="restacks_7d", metric="restacks",
                        source="substack",
                        expected_by=now + timedelta(days=7)),
            OutcomeSlot(slot_name="new_subscribers_7d", metric="new_subscribers",
                        source="substack",
                        expected_by=now + timedelta(days=7)),
            OutcomeSlot(slot_name="paid_conversions_30d", metric="paid_conversions",
                        source="substack",
                        expected_by=now + timedelta(days=30)),
        ]
    return []
