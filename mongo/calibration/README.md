# Calibration sets (in GCS, not in Mongo)

Rubric calibration sets live in GCS at:

```
gs://${PROJECT_ID}-evals/calibration/
  brand_voice.jsonl
  claim_support.jsonl
  claim_risk.jsonl
  icp_relevance.jsonl
  originality.jsonl
  conversion_intent.jsonl
```

Each JSONL line is:

```jsonl
{"input": {"candidate_text": "..."}, "human_score": 5, "rationale": "..."}
```

20-50 examples per rubric is enough for hackathon. Re-grade quarterly.

The calibration sets are populated automatically by `demo/seed_demo.py` —
they appear in GCS within seconds of running it.
