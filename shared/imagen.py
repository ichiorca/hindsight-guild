"""Imagen wrapper — generates an image, returns a reachable URL.

Per-channel sizing is the caller's responsibility (the ImageBrief Agent
picks the right aspect_ratio based on channel). We just take a prompt +
aspect + telemetry_id and return a stable URL.

Two backends, picked at runtime:
  - **Cloud (default)**: Vertex AI Imagen → GCS upload → public URL.
    Used when ADC is configured and ``LOCAL_DEV`` is unset.
    Storage: gs://${PROJECT_ID}-media/<telemetry_id>/<n>.png
  - **Local-dev**: ``google.genai.Client(api_key=GOOGLE_API_KEY)`` →
    Imagen 4 fast → write PNG to ``drafts/media/<telemetry_id>/<n>.png``
    → return ``file://`` URL. Used when LOCAL_DEV=1 or Vertex auth fails.

Cost: ~$0.04 per Imagen 3 image, ~$0.02 per Imagen 4 fast image. At 30
drafts/wk this is a few dollars/month either way.
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "hindsight-guild-mvp")
LOCATION = os.environ.get("REGION", "us-central1")
MEDIA_BUCKET = os.environ.get("MEDIA_BUCKET", f"{PROJECT_ID}-media")

# Resolve the local media root once. Repo root is two levels up from this
# file (shared/imagen.py → repo root). Caller's MEDIA_DIR_LOCAL overrides.
_REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_MEDIA_DIR = Path(
    os.environ.get("MEDIA_DIR_LOCAL", str(_REPO_ROOT / "drafts" / "media"))
)


def _is_local_dev() -> bool:
    """Use the AI Studio path when LOCAL_DEV=1 — same toggle the rest of
    the codebase uses to skip BQ/GCS/Vertex. Mirrors ``shared/telemetry.py``
    and ``shared/mongo_tools.py`` so a single env var flips the whole stack."""
    return os.environ.get("LOCAL_DEV") == "1"

# Aspect ratios Imagen 3 supports natively
AspectRatio = Literal["1:1", "9:16", "16:9", "3:4", "4:3"]


@dataclass
class GeneratedImage:
    mode: Literal["api", "stub"]
    gcs_uri: str | None
    public_url: str | None
    alt_text: str
    width: int
    height: int
    prompt: str
    aspect_ratio: AspectRatio
    reason: str | None = None


# Channel → preferred aspect ratio. Used by the ImageBrief Agent so the
# generated asset fits the publishing surface without per-channel rework.
CHANNEL_ASPECT: dict[str, AspectRatio] = {
    "linkedin": "1:1",       # square works for both feed and DMs
    "email":    "16:9",      # email header banner
    "blog":     "16:9",      # blog hero
    "substack": "16:9",      # newsletter featured image
    "meta_ads": "1:1",       # Meta feed
    "google_ads": "1:1",     # display network
    "landing_page": "16:9",  # hero
}

# Rough pixel sizes corresponding to each aspect (Imagen 3 outputs are
# 1024px on the long side at these ratios).
ASPECT_PIXELS: dict[AspectRatio, tuple[int, int]] = {
    "1:1":  (1024, 1024),
    "9:16": (576, 1024),
    "16:9": (1024, 576),
    "3:4":  (768, 1024),
    "4:3":  (1024, 768),
}


def aspect_for_channel(channel: str | None) -> AspectRatio:
    return CHANNEL_ASPECT.get(channel or "", "1:1")


def generate_image(
    prompt: str,
    *,
    telemetry_id: str,
    aspect_ratio: AspectRatio = "1:1",
    alt_text: str = "",
    n: int = 1,
    safety_filter: str = "block_some",
    model_name: str | None = None,
) -> GeneratedImage:
    """Generate an image. Backend depends on environment:
      - LOCAL_DEV=1 → google.genai (Imagen 4 fast) → local PNG file.
      - else → Vertex AI Imagen → GCS public URL.

    On any failure (auth, safety filter, quota), returns mode="stub" with
    reason so the downstream pipeline keeps moving and Review can flag for
    manual upload.
    """
    width, height = ASPECT_PIXELS[aspect_ratio]

    if _is_local_dev():
        return _generate_local(
            prompt=prompt,
            telemetry_id=telemetry_id,
            aspect_ratio=aspect_ratio,
            alt_text=alt_text,
            n=n,
            model_name=model_name or "imagen-4.0-fast-generate-001",
            width=width, height=height,
        )

    return _generate_vertex(
        prompt=prompt,
        telemetry_id=telemetry_id,
        aspect_ratio=aspect_ratio,
        alt_text=alt_text,
        n=n,
        safety_filter=safety_filter,
        model_name=model_name or "imagen-3.0-generate-002",
        width=width, height=height,
    )


def _generate_local(
    *, prompt: str, telemetry_id: str, aspect_ratio: AspectRatio,
    alt_text: str, n: int, model_name: str, width: int, height: int,
) -> GeneratedImage:
    """AI Studio path — works with GOOGLE_API_KEY, no ADC needed.

    Uses Imagen 4 fast (the cheapest/fastest Imagen 4 SKU available via
    the AI Studio API) and writes the PNG to ``LOCAL_MEDIA_DIR``. Returns
    a ``file://`` URL so the markdown driver can embed locally.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return _stub_result(prompt, aspect_ratio, alt_text, telemetry_id,
                             reason="LOCAL_DEV path needs GOOGLE_API_KEY")
    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        resp = client.models.generate_images(
            model=model_name,
            prompt=prompt,
            config={
                "number_of_images": n,
                "aspect_ratio": aspect_ratio,
                # AI Studio's Imagen 4 rejects ALL person-related prompts
                # unless person_generation is explicitly allowed. The image
                # safety check has already filtered named likeness; this
                # only opens generic-person rendering.
                "person_generation": "allow_adult",
            },
        )
        imgs = resp.generated_images or []
        if not imgs:
            return _stub_result(prompt, aspect_ratio, alt_text, telemetry_id,
                                 reason="imagen_returned_empty")
        img_bytes = imgs[0].image.image_bytes
        if not img_bytes:
            return _stub_result(prompt, aspect_ratio, alt_text, telemetry_id,
                                 reason="imagen_returned_no_bytes")

        out_dir = LOCAL_MEDIA_DIR / telemetry_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{uuid.uuid4().hex[:8]}.png"
        out_path.write_bytes(img_bytes)

        log.info("Imagen LOCAL: wrote %s (%d bytes)",
                 out_path, len(img_bytes))
        # Return an HTTP-relative URL so the React UI (served from Vite at
        # localhost:5173 with /api + /media proxied to the FastAPI on 8000)
        # can <img src> it. Browsers reject file:/// from http:// origins.
        # The FastAPI app mounts /media → LOCAL_MEDIA_DIR (see web_api/main.py).
        http_url = f"/media/{telemetry_id}/{out_path.name}"
        return GeneratedImage(
            mode="api",
            gcs_uri=None,
            public_url=http_url,
            alt_text=alt_text,
            width=width, height=height,
            prompt=prompt, aspect_ratio=aspect_ratio,
        )
    except Exception as e:
        log.warning("Imagen LOCAL failed for %s: %s", telemetry_id, e)
        return _stub_result(prompt, aspect_ratio, alt_text, telemetry_id,
                             reason=f"imagen_local_error: {e!s}")


