<!--
Adapted from claude-seo `skills/seo-content/SKILL.md` §"AI Content
Assessment" + §"AI Citation Readiness". The upstream skill is a 200+
line E-E-A-T audit; this file extracts the subset relevant to
drafting-time AI-citation checks. Full E-E-A-T scoring stays with our
existing `review_agent` (which evaluates brand_voice + claim_support +
claim_risk + icp_relevance + originality + conversion_intent rubrics).
Source basis: claude-seo v2.0.0 (MIT).
-->

# Content quality checks for AI citation

The AEO scorer runs these checks on the draft body via
`scripts/aeo/content_quality.py`. They identify drafts that would
fail Google's QRG §4.6.5 (scaled content abuse) and §4.6.6 (low-effort
main content), which is what gets de-cited from AI search surfaces.

This is **advisory** — it does not produce its own veto. The
`review_agent` is still the source of truth for ship/no-ship.

## Signals

### Filler-phrase density

Phrases that QRG flags as "little to no value":

```
in today's competitive market   leveraging the power of
in this article, we will        whether you're a beginner or
let's dive in                   it's important to note
the importance of               cannot be overstated
in conclusion                   to put it simply
revolutionize                   game-changer
unlock the potential            harness the power
the world of                    when it comes to
```

Threshold: ≥ 3 hits per 1000 tokens flags `filler` in the script
output. AI engines preferentially cite content with low filler.

### AI-pattern density

Phrases that appear disproportionately in LLM output and rarely in
human writing (the Wikipedia AI-Cleanup project's catalogue, MIT):

```
delve into             in the realm of           tapestry of
multifaceted           comprehensive overview     navigate the
landscape of           paradigm shift             holistic
synergy                seamlessly integrate       robust framework
empower                cutting-edge               state-of-the-art
```

Threshold: ≥ 5 hits per 1000 tokens flags `ai-patterns`. AI engines
trained on LLM output are starting to **down-weight** these phrases as
"AI-feeling".

### Information density

Defined as `(named_entities + numeric_claims) / token_count`. Below
0.05 flags `low-density` — the draft has lots of words but little
information.

### Repetition

If the same 5-token n-gram appears ≥ 3 times in a 500-token window,
flags `repetitive`. Common cause: the LLM started cycling on a theme.

## Composite

`overall_quality` (0-100) is the composite the script returns.
Heuristic weights:

```
overall_quality =
    25 if filler_score    < 30   else 0
  + 25 if ai_pattern_score < 30  else 0
  + 25 if information_density > 0.08 else 0
  + 25 if repetition_score < 20  else 0
```

Drafts under 60 overall_quality should NOT be cited by AI engines —
not because they're "wrong", but because the structural quality
signals AI ranking systems look for are absent.

## What the scorer does

Calls `content_quality(text)` from `scripts/aeo/content_quality.py`.
The result feeds the rationale for `answer_first` and
`definition_patterns` sub-signals (low information_density → both
sub-signals likely low). The script is **not** a direct sub-signal —
it's a diagnostic.

## What the reviser does

If `flags` contains `ai-patterns` or `filler`, the reviser MAY rewrite
opening sentences to strip the matched phrases. The reviser MUST:

1. Preserve every fact and claim.
2. Keep the rewritten sentence under the original word count + 10%.
3. Re-run `content_quality` on the rewrite; if the flags don't clear
   in one pass, leave the original and record
   `strip_ai_patterns: insufficient` in `rewrites`.

## What's NOT in scope here

- **Full E-E-A-T scoring** stays with `review_agent`.
- **Schema validation** lives in `schema-for-ai.md`.
- **Word count guidance.** Google has confirmed word count is not a
  ranking factor; the AEO scorer does not target it. The
  134-167-word block rule in `passage-blocks.md` is about *citation
  passage length*, not draft length.

## Attribution

The phrase lists above are derived from the open-source
`content_quality.py` script in claude-seo (MIT), which in turn drew
from the Wikipedia "AI Cleanup" project's catalogue of LLM-typical
phrasings (CC BY-SA 4.0). Both upstreams are credited in the script
docstring.
