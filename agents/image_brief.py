"""ImageBrief Agent — 12th agent.

Sits between Content and Review in the drafting pipeline. Reads {draft}
and {channel} from session state, produces:
  1. An Imagen prompt that matches the draft's tone, ICP, and channel.
  2. Alt text for accessibility + Review's claim_risk check.
  3. The generated image (via the imagen_generate FunctionTool).
  4. {image} object written to state for Review and the publisher.

Why a dedicated agent: image briefing is a different skill than copy
drafting. The Content Agent is good at words. Separating concerns means
each can be optimized + evaluated independently. Long-term, the image
brief itself becomes a versioned playbook.
"""
from __future__ import annotations

from google.adk.tools import FunctionTool

from agents._factory import make_llm_agent
from agents._image_safety import check_image_safety_tool
from agents._models import LIGHT
from agents._prompts import IMAGE_BRIEF_INSTRUCTIONS
from shared import diagrams, imagen

# ImageBrief doesn't touch the mongo helpers directly — it reads voice/claims
# through the MCPToolset under the read-only DB user. Setting a process default
# here would be silently overwritten in the multi-agent a2a_server (see C5).


def imagen_generate(
    telemetry_id: str,
    prompt: str,
    channel: str,
    alt_text: str = "",
) -> dict:
    """FunctionTool exposed to the agent. Picks the right aspect ratio for
    the channel and calls Imagen via shared/imagen.py."""
    aspect = imagen.aspect_for_channel(channel)
    result = imagen.generate_image(
        prompt=prompt,
        telemetry_id=telemetry_id,
        aspect_ratio=aspect,
        alt_text=alt_text,
    )
    return {
        "mode": result.mode,
        "url": result.public_url,
        "gcs_uri": result.gcs_uri,
        "alt_text": result.alt_text,
        "width": result.width,
        "height": result.height,
        "aspect_ratio": result.aspect_ratio,
        "prompt": result.prompt,
        "reason": result.reason,
    }


imagen_generate_tool = FunctionTool(func=imagen_generate)


def diagram_generate(
    telemetry_id: str,
    mermaid: str,
    look: str = "handDrawn",
    alt_text: str = "",
) -> dict:
    """Render a Mermaid diagram to a hosted PNG with LEGIBLE text labels.

    look="handDrawn" -> excalidraw hand-drawn aesthetic; look="classic" -> a
    clean, flat infographic. PREFER this over imagen_generate for content
    channels (blog/substack/email/linkedin) when the draft has a framework,
    flow, comparison, or before/after that a labeled diagram conveys — an image
    model garbles text, this does not. Returns {mode, url, alt_text, kind, ...}.
    """
    return diagrams.render_mermaid(
        mermaid=mermaid, look=look, telemetry_id=telemetry_id, alt_text=alt_text,
    )


diagram_generate_tool = FunctionTool(func=diagram_generate)

# ImageBrief reads Mongo via the toolset but doesn't write — and historically
# this file omitted mongodb_toolset entirely (image safety + imagen are the
# only data-plane tools). Pass mode=None to preserve that surface exactly.
image_brief_agent = make_llm_agent(
    name="image_brief_agent",
    instructions=IMAGE_BRIEF_INSTRUCTIONS,
    model=LIGHT,
    mode=None,
    output_key="images",
    skill_id="image_brief",
    action_type="image_brief_op",
    extra_tools=[
        check_image_safety_tool,   # MUST be called before imagen_generate
        imagen_generate_tool,
        diagram_generate_tool,
    ],
)
