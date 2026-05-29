// C11 — Apps Script for the agent approval sheet.
// Trigger: time-driven, every 5 minutes (set via setupTrigger() once).
//
// Sheet schema (first tab 'approvals'):
//   telemetry_id | channel | original_draft | approved_text | decision
//   | rejection_reason | decided_by | decided_at | synced
//
// IMPORTANT: 'channel' is what tells the handler whether to fan out to the
// substack publisher and what value to stamp on the negative_examples doc.
// Without it, sheet-originated approvals can never publish to Substack and
// rubric grounding can't find the negative.
//
// HANDLER_URL is stored as a Script Property so the project doesn't need a
// code edit after each deploy. Set it once via:
//   File → Project properties → Script properties → HANDLER_URL = <url>

function getHandlerUrl_() {
  const props = PropertiesService.getScriptProperties();
  const url = props.getProperty('HANDLER_URL');
  if (!url) {
    throw new Error("Script property 'HANDLER_URL' not set. Configure it to "
      + "the edit-capture-handler Cloud Run URL (ending in /handle).");
  }
  return url;
}

function syncRows() {
  const sheet = SpreadsheetApp.getActive().getSheetByName('approvals');
  const range = sheet.getDataRange();
  const values = range.getValues();
  const header = values[0];
  const cols = Object.fromEntries(header.map((h, i) => [h, i]));
  const handlerUrl = getHandlerUrl_();

  for (let i = 1; i < values.length; i++) {
    const row = values[i];
    if (row[cols['decision']] && !row[cols['synced']]) {
      const payload = {
        telemetry_id: row[cols['telemetry_id']],
        channel: cols['channel'] !== undefined ? row[cols['channel']] : null,
        original_draft: row[cols['original_draft']],
        approved_text: row[cols['approved_text']],
        decision: row[cols['decision']],
        rejection_reason: row[cols['rejection_reason']],
        decided_by: row[cols['decided_by']] || Session.getActiveUser().getEmail(),
      };
      const response = UrlFetchApp.fetch(handlerUrl, {
        method: 'post',
        contentType: 'application/json',
        payload: JSON.stringify(payload),
        muteHttpExceptions: true,
      });
      if (response.getResponseCode() === 200) {
        sheet.getRange(i + 1, cols['synced'] + 1).setValue('yes');
        sheet.getRange(i + 1, cols['decided_at'] + 1).setValue(new Date().toISOString());
      } else {
        console.error('Sync failed:', response.getContentText());
      }
    }
  }
}

function setupTrigger() {
  // Run once to install the 5-min time-based trigger.
  ScriptApp.newTrigger('syncRows').timeBased().everyMinutes(5).create();
}
