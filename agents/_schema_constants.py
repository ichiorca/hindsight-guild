"""Single source of truth for status enums, collection names, channel
labels, and ICP segments shared between code AND LLM prompts.

These constants are the single source of truth for status enums +
collection names + channel labels + ICP segments. Code AND prompts both
reference them. When you rename one, grep finds every site.

Notes:
- Collection names MUST stay in sync with ``mongo/schema.py`` (COLLECTIONS).
- Channel labels MUST match the ``AGENT_ROUTES`` table and the
  ``channel:`` frontmatter values used in ``skills/*/SKILL.md``.
- ICP segment ids MUST match ``mongo/data/customer_voice.py`` and
  ``mongo/data/messaging_library.py``.
- Status enums match the canonical strings persisted on Mongo docs:
  - ``skills[*].self_critique_proposal.status``  → AWAITING_HUMAN_REVIEW
                                                   → ACCEPTED / REJECTED_BY_GATE
  - ``skills[*].promotion_request.status``       → AWAITING_APPROVAL
  - ``messaging_library[*].status``              → APPROVED
"""
from __future__ import annotations


# Collection names — must match mongo/schema.py
class Coll:
    EXPERIMENTS = "experiments"
    SKILLS = "skills"
    CUSTOMER_VOICE = "customer_voice"
    MESSAGING_LIBRARY = "messaging_library"
    NEGATIVE_EXAMPLES = "negative_examples"
    APPROVALS = "approvals"
    ATTRIBUTION_MAP = "attribution_map"
    EMAIL_SEQUENCES = "email_sequences"
    PAID_VARIANTS = "paid_variants"
    OPS_INCIDENTS = "ops_incidents"
    POSITIONING_PROPOSALS = "positioning_proposals"
    # PRD-01
    AEO_AUDITS = "aeo_audits"
    AEO_CITATIONS = "aeo_citations"
    # PRD-02
    SIGNALS = "signals"
    SIGNAL_SOURCES = "signal_sources"
    # PRD-03
    PAID_ACTIONS_PROPOSED = "paid_actions_proposed"
    PAID_THRESHOLDS = "paid_thresholds"
    SELF_CRITIQUE_RUNS = "self_critique_runs"


# Status enums for self_critique / promotion / approvals
class Status:
    AWAITING_HUMAN_REVIEW = "awaiting_human_review"   # self_critique_proposal
    AWAITING_APPROVAL = "awaiting_approval"           # promotion_request
    ACCEPTED = "accepted"                              # self_critique flows
    REJECTED_BY_GATE = "rejected_by_gate"             # promotion_gate downgrade
    ESCALATED = "escalated"                            # proposal → promotion_request raised
    APPROVED = "approved"                              # founder approval (claims, etc.)


# Channels — must match the AGENT_ROUTES / SKILL.md frontmatter
class Channel:
    LINKEDIN = "linkedin"
    EMAIL = "email"
    SUBSTACK = "substack"
    BLOG = "blog"
    LIFECYCLE_EMAIL = "lifecycle_email"
    GOOGLE_ADS = "google_ads"
    META_ADS = "meta_ads"
    LINKEDIN_ADS = "linkedin_ads"


# ICP segments — must match mongo/data/customer_voice.py + messaging_library.py
class Icp:
    FOUNDER_B2B = "seg_founder_b2b"
    REVOPS_DIRECTOR = "seg_revops_director"
    AE_GROWTH = "seg_ae_growth"
    PMM_GROWTH = "seg_pmm_growth"
