"""Outbound integrations — adapters to the third-party platforms the
founder publishes to.

Currently wired:
  - Dev.to (Forem)   — free, no review; maps to ``blog`` channel
  - Substack         — newsletter publish via the substack_publisher service
  - LinkedIn UGC     — personal/org share via v2/ugcPosts
  - Google Ads       — paused Responsive Search Ad (RSA) draft
  - Meta Ads         — paused Facebook/Instagram ad creative
  - Email (HubSpot)  — nurture sequence persisted + step 1 staged as a
                       DRAFT marketing email (never auto-sent)

Every integration here is gated on an env-var credential check
(``<module>.is_configured()``). Missing creds → the integration is a
no-op, the approval still saves locally, the rest of the system
carries on.

The CHANNEL_ROUTES table below maps Mongo ``actions.channel`` values
to the integration module responsible for publishing them. Services
that dispatch on channel (currently ``services/web_api/main.py``) import
this table rather than maintaining their own switch.
"""
from __future__ import annotations

from . import devto, email_esp, google_ads, linkedin, meta_ads, substack

# channel slug → (integration_module, human-readable platform name)
# The platform name is the surface label the UI shows on the Ship-it
# button and the post-ship "Published → X" chip.
CHANNEL_ROUTES: dict[str, tuple[object, str]] = {
    "blog": (devto, "Dev.to"),
    "substack": (substack, "Substack"),
    "linkedin": (linkedin, "LinkedIn"),
    "google_ads": (google_ads, "Google Ads"),
    "meta_ads": (meta_ads, "Meta Ads"),
    # Email ships as a HubSpot DRAFT (never auto-sends) + always persists
    # the sequence to email_sequences — see queue.py::_publish_email.
    "email": (email_esp, "HubSpot (draft)"),
    "lifecycle_email": (email_esp, "HubSpot (draft)"),
}


def status_snapshot() -> dict[str, dict[str, object]]:
    """Return ``{integration_slug: {configured, channels, platform}}``
    for the ``/api/integrations/status`` endpoint.

    Built by inverting CHANNEL_ROUTES so each integration lists all the
    channels it serves (Dev.to handles ``blog``; Substack handles
    ``substack`` via its own publisher service).
    """
    by_module: dict[str, dict[str, object]] = {}
    for channel, (mod, platform) in CHANNEL_ROUTES.items():
        slug = mod.__name__.rsplit(".", 1)[-1]
        entry = by_module.setdefault(slug, {
            "configured": bool(mod.is_configured()),
            "platform": platform,
            "channels": [],
        })
        entry["channels"].append(channel)
    return by_module
