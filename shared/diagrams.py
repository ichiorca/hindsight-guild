"""Mermaid diagram rendering for content-channel visuals.

Calls the ``mermaid-renderer`` Cloud Run service (Mermaid -> PNG, handDrawn or
classic look), uploads the PNG to the media bucket, and returns the same dict
shape the ImageBrief agent emits for an Imagen image. Unlike an image model,
this produces LEGIBLE text labels — so it's what we use for blog/article/post
infographics + excalidraw diagrams.

The renderer URL comes from ``MERMAID_RENDERER_URL`` (set on the agent services
at deploy time). The service is private, so we mint a Cloud Run ID token for it
exactly like the A2A calls (see agents/a2a_client). Best-effort: a render
failure returns a ``mode="stub"`` dict so the pipeline never breaks.
"""
from __future__ import annotations

import logging
import os
import uuid

import httpx

from shared.imagen import MEDIA_BUCKET, PROJECT_ID

log = logging.getLogger(__name__)

# look -> what we call the visual type in the image dict + UI.
_LOOK_TO_KIND = {"handDrawn": "excalidraw", "classic": "infographic"}


def _renderer_url() -> str | None:
    url = os.environ.get("MERMAID_RENDERER_URL")
    return url.rstrip("/") if url else None


def _auth_headers(target_url: str) -> dict:
    """Cloud Run ID token (audience = renderer URL), minted from the metadata
    server for the running service account. No-op in LOCAL_DEV."""
    if os.environ.get("LOCAL_DEV"):
        return {}
    try:
        from urllib.parse import urlsplit

        import google.auth.transport.requests
        import google.oauth2.id_token

        parts = urlsplit(target_url)
        audience = f"{parts.scheme}://{parts.netloc}"
        token = google.oauth2.id_token.fetch_id_token(
            google.auth.transport.requests.Request(), audience)
        return {"Authorization": f"Bearer {token}"}
    except Exception as e:  # noqa: BLE001 — best-effort; a 403 surfaces if it fails
        log.warning("could not mint ID token for %s: %s", target_url, e)
        return {}


def _stub(alt_text: str, look: str, reason: str) -> dict:
    return {
        "mode": "stub",
        "url": None,
        "alt_text": alt_text or "(diagram pending)",
        "kind": _LOOK_TO_KIND.get(look, "infographic"),
        "rationale": reason,
        "confidence": "low",
    }


def render_mermaid(mermaid: str, look: str, telemetry_id: str,
                    alt_text: str, aspect_ratio: str = "16:9") -> dict:
    """Render a Mermaid diagram to a hosted PNG. ``look`` is "handDrawn"
    (excalidraw) or "classic" (clean infographic). Returns an image dict
    ({mode,url,alt_text,kind,aspect_ratio,...}) or a stub on any failure."""
    look = "handDrawn" if look == "handDrawn" else "classic"
    url = _renderer_url()
    if not url:
        return _stub(alt_text, look, "renderer_unconfigured")
    if not mermaid or not mermaid.strip():
        return _stub(alt_text, look, "empty_mermaid")
    try:
        r = httpx.post(
            f"{url}/render",
            json={"mermaid": mermaid, "look": look},
            headers={**_auth_headers(url), "Content-Type": "application/json"},
            timeout=90,
        )
        r.raise_for_status()
        png = r.content
        if not png or r.headers.get("content-type", "").startswith("application/json"):
            return _stub(alt_text, look, "renderer_no_image")

        from google.cloud import storage
        bucket = storage.Client(project=PROJECT_ID).bucket(MEDIA_BUCKET)
        blob_name = f"{telemetry_id}/diagram_{uuid.uuid4().hex[:8]}.png"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(png, content_type="image/png")
        # Bucket is public via IAM (UBLA) — no per-object make_public.
        public_url = f"https://storage.googleapis.com/{MEDIA_BUCKET}/{blob_name}"
        return {
            "mode": "api",
            "url": public_url,
            "alt_text": alt_text,
            "kind": _LOOK_TO_KIND[look],
            "aspect_ratio": aspect_ratio,
            "rationale": f"{_LOOK_TO_KIND[look]} diagram rendered from the draft's structure",
            "confidence": "high",
        }
    except Exception as e:  # noqa: BLE001 — never break the pipeline on a visual
        log.warning("mermaid render failed (%s): %s", look, e)
        return _stub(alt_text, look, f"render_error: {e!s}"[:200])
