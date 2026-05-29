"""ImageBrief pre-generation safety check.

Wraps a cheap, deterministic check the ImageBrief agent must call BEFORE
invoking imagen_generate. The agent passes its planned prompt + alt_text;
this tool returns either {"safe": true, ...} or {"safe": false, "reasons":
[...]} so the agent rewrites the prompt before burning Imagen quota on
something the Review Agent will flag.

Why pre-gen vs post-gen:
- Imagen costs $$ per generation. Catching a risky prompt before the call
  saves money AND time.
- Generation failure modes (real-person likeness, brand logo, trademarked
  IP) are easier to spot in the prompt text than in the resulting pixels.
- The Review Agent will still post-check the generated image's alt_text,
  but having two layers (pre + post) is the standard governance pattern.

This is a deterministic rule check, NOT an LLM call. The patterns here are
the same risks Vertex's Model Armor would flag — we just front-load them
so the agent can correct, rather than triggering an Armor block downstream.
"""
from __future__ import annotations

import re

from google.adk.tools import FunctionTool

_PUBLIC_FIGURE_PATTERNS = [
    r"\b(elon musk|jeff bezos|mark zuckerberg|sundar pichai|satya nadella|"
    r"tim cook|sam altman|dario amodei|jensen huang|bill gates|warren buffett|"
    r"steve jobs|sheryl sandberg|marc benioff|peter thiel|reid hoffman|"
    r"trump|biden|obama|harris|pope|king charles)\b",
]

# Trademarks the system should NEVER stylize an asset around (defensive list;
# we add to this as legal/positioning surfaces new ones).
_TRADEMARK_PATTERNS = [
    r"\b(apple logo|google logo|microsoft logo|meta logo|amazon logo|"
    r"openai logo|anthropic logo|nvidia logo|hubspot logo|salesforce logo|"
    r"slack logo|notion logo|figma logo)\b",
    # Brand styling without "logo" still pulls trademark territory:
    r"\bin the style of (apple|nike|coca[- ]cola|disney|marvel)\b",
]

# Lookalike traps — phrases that hint the prompt is dressing up a real
# person or brand to dodge a direct mention.
_LIKENESS_PATTERNS = [
    r"\b(famous|celebrity|well[- ]known) (founder|ceo|investor|entrepreneur)\b",
    r"\b(realistic|photo[- ]realistic) portrait of a (man|woman) who looks like\b",
    r"\b(resembling|in the likeness of) [A-Z][a-z]+ [A-Z][a-z]+\b",
]

# Harmful imagery the agent should never request even tongue-in-cheek.
_DISALLOWED_PATTERNS = [
    r"\b(weapon|gun|knife|blood|gore|dead body|corpse|nude|naked|"
    r"sexual|fetish|drug use|cocaine|heroin|swastika|nazi)\b",
]

_OVER_CLAIM_PATTERNS = [
    # Alt text that makes a hard factual claim is a problem because the
    # image becomes evidence for the claim — a higher bar than copy.
    r"\b\d+%\b",        # any percentage in alt_text
    r"\b\d+x\b",        # 3x, 10x, etc.
    r"\b(guaranteed|proven|certified|FDA[- ]approved|patented)\b",
]


def check_image_safety(prompt: str, alt_text: str = "") -> dict:
    """Run deterministic safety + likeness + trademark checks against an
    Imagen prompt + its alt text. Caller passes both, gets back a verdict
    and (if unsafe) the exact rules that triggered so the agent can fix it.

    Args:
        prompt: The Imagen prompt the agent is about to call.
        alt_text: Optional accessibility alt-text the agent drafted.

    Returns:
        {
            "safe": bool,
            "reasons": [
                {"rule": "<rule_name>", "match": "<text that matched>"}
            ],
            "guidance": "<one-line how-to-fix message; empty if safe>"
        }
    """
    reasons = []
    body = prompt.lower() + " " + (alt_text or "").lower()

    for rx in _PUBLIC_FIGURE_PATTERNS:
        m = re.search(rx, body, flags=re.IGNORECASE)
        if m:
            reasons.append({
                "rule": "public_figure_likeness",
                "match": m.group(0),
            })

    for rx in _TRADEMARK_PATTERNS:
        m = re.search(rx, body, flags=re.IGNORECASE)
        if m:
            reasons.append({
                "rule": "trademark_or_brand_style",
                "match": m.group(0),
            })

    for rx in _LIKENESS_PATTERNS:
        m = re.search(rx, body, flags=re.IGNORECASE)
        if m:
            reasons.append({
                "rule": "lookalike_attempt",
                "match": m.group(0),
            })

    for rx in _DISALLOWED_PATTERNS:
        m = re.search(rx, body, flags=re.IGNORECASE)
        if m:
            reasons.append({
                "rule": "disallowed_imagery",
                "match": m.group(0),
            })

    # alt_text gets a stricter rule: it's accessibility content AND becomes
    # part of the claim surface the Review agent grades.
    for rx in _OVER_CLAIM_PATTERNS:
        m = re.search(rx, alt_text or "", flags=re.IGNORECASE)
        if m:
            reasons.append({
                "rule": "alt_text_overclaims",
                "match": m.group(0),
            })

    safe = not reasons
    guidance = ""
    if not safe:
        rules = {r["rule"] for r in reasons}
        if "public_figure_likeness" in rules:
            guidance = ("Remove the named public figure; describe the role "
                        "or persona instead (e.g., 'a tech CEO at a podium').")
        elif "trademark_or_brand_style" in rules:
            guidance = ("Drop the brand/logo reference. Describe the visual "
                        "abstractly (e.g., 'minimal flat-design app icon').")
        elif "lookalike_attempt" in rules:
            guidance = ("Don't engineer a likeness. Describe an anonymous "
                        "persona in plain visual terms.")
        elif "disallowed_imagery" in rules:
            guidance = "Disallowed imagery — remove entirely and reconceive."
        elif "alt_text_overclaims" in rules:
            guidance = ("Alt text shouldn't make a hard factual claim. "
                        "Describe what the image SHOWS, not what it PROVES.")

    return {"safe": safe, "reasons": reasons, "guidance": guidance}


check_image_safety_tool = FunctionTool(func=check_image_safety)
