/**
 * Self-Learning page — end-to-end coverage.
 *
 * What this spec verifies:
 *   - GET  /api/self-critique/summary             shape + `days` window
 *   - GET  /api/self-critique/summary?days=N      monotonic miner_runs window
 *   - The summary aggregates miner_run events after a fresh run-now
 *   - /learning page renders without console errors
 *   - KPI band shows all 5 tile labels
 *   - "The loop" 4-stage visual renders
 *   - "Run miners now" button fires /api/self-critique/run-now
 *   - Per-miner table renders all 5 standard miners when activity exists
 *   - Pending-proposals sidebar badge appears next to Weekly Review
 *     (when there's at least one pending proposal)
 *
 * Realism notes:
 *   - We trigger /api/self-critique/run-now to seed at least one
 *     self_critique_runs row before the page-level assertions, so the
 *     page is rendering real data rather than its empty state.
 *   - The pending-badge assertion is opportunistic: when no proposals
 *     are pending (clean test DB), the assertion is skipped rather than
 *     failed — same idiom the queue spec uses.
 */
import { test, expect } from "@playwright/test";
import { navigate, expectNoConsoleErrors } from "./helpers/page-helpers";
import { waitUntil } from "./helpers/data-helpers";

test.describe.configure({ mode: "serial" });

test("learning: GET /api/self-critique/summary returns well-formed shape", async ({ request }) => {
  // Seed at least one run row so per_miner counts are populated.
  await request.post("/api/self-critique/run-now");

  const r = await request.get("/api/self-critique/summary");
  expect(r.ok()).toBeTruthy();
  const body = await r.json();

  // Top-level keys
  for (const k of [
    "days", "this_window", "pending_proposals",
    "per_miner_28d", "events", "voice_delta",
  ]) {
    expect(body, `summary missing key ${k}`).toHaveProperty(k);
  }
  expect(body.days).toBe(7);

  // this_window sub-keys + types
  for (const k of [
    "proposals_emitted", "proposals_accepted",
    "proposals_dismissed", "promotions", "miner_runs",
  ]) {
    expect(body.this_window, `this_window missing ${k}`).toHaveProperty(k);
    expect(typeof body.this_window[k]).toBe("number");
  }

  // per_miner_28d shape — array; each row has the contract fields
  expect(Array.isArray(body.per_miner_28d)).toBeTruthy();
  expect(body.per_miner_28d.length).toBe(5);   // 5 standard miners
  for (const row of body.per_miner_28d) {
    for (const k of ["miner", "runs", "emitted", "accepted", "dismissed"]) {
      expect(row, `per_miner row missing ${k}`).toHaveProperty(k);
    }
    expect(typeof row.runs).toBe("number");
  }

  // All 5 standard miner names present
  const minerNames = body.per_miner_28d.map((r: { miner: string }) => r.miner);
  for (const m of ["voice", "negative", "paid", "aeo", "signal"]) {
    expect(minerNames, `miner ${m} absent from per_miner_28d`).toContain(m);
  }

  // events is an array (may be empty)
  expect(Array.isArray(body.events)).toBeTruthy();
});

test("learning: ?days param controls window monotonically", async ({ request }) => {
  // Two windows: 7d and 14d. miner_runs in 14d MUST be >= miner_runs in 7d
  // (because the larger window strictly contains the smaller).
  const [r7, r14] = await Promise.all([
    request.get("/api/self-critique/summary?days=7"),
    request.get("/api/self-critique/summary?days=14"),
  ]);
  const b7 = await r7.json();
  const b14 = await r14.json();
  expect(b7.days).toBe(7);
  expect(b14.days).toBe(14);
  expect(b14.this_window.miner_runs).toBeGreaterThanOrEqual(b7.this_window.miner_runs);
});

test("learning: events feed contains miner_run entries after run-now", async ({ request }) => {
  await request.post("/api/self-critique/run-now");
  const r = await request.get("/api/self-critique/summary?days=1");
  const body = await r.json();
  const minerRuns = body.events.filter(
    (e: { kind: string }) => e.kind === "miner_run",
  );
  expect(minerRuns.length, "no miner_run events in last 24h after run-now").toBeGreaterThan(0);
  // Each event has the contract fields
  for (const e of minerRuns) {
    expect(e).toHaveProperty("ts");
    expect(e).toHaveProperty("summary");
    expect(typeof e.summary).toBe("string");
    expect(e.summary.length).toBeGreaterThan(0);
  }
});

test("learning: /learning page renders without console errors", async ({ page }) => {
  const errors = await navigate(page, "/learning");
  await expect(
    page.getByRole("heading", { name: /self-learning/i }).first(),
  ).toBeVisible();
  // Description copy mentions "closed-loop" or "nightly miners" — verify
  // one of those is present so a future copy refactor doesn't quietly
  // gut the page.
  await expect(
    page.getByText(/nightly miners|closed.loop|closed loop|self.critique/i).first(),
  ).toBeVisible();
  expectNoConsoleErrors(errors);
});