def _generate_vertex(
    *, prompt: str, telemetry_id: str, aspect_ratio: AspectRatio,
    alt_text: str, n: int, safety_filter: str, model_name: str,
    width: int, height: int,
) -> GeneratedImage:
    """Cloud path — Vertex Imagen → GCS upload. Requires ADC."""
    try:
        import vertexai
        from google.cloud import storage
        from vertexai.preview.vision_models import ImageGenerationModel

        vertexai.init(project=PROJECT_ID, location=LOCATION)
        model = ImageGenerationModel.from_pretrained(model_name)
        result = model.generate_images(
            prompt=prompt,
            number_of_images=n,
            aspect_ratio=aspect_ratio,
            safety_filter_level=safety_filter,
            person_generation="allow_adult",
        )
        if not result or not result.images:
            return _stub_result(prompt, aspect_ratio, alt_text, telemetry_id,
                                 reason="imagen_returned_empty")

        img = result.images[0]
        bucket = storage.Client(project=PROJECT_ID).bucket(MEDIA_BUCKET)
        blob_name = f"{telemetry_id}/{uuid.uuid4().hex[:8]}.png"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(img._image_bytes, content_type="image/png")
        # Public read so the email/web previews can embed without auth.
        # In Phase 2 swap for signed URLs + auth.
        blob.make_public()

        return GeneratedImage(
            mode="api",
            gcs_uri=f"gs://{MEDIA_BUCKET}/{blob_name}",
            public_url=blob.public_url,
            alt_text=alt_text,
            width=width, height=height,
            prompt=prompt, aspect_ratio=aspect_ratio,
        )
    except Exception as e:
        log.warning("Imagen Vertex failed for %s: %s", telemetry_id, e)
        return _stub_result(prompt, aspect_ratio, alt_text, telemetry_id,
                             reason=f"imagen_error: {e!s}")


def _stub_result(prompt: str, aspect_ratio: AspectRatio,
                  alt_text: str, telemetry_id: str,
                  reason: str) -> GeneratedImage:
    """When Imagen is unavailable, return a placeholder GCS path. The
    ChannelPreview will render a styled placeholder card; the Review Agent
    flags it for manual upload."""
    width, height = ASPECT_PIXELS[aspect_ratio]
    return GeneratedImage(
        mode="stub",
        gcs_uri=None,
        public_url=None,
        alt_text=alt_text or "(image pending)",
        width=width, height=height,
        prompt=prompt, aspect_ratio=aspect_ratio,
        reason=reason,
    )
