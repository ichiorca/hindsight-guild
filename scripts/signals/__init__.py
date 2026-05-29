"""PRD-02 source adapters.

Each adapter is a pure module exporting two functions:

  - ``poll(source_doc: dict, mongo_db) -> list[dict]`` - poll the source,
    return list of raw event dicts (NOT scored yet; the watcher applies
    ICP-regex scoring on top). Updates ``source_doc['cursor']`` in place;
    the watcher persists it.
  - ``base_score(raw: dict) -> float`` - source-specific base score
    (0.0..1.0) before the ICP-fit boost.

Three v1 adapters: ``hn_adapter``, ``reddit_adapter``, ``rss_adapter``.
All three target FREE public endpoints (HN Algolia, Reddit JSON, RSS)
so MVP 1 doesn't depend on paid intent providers.
"""
