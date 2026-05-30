/**
 * End-to-end user journey tests — cross-page workflows that mirror what
 * the founder actually does in a working session. Higher-value than any
 * single-page test because they catch wiring bugs between pages.
 */
import { test, expect } from "@playwright/test";
import {
  kickoffDraft,
  ensureQueueHas,
  submitDecision,
  waitUntil,
} from "./helpers/data-helpers";

test("journey: generate draft → see in queue → ship → see in weekly review", async ({ page, request }) => {
  test.setTimeout(90_000);

  // 1. Drafting page — kick off a new pipeline draft via the form.
  await page.goto("/draft");
  await page.waitForLoadState("networkidle");

  await page.locator("input[placeholder*='handoff' i], input[placeholder*='topic' i]").first()
    .fill(`journey test — ${Date.now()}`);
  await page.getByRole("button", { name: /hand (off|it)/i }).first().click();

  // Result panel surfaces the draft body.
  await expect(page.getByText(/drafted this/i)).toBeVisible({ timeout: 60_000 });

  // 2. Navigate to Queue. The new draft should appear.
  // (synthetic drafts land in queue as actions w/ action_type=draft_<channel>).
  // It may take a moment for the queue API to reflect the new row.
  await waitUntil(async () => {
    const q = await (await request.get("/api/queue")).json();
    return q.length > 0;
  });

  await page.goto("/queue");
  await page.waitForLoadState("networkidle");

  const queueBefore = await (await request.get("/api/queue")).json();
  expect(queueBefore.length).toBeGreaterThan(0);
  const target = queueBefore[0];

  // 3. Ship it via the UI.
  const beforeApprovals = (
    await (await request.get("/api/this-week-summary")).json()
  ).approvals;

  await page.getByRole("button", { name: /ship it/i }).first().click();
  await waitUntil(async () => {
    const q = await (await request.get("/api/queue")).json();
    return !q.some((i: { telemetry_id: string }) => i.telemetry_id === target.telemetry_id);
  });

  // 4. Weekly Review reflects the new decision.
  await page.goto("/weekly-review");
  await waitUntil(async () => {
    const after = (await (await request.get("/api/this-week-summary")).json()).approvals;
    return after > beforeApprovals;
  });
});

test("journey: agent handoff from /agents → result in /draft via deep-link", async ({ page, request }) => {
  test.setTimeout(60_000);

  await page.goto("/agents");
  await page.waitForLoadState("networkidle");

  // Find Positioning card + open it
  const card = page.locator("button").filter({
    has: page.locator("h3").filter({ hasText: /positioning/i }),
  }).first();
  await card.click();

  // Send via the QuickHandoff form
  await page.locator("input[placeholder*='angle' i], input[placeholder*='claim' i]").first()
    .fill("journey: cross-page positioning push");
  await page.getByRole("button", { name: /send to/i }).first().click();

  // Success card appears with link
  await expect(page.getByText(/task handed off to/i)).toBeVisible({ timeout: 30_000 });
  const openLink = page.getByRole("link", { name: /open in drafting/i });
  await openLink.click();

  // Now on /draft?agent=positioning_agent
  await expect(page).toHaveURL(/\/draft\?agent=positioning_agent/);
  const routeSel = page.locator("label").filter({ hasText: /route to/i })
    .locator("..").locator("select");
  await expect(routeSel).toHaveValue("positioning_agent");
});

test("journey: reject in queue → negative_example surfaces on /telemetry", async ({ page, request }) => {
  test.setTimeout(60_000);

  await ensureQueueHas(request, 1);
  const target = (await (await request.get("/api/queue")).json())[0];
  // /api/negatives caps at limit=50 by default — once the suite has
  // accumulated >50 negative_examples rows the cap saturates and length
  // comparisons stop working. Use a wider window so a new insert reliably
  // bumps the count.
  const beforeNeg = (await (await request.get("/api/negatives?limit=500")).json()).length;

  // Reject via the API to keep the test focused on the cross-page wiring
  // (queue-action UI is covered in 01-queue.spec.ts).
  await submitDecision(request, {
    telemetry_id: target.telemetry_id,
    decision: "reject",
    original_draft: target.draft_text ?? "",
    rejection_reason: "Absolute claim — unsupportable",
    channel: target.channel,
  });

  await waitUntil(async () => {
    const after = (await (await request.get("/api/negatives?limit=500")).json()).length;
    return after > beforeNeg;
  }, 20_000);

  await page.goto("/telemetry");
  await page.waitForLoadState("networkidle");

  // The Negative-example library card on /telemetry calls useNegatives()
  // which hits /api/negatives without a limit — so it sees the default
  // cap of 50. Compare against THAT shape, not the wider window we used
  // for the existence assertion above.
  const cappedNeg = (await (await request.get("/api/negatives")).json()).length;
  await expect(
    page.getByText(new RegExp(`\\b${cappedNeg}\\b`)).first()
  ).toBeVisible({ timeout: 5_000 });
});

