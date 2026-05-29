"""Customer voice browser + negative examples (rejected patterns)."""

from fastapi import APIRouter

from shared import mongo_tools

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
    # customer_voice rows come back with raw ObjectIds when inserted by
    # the customer_voice_agent (no _id assigned). FastAPI's encoder
    # can't serialize ObjectId — stringify them so JSON encoding
    # succeeds. Also strip the auto-generated embedding (1024 floats)
    # since the UI doesn't render it.
    out = []
    for r in rows:
        r = dict(r)
        if "_id" in r and not isinstance(r["_id"], str):
            r["_id"] = str(r["_id"])
        r.pop("embedding", None)
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
    # Reject-driven inserts from /api/decisions LOCAL_DEV path leave raw
    # ObjectId _id values; FastAPI's encoder can't serialize those.
    out = []
    for r in rows:
        r = dict(r)
        if "_id" in r and not isinstance(r["_id"], str):
            r["_id"] = str(r["_id"])
        out.append(r)
    return out
