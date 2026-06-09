"""One recursive BSON→JSON sanitizer, shared by every layer that hands Mongo
documents to an LLM.

Why this is centralized: ADK serializes the *entire* LLM request — including
tool-result history — to JSON, both for its request log
(``google_llm._build_request_log``) and for the next model call. A raw
``ObjectId`` (or ``datetime`` / ``Decimal128`` / bytes) ANYWHERE in a document a
tool returns — not just the top-level ``_id`` — makes that serialization raise
``PydanticSerializationError`` and the agent run dies mid-flight with an empty
result. This bit us twice in prod (``customer_voice.signal_id`` is an ObjectId,
surfaced first by ``mongodb_vector_search`` and then by ``validate_claim``),
each in a separately-written ``_clean``/``_stringify_id`` helper that only
handled the top-level ``_id``. Having one implementation — applied at the
read-tool layer AND as a defensive ``after_tool_callback`` — means no future
tool can reintroduce the bug.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import Decimal128, ObjectId


def jsonable(value: Any) -> Any:
    """Recursively coerce Mongo/BSON values into JSON-serializable forms.

    Walks nested dicts/lists/tuples and converts the offenders ADK can't
    serialize: ObjectId → str, datetime → ISO-8601 str, Decimal128 → str,
    bytes → None (we never ship binary blobs back to the model). Everything
    else passes through untouched.
    """
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal128):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return None
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value
