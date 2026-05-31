"""Golden-set eval corpus — the regression anchor for the rubric harness.

A small, curated set of drafts whose quality is *unambiguous* to a human, each
labelled with the score band we expect from ``shared.rubrics`` on the named
rubrics. Bands are expressed in the normalized 0..1 space the harness returns
(raw 1-5 judge score / 5).

Two consumers:

  - ``tests/unit/test_eval_golden.py`` — runs cloud-free in CI. It validates the
    corpus is well-formed, that every rubric named here exists in
    ``rubrics.ALL_RUBRICS``, and that the harness plumbing (column wiring +
    1-5→0..1 normalization + quality floor) maps a judge's raw verdict into the
    expected band. This catches *harness-contract* regressions (a renamed
    rubric, broken normalization, a dropped column) without spending Vertex
    quota.

  - ``tests/integration/test_eval_golden_live.py`` — gated on
    ``INTEGRATION_TEST=1``. It runs the REAL Vertex AI Eval Service over this
    same corpus and asserts the live judge still lands each draft in its band.
    This is the *judge-drift* gate: if a rubric-prompt edit or a JUDGE_MODEL
    bump regresses quality scoring, it fails here.

Keep entries unambiguous. The bands are intentionally wide (a human would never
disagree about which side of the band a draft sits on); the point is to catch
regressions, not to micro-calibrate the judge.

To extend: add an entry with a clear-cut draft and only the rubric(s) it most
sharply exercises. Prefer adding a new failure mode over piling assertions onto
one draft.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Default ICP used when a draft doesn't target a specific persona.
_REVOPS = (
    "RevOps director at a 50-200 person B2B SaaS company; owns pipeline "
    "reporting, attribution, and the GTM tech stack; skeptical of hype, "
    "fluent in CAC/LTV, allergic to vague 'synergy' language."
)


@dataclass(frozen=True)
class GoldenDraft:
    id: str
    channel: str
    text: str
    # rubric_name -> (min_inclusive, max_inclusive) in normalized 0..1 space.
    expect: dict[str, tuple[float, float]]
    icp_description: str = _REVOPS
    note: str = ""
    # Whether this draft SHOULD clear a ship/quality floor (see
    # rubrics.passes_quality_floor). Good drafts pass; deliberately bad ones
    # must be held.
    should_pass_floor: bool = True
    tags: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# GOOD drafts — should score high; must clear the floor.
# ---------------------------------------------------------------------------

GOOD: list[GoldenDraft] = [
    GoldenDraft(
        id="good_linkedin_attribution",
        channel="linkedin",
        text=(
            "Most RevOps teams I talk to can't tie a single closed-won deal to "
            "the campaign that sourced it. Not because the data isn't there — "
            "because it lives in four tools that don't agree on what a 'lead' "
            "is.\n\n"
            "We pulled the attribution paths for 1,200 B2B deals last quarter. "
            "The median deal touched 7 channels before close; 3 of those never "
            "showed up in the CRM. That gap is where pipeline reporting quietly "
            "breaks.\n\n"
            "If your board deck and your CRM disagree on what drove revenue, "
            "the problem usually isn't the model — it's the plumbing. Curious "
            "how others here reconcile multi-touch paths across tools?"
        ),
        expect={
            "brand_voice": (0.6, 1.0),
            "claim_risk": (0.6, 1.0),
            "icp_relevance": (0.6, 1.0),
            "conversion_intent": (0.6, 1.0),
        },
        note="Concise, evidence-led, specific to RevOps, soft discussion CTA.",
        tags=("good", "exemplar"),
    ),
    GoldenDraft(
        id="good_email_nurture",
        channel="email",
        text=(
            "Subject: The attribution gap nobody puts on the board deck\n\n"
            "Hi {first_name},\n\n"
            "Quick one. When we audited pipeline reporting across 40 B2B teams, "
            "the same pattern showed up: the CRM credited the last form fill, "
            "and everything that warmed the buyer first went uncounted.\n\n"
            "We wrote up the three reconciliation steps that closed the gap "
            "for those teams — no rip-and-replace, just a way to make your "
            "tools agree on a lead.\n\n"
            "Want the two-page version? Reply 'send it' and it's yours.\n\n"
            "— The Hindsight team"
        ),
        expect={
            "brand_voice": (0.6, 1.0),
            "claim_risk": (0.6, 1.0),
            "conversion_intent": (0.6, 1.0),
        },
        note="Clear value, evidence-led, single soft CTA appropriate to email.",
        tags=("good", "exemplar"),
    ),
]


# ---------------------------------------------------------------------------
# BAD drafts — each sharply exercises ONE failure mode; must be held by floor.
# ---------------------------------------------------------------------------

BAD: list[GoldenDraft] = [
    GoldenDraft(
        id="bad_overclaim_legal_risk",
        channel="linkedin",
        text=(
            "We GUARANTEE you'll eliminate 100% of your pipeline leakage and "
            "become the #1 RevOps team in your industry overnight. Our platform "
            "is the only solution that completely removes all attribution error, "
            "forever. Results guaranteed or it's basically magic. Switch today "
            "and watch revenue double in 30 days — guaranteed."
        ),
        expect={
            "claim_risk": (0.0, 0.4),
            "claim_support": (0.0, 0.4),
        },
        note="Absolutes, guarantees, #1-ranking, unverifiable — legal/claim risk.",
        should_pass_floor=False,
        tags=("bad", "claim_risk"),
    ),
    GoldenDraft(
        id="bad_generic_no_icp",
        channel="linkedin",
        text=(
            "Marketing is very important for businesses today. In this digital "
            "world, companies need to leverage synergies to drive growth and "
            "unlock value. Our solution helps businesses of all sizes achieve "
            "their goals. Contact us to learn more about how we can help your "
            "business succeed in the modern marketplace."
        ),
        expect={
            "icp_relevance": (0.0, 0.4),
            "originality": (0.0, 0.45),
        },
        note="Generic boilerplate, no RevOps signal, recycled category language.",
        should_pass_floor=False,
        tags=("bad", "icp_relevance", "originality"),
    ),
    GoldenDraft(
        id="bad_hard_sell_cta_forest",
        channel="email",
        text=(
            "Subject: BUY NOW!!! Limited time!!!\n\n"
            "ACT FAST — this deal won't last! Click here to buy now! Sign up "
            "today! Book a demo right now! Don't wait — purchase immediately! "
            "Call us now! Email us now! Start your free trial AND buy the "
            "enterprise plan AND refer a friend TODAY! Hurry, offer ends soon!!!"
        ),
        expect={
            "conversion_intent": (0.0, 0.4),
            "brand_voice": (0.0, 0.45),
        },
        note="Hard sell, multi-CTA forest, all-caps urgency — off-brand.",
        should_pass_floor=False,
        tags=("bad", "conversion_intent"),
    ),
]


ALL_GOLDEN: list[GoldenDraft] = GOOD + BAD


def rubric_names_referenced() -> set[str]:
    """Every rubric name any golden entry asserts on."""
    names: set[str] = set()
    for g in ALL_GOLDEN:
        names.update(g.expect.keys())
    return names
