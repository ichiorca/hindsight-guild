# Approval Sheet + Apps Script

## Sheet setup (manual)

1. Create a new Google Sheet titled "Agentic Marketing — Approvals".
2. Rename the first tab to **`approvals`**.
3. Row 1 (headers) — exactly these names, in this order:

```
telemetry_id | original_draft | approved_text | decision | rejection_reason | decided_by | decided_at | synced
```

4. **Data validation** on the `decision` column: dropdown with values
   `approve`, `edit`, `reject`. Reject invalid input.
5. **Conditional formatting** (optional): color rows by decision so the founder
   can scan the queue.

## Apps Script setup

1. Sheet → Extensions → Apps Script.
2. Paste `Code.gs` into the editor.
3. Replace `HANDLER_URL` with the deployed Cloud Run service URL
   (`gcloud run services describe edit-capture-handler --format='value(status.url)'`).
4. Run `setupTrigger()` once. Authorize the script when prompted.
5. Verify in **Triggers** (clock icon) that a 5-min time-driven trigger exists.

## Test

1. Manually insert a row with a fake `telemetry_id`, `original_draft=...`,
   `decision=reject`, `rejection_reason=Test`.
2. Run `syncRows()` manually in the script editor (or wait 5 min).
3. Verify:
   - `synced` column flips to `yes`.
   - A row appears in BigQuery `training.edits`.
   - A document appears in MongoDB `negative_examples` with `_id=neg_<telemetry_id>`.
   - A document appears in MongoDB `approvals`.