test("learning: KPI band shows all 5 tile labels", async ({ page }) => {
  await navigate(page, "/learning");
  // Wait for data to settle.
  await expect(page.getByText(/this week/i).first()).toBeVisible();
  // Each tile label is uppercase tracking-wider text — match loosely.
  for (const labelRe of [
    /proposals emitted/i,
    /^accepted$/i,
    /^dismissed$/i,
    /promotions/i,
    /voice trend/i,
  ]) {
    await expect(
      page.getByText(labelRe).first(),
      `KPI tile ${labelRe} not visible`,
    ).toBeVisible();
  }
});

test("learning: 'The loop' visual shows all 4 stages", async ({ page }) => {
  await navigate(page, "/learning");
  await expect(page.getByText(/^the loop$/i).first()).toBeVisible();

  // Each stage has an uppercase short label.
  for (const stage of [/^miners$/i, /^proposals$/i, /^decisions$/i, /^promotions$/i]) {
    await expect(
      page.getByText(stage).first(),
      `loop stage ${stage} not visible`,
    ).toBeVisible();
  }
});

test("learning: 'Run miners now' button increments the run counter", async ({ page, request }) => {
  await navigate(page, "/learning");

  const before = await (await request.get("/api/self-critique/summary?days=1")).json();
  const beforeRuns = before.this_window.miner_runs;

  await page.getByRole("button", { name: /run miners now/i }).click();

  // Wait for the new self_critique_runs row to appear in the summary.
  await waitUntil(async () => {
    const r = await request.get("/api/self-critique/summary?days=1");
    const after = await r.json();
    return after.this_window.miner_runs > beforeRuns;
  }, 10_000);
});

test("learning: per-miner table renders all 5 standard miners when activity exists", async ({ page, request }) => {
  // Ensure activity in the 28-day window.
  await request.post("/api/self-critique/run-now");

  await navigate(page, "/learning");
  await expect(page.getByText(/per miner.*28/i).first()).toBeVisible();

  const summary = await (await request.get("/api/self-critique/summary")).json();
  const totalEmitted = summary.per_miner_28d.reduce(
    (a: number, r: { emitted: number }) => a + r.emitted, 0,
  );

  if (totalEmitted === 0) {
    test.info().annotations.push({
      type: "empty-state",
      description: "no miner emitted in 28d; table renders empty-hint instead",
    });
    // Empty-state copy is rendered when totalEmitted === 0.
    await expect(
      page.getByText(/no miner activity in the last 28/i).first(),
    ).toBeVisible();
    return;
  }

  // Table rows — each miner name should be visible.
  for (const m of ["voice", "negative", "paid", "aeo", "signal"]) {
    await expect(
      page.locator("td").filter({ hasText: new RegExp(`^${m}$`, "i") }).first(),
      `miner ${m} row not visible in per-miner table`,
    ).toBeVisible();
  }
});

test("learning: sidebar carries pending-proposals badge on Weekly Review when proposals exist", async ({ page, request }) => {
  const proposals = await (await request.get("/api/self-critique/proposals")).json();
  if (!Array.isArray(proposals) || proposals.length === 0) {
    test.info().annotations.push({
      type: "skip-reason",
      description: "no pending proposals — badge intentionally hidden",
    });
    return;
  }

  await navigate(page, "/learning");
  // The badge is inside the Weekly Review NavLink. Its title attribute
  // is "${n} pending"; that's the stable hook.
  const badge = page.locator(`[title="${proposals.length} pending"]`).first();
  await expect(badge).toBeVisible();
  await expect(badge).toHaveText(String(proposals.length));
});

test("learning: recent events list deep-links into /learning chips for fresh events", async ({ page, request }) => {
  // After a run-now, the summary has miner_run events in the last 24h.
  await request.post("/api/self-critique/run-now");
  await navigate(page, "/learning");

  await expect(page.getByText(/recent events/i).first()).toBeVisible();

  const r = await request.get("/api/self-critique/summary?days=1");
  const events = (await r.json()).events ?? [];
  if (events.length === 0) {
    // Empty-state guidance is the contract when there's no data.
    await expect(page.getByText(/no events yet/i).first()).toBeVisible();
    return;
  }

  // The first event's summary text should be visible somewhere on the page.
  const firstSummary = events[0].summary as string;
  const firstWords = firstSummary.split(/\s+/).slice(0, 2).join(" ");
  await expect(
    page.locator("p").filter({ hasText: firstWords }).first(),
  ).toBeVisible({ timeout: 8_000 });
});
