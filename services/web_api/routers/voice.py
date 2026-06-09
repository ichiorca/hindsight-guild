"""Customer voice browser + negative examples (rejected patterns)."""

from fastapi import APIRouter

from shared import mongo_tools
from shared.bson_json import jsonable

router = APIRouter()


@router.get("/api/voice")
def list_voice(icp_segment: str | None = None, theme: str | None = None,
                limit: int = 100):
    q: dict = {}
    if icp_segment:
        q["icp_segment"] = icp_segment
    if theme:
        q["theme"] = theme
    rows = mongo_tools.find("customer_voice", q, limit=limit)
    # customer_voice rows carry raw BSON FastAPI's encoder can't serialize:
    # the top-level ``_id`` AND ``signal_id`` (an ObjectId mirrored from the
    # signals collection on signal-triggered ingests), plus datetimes. A
    # top-level-_id-only fix used to 500 the page with "'ObjectId' object is
    # not iterable" once signal_id appeared. Use the shared recursive
    # sanitizer so every nested BSON value is coerced. Strip the embedding
    # (1024 floats) first since the UI doesn't render it.
    out = []
    for r in rows:
        r = dict(r)
        r.pop("embedding", None)
        r = jsonable(r)
        # Normalize: customer_voice_agent occasionally writes ``raw_quote``
        # instead of ``text`` (the schema contract). Surface as ``text`` so
        # the UI's Voice page renders consistently. New agent runs use
        # ``text`` per the updated CUSTOMER_VOICE_INSTRUCTIONS prompt.
        if "text" not in r and "raw_quote" in r:
            r["text"] = r["raw_quote"]
        out.append(r)
    return out


@router.get("/api/negatives")
def list_negatives(channel: str | None = None, category: str | None = None,
                    limit: int = 50):
    q: dict = {}
    if channel:
        q["channel"] = channel
    if category:
        q["rejection_category"] = category
    rows = mongo_tools.find_sorted("negative_examples", q,
                                    sort=[("ts", -1)], limit=limit)
    # Reject-driven inserts leave raw ObjectId _id values (and provenance
    # blocks may nest more BSON); FastAPI's encoder can't serialize those.
    # Recursively sanitize via the shared coercer.
    return [jsonable(dict(r)) for r in rows]
