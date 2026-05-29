"""AEO scoring + rewriting scripts.

Pure-Python utilities called by ``agents/aeo_agent.py`` (via FunctionTool
wrappers) to score and restructure drafts for citation by AI search
engines (ChatGPT, Perplexity, Google AI Overviews, Claude web search).

Modules:
  - ``content_quality``  — QRG-aligned filler / AI-pattern / repetition
    / info-density scoring. Adapted from
    https://github.com/AgriciDaniel/claude-seo (MIT).
  - ``parse_draft``      — markdown-first structure extractor (H1, H2
    list, paragraph segments, word count). Adapted from the upstream
    ``parse_html.py`` but markdown-native for our drafting pipeline.
  - ``passage_blocks``   — 134-167-word self-contained-block detector.
    The core AEO sub-signal segmenter. Reuses ``parse_draft``.
  - ``schema_generate``  — Article + Person + Organization JSON-LD
    emitter for the publish step.
  - ``log_citation``     — CLI to write rows into the ``aeo_citations``
    collection. Founder-facing during MVP 1; Perplexity API wires it
    up in MVP 2.

All four pure-Python scripts have no Mongo / network side effects so
they can be run on raw text in unit tests.
"""