test("journey: drafting handoff → action appears in /live ops feed", async ({ page, request }) => {
  test.setTimeout(60_000);

  // Kick off a handoff. The action gets emitted to Mongo `actions`
  // (LOCAL_DEV mirror) which /api/live reads.
  await kickoffDraft(request, {
    agent_id: "positioning_agent",
    topic_hint: `live-ops journey ${Date.now()}`,
  });

  await page.goto("/live");
  await page.waitForLoadState("networkidle");

  // The live page lists recent actions. positioning_agent should appear
  // somewhere in the recent activity.
  await expect(page.getByText(/positioning/i).first()).toBeVisible({ timeout: 8_000 });
});

test("journey: capabilities heatmap reflects drafts in real time", async ({ page, request }) => {
  test.setTimeout(60_000);

  const before = await (await request.get("/api/capabilities?days=7")).json();
  const contentBefore = before.by_agent.find(
    (a: any) => a.agent_name === "content_agent",
  )?.total_loads ?? 0;

  // Fire several drafts to bump the count.
  for (let i = 0; i < 2; i++) {
    await kickoffDraft(request, {
      channel: "linkedin",
      topic_hint: `journey heatmap test #${i}`,
    });
  }

  const after = await (await request.get("/api/capabilities?days=7")).json();
  const contentAfter = after.by_agent.find(
    (a: any) => a.agent_name === "content_agent",
  )?.total_loads ?? 0;

  expect(contentAfter).toBeGreaterThan(contentBefore);

  // The UI should reflect the new count.
  await page.goto("/capabilities");
  await page.waitForLoadState("networkidle");
  // total_loads_period summary tile shows somewhere.
  const totalNow = after.summary.loads_period;
  await expect(
    page.getByText(new RegExp(`\\b${totalNow}\\b`)).first()
  ).toBeVisible({ timeout: 5_000 });
});

test("journey: live ticker WS receives push when job is in flight", async ({ page, request, baseURL }) => {
  test.setTimeout(45_000);
  // WS push only happens where the socket can connect (local dev). On the
  // deployed Firebase origin the ticker polls /api/live/now instead — there is
  // no WS to receive frames on, by design.
  test.skip(
    !baseURL?.includes("localhost") && !baseURL?.includes("127.0.0.1"),
    "WS transport is local-only; the deployed ticker polls /api/live/now",
  );

  // Open the queue page so the LiveTicker (mounted in Layout) opens
  // its WebSocket.
  await page.goto("/queue");

  // Capture WS frames received in the next 10s.
  const framesReceived: string[] = [];
  page.on("websocket", (ws) => {
    ws.on("framereceived", (data) => {
      const payload = typeof data.payload === "string" ? data.payload
        : data.payload.toString();
      framesReceived.push(payload);
    });
  });
  // Allow WS to connect.
  await page.waitForTimeout(2_000);

  // Kick off a job via the API (don't await — we want it in-flight).
  request.post("/api/draft", {
    data: {
      icp_segment: "seg_founder_b2b",
      channel: "linkedin",
      topic_hint: "ws ticker test",
      agent_id: "positioning_agent",
    },
  });

  // The broadcaster should push at least one frame within 6s.
  await page.waitForTimeout(6_000);
  expect(framesReceived.length, "expected at least one WS push").toBeGreaterThan(0);

  // Frames should be JSON with the live-now shape.
  const parsed = framesReceived.map((f) => {
    try { return JSON.parse(f); } catch { return null; }
  }).filter((p) => p !== null);
  expect(parsed.length).toBeGreaterThan(0);
  expect(parsed[0]).toHaveProperty("active_jobs");
});
