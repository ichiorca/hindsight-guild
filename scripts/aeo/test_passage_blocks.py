"""Tests for the passage-block detector.

Covers the rules from skills/aeo/references/passage-blocks.md:
  - 134-167-word window for non-listicle.
  - 80-120-word window for listicle.
  - Backward-reference / forward-reference / transitional-opener
    penalties.
  - Floating-number penalty (number without inline attribution).
  - Per-H2-section aggregation.
"""
from __future__ import annotations

from scripts.aeo import passage_blocks


# Generate a paragraph with a target word count, padded with
# information-dense filler so content_quality wouldn't trip flags.
def _para(n_words: int, prefix: str = "Our system records") -> str:
    base = (prefix + " calibrated outcomes from active experiments tagged "
            "to ICP segments across owned channels. The dataset comes from "
            "Mongo's actions collection joined to the attribution_map and "
            "filtered to approved drafts only. Reviewers cross-check claims "
            "against the messaging_library and customer_voice quotes before "
            "the eval scorer touches a draft. The result is a stable view "
            "of which playbooks moved the metric across consecutive weeks "
            "without resorting to hand-tuned per-channel hacks or stale "
            "snapshots.")
    words = base.split()
    while len(words) < n_words:
        words += words
    return " ".join(words[:n_words])


def test_block_in_target_range_qualifies():
    md = "# Title\n\n## How does the loop work?\n\n" + _para(150) + "\n"
    out = passage_blocks.detect_blocks(md)
    assert out["target_min"] == 134
    assert out["target_max"] == 167
    assert out["n_h2_sections"] == 1
    block = out["h2_sections"][0]["blocks"][0]
    assert block["in_target_range"], block
    assert block["qualifies"], block
    assert out["self_contained_blocks_signal"] == 1.0


def test_block_too_short_does_not_qualify():
    md = "# Title\n\n## How does the loop work?\n\n" + _para(40) + "\n"
    out = passage_blocks.detect_blocks(md)
    block = out["h2_sections"][0]["blocks"][0]
    assert not block["in_target_range"]
    assert not block["qualifies"]
    assert out["self_contained_blocks_signal"] == 0.0


def test_block_too_long_does_not_qualify():
    md = "# Title\n\n## How does the loop work?\n\n" + _para(300) + "\n"
    out = passage_blocks.detect_blocks(md)
    block = out["h2_sections"][0]["blocks"][0]
    assert not block["in_target_range"]


def test_backward_reference_lowers_self_containment():
    body = _para(150) + " As we discussed above, the loop is stable."
    md = "# T\n\n## Q\n\n" + body + "\n"
    out = passage_blocks.detect_blocks(md)
    block = out["h2_sections"][0]["blocks"][0]
    assert block["self_contained_score"] < 1.0
    # 0.6 (backward) * (1.0 if no other hits, but the text also contains a
    # number-free "above" reference which is the backward marker)
    assert block["self_contained_score"] <= 0.6


def test_forward_reference_lowers_self_containment():
    body = _para(150) + " We will see the chart below in the next section."
    md = "# T\n\n## Q\n\n" + body + "\n"
    out = passage_blocks.detect_blocks(md)
    block = out["h2_sections"][0]["blocks"][0]
    # "below" + "next section" both match forward + "we will see" matches
    # forward too; multiplicative penalty stacks but caps via min 0.
    assert block["self_contained_score"] <= 0.6


def test_floating_number_no_attribution_penalty():
    body = ("The renewal slip across the CSM handoff costs "
            + "23 percent of expected revenue in the median enterprise org. "
            + _para(120))
    md = "# T\n\n## Q\n\n" + body + "\n"
    out = passage_blocks.detect_blocks(md)
    block = out["h2_sections"][0]["blocks"][0]
    # has number, no inline source -> multiplied by 0.7
    assert block["self_contained_score"] <= 0.7


def test_attributed_number_not_penalised():
    body = (_para(120) + " Per Ahrefs December 2025 study, 23% of brand "
            "mentions correlate with AI citation lift.")
    md = "# T\n\n## Q\n\n" + body + "\n"
    out = passage_blocks.detect_blocks(md)
    block = out["h2_sections"][0]["blocks"][0]
    # Has number but also has attribution — no floating-number penalty.
    assert block["self_contained_score"] == 1.0


def test_section_signal_fraction_of_qualifying_h2s():
    # 2 H2s; one with qualifying block, one without.
    md = (
        "# T\n\n"
        "## First section\n\n" + _para(150) + "\n\n"
        "## Second section\n\n" + _para(40) + "\n"
    )
    out = passage_blocks.detect_blocks(md)
    assert out["n_h2_sections"] == 2
    assert out["n_qualifying_sections"] == 1
    assert out["self_contained_blocks_signal"] == 0.5


def test_listicle_uses_shorter_window():
    md = (
        "# Five takes\n\n"
        "## Why\n\n"
        "- Take one: " + _para(95, prefix="One observation is") + "\n"
        "- Take two: " + _para(95, prefix="Two observations are") + "\n"
        "- Take three: " + _para(95, prefix="Three observations are") + "\n"
    )
    out = passage_blocks.detect_blocks(md, page_type_hint="listicle")
    assert out["target_min"] == 80
    assert out["target_max"] == 120


def test_empty_body_zero_signal():
    out = passage_blocks.detect_blocks("")
    assert out["self_contained_blocks_signal"] == 0.0
    assert out["n_h2_sections"] == 0


def test_body_without_h2_falls_back_to_whole_body():
    md = "# Title only\n\n" + _para(150) + "\n"
    out = passage_blocks.detect_blocks(md)
    assert out["n_h2_sections"] == 0
    # The intro block qualifies → signal 1.0.
    assert out["self_contained_blocks_signal"] == 1.0
