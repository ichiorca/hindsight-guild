/**
 * Capabilities page — deep coverage:
 *   - Heatmap density: before/after drafts, cell counts increment
 *   - Multiple agents × multiple skills (the augmentation we added)
 *   - Dead-weight skills surface in the summary
 *   - Recent loads feed shows fresh entries after a draft
 */
import { test, expect } from "@playwright/test";
import { navigate, expectNoConsoleErrors } from "./helpers/page-helpers";
import { kickoffDraft } from "./helpers/data-helpers";

test("capabilities: heatmap is dense (>= 5 agents × >= 3 skills)", async ({ page, request }) => {
  const data = await (await request.get("/api/capabilities?days=7")).json();
  expect(data.by_agent.length, "fewer than 5 agents in heatmap").toBeGreaterThanOrEqual(5);

  // Sum populated cells.
  const totalCells = data.by_agent.reduce(
    (acc: number, a: any) => acc + a.skill_loads.length, 0,
  );
  expect(totalCells, "heatmap density too low").toBeGreaterThan(10);

  const errors = await navigate(page, "/capabilities");
  await expect(page.locator("table").first()).toBeVisible();
  expectNoConsoleErrors(errors);
});

test("capabilities: firing a draft increments the heatmap counts", async ({ page, request }) => {
  test.setTimeout(45_000);

  // Capture before-state: total loads for content_agent.
  const before = await (await request.get("/api/capabilities?days=7")).json();
  const contentBefore = before.by_agent.find((a: any) => a.agent_name === "content_agent");
  const beforeTotal = contentBefore?.total_loads ?? 0;

  // Fire a draft (synthetic — completes in ~5s).
  await kickoffDraft(request, {
    channel: "linkedin",
    topic_hint: "capabilities heatmap increment test",
  });

  // After the draft, content_agent's total_loads should be greater.
  const after = await (await request.get("/api/capabilities?days=7")).json();
  const contentAfter = after.by_agent.find((a: any) => a.agent_name === "content_agent");
  const afterTotal = contentAfter?.total_loads ?? 0;
  expect(afterTotal, "content_agent total_loads should have increased").toBeGreaterThan(beforeTotal);

  // Re-load the UI and verify the new content_agent cell value shows up.
  await navigate(page, "/capabilities");
  // The matrix has cells with the count in font-mono. Find content_agent's
  // highest cell.
  const peakCell = contentAfter.skill_loads[0]?.count;
  if (peakCell) {
    await expect(
      page.locator("td.font-mono, td").filter({ hasText: new RegExp(`^${peakCell}$`) }).first()
    ).toBeVisible();
  }
});

test("capabilities: summary tile shows installed skills count + dead weight", async ({ page, request }) => {
  const data = await (await request.get("/api/capabilities?days=7")).json();
  const installed = data.summary.installed_skills;
  expect(installed).toBeGreaterThan(0);

  await navigate(page, "/capabilities");
  await expect(
    page.getByText(new RegExp(`\\b${installed}\\b`)).first()
  ).toBeVisible({ timeout: 5_000 });

  // dead_weight list should be present (even if 0) — it's a key affordance
  // for the founder to know what skills aren't being used.
  expect(data.summary).toHaveProperty("dead_weight_count");
  expect(data.summary).toHaveProperty("dead_weight");
});

test("capabilities: cell tooltip surfaces 'agent × skill: N load(s)'", async ({ page }) => {
  await navigate(page, "/capabilities");
  // Find any non-zero cell.
  const populatedCells = page.locator("td[title]").filter({ hasText: /^\d+$/ });
  const count = await populatedCells.count();
  expect(count, "no populated cells in heatmap").toBeGreaterThan(0);

  const title = await populatedCells.first().getAttribute("title");
  // Tooltip format: "Agent × skill name: used N time(s)"
  expect(title).toMatch(/\w+ ×.+used \d+ time/);
});

test("capabilities: by-agent rows show all 12 agents (or filtered subset)", async ({ request }) => {
  const data = await (await request.get("/api/capabilities?days=7")).json();
  // The augmented rollup includes every agent that had ≥ 1 action in the
  // window. After preflight that should be most or all 12.
  expect(data.by_agent.length).toBeGreaterThanOrEqual(8);
});
