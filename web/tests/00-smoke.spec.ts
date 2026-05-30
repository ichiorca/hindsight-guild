/**
 * Smoke: every page loads without console errors + shows its header.
 *
 * If any of these fail, the rest of the suite won't tell us much — this
 * is the foundation. Run first.
 */
import { test, expect } from "@playwright/test";
import { navigate, expectNoConsoleErrors, expectPageHeader } from "./helpers/page-helpers";

const ROUTES: Array<[string, RegExp]> = [
  ["/queue",         /approval queue/i],
  ["/weekly-review", /weekly review/i],
  ["/draft",         /drafting/i],
  ["/agents",        /agents/i],
  ["/experiments",   /experiments/i],
  // Page title was renamed Skills → Playbooks as part of the business-language sweep.
  ["/skills",        /playbooks|skills/i],
  ["/capabilities",  /capabilities/i],
  ["/voice",         /customer voice/i],
  // Page title was renamed Telemetry → Quality Signals.
  ["/telemetry",     /quality signals|telemetry/i],
  ["/live",          /live ops/i],
];

for (const [path, headerText] of ROUTES) {
  test(`smoke: ${path} loads cleanly`, async ({ page }) => {
    const errors = await navigate(page, path);
    await expectPageHeader(page, headerText);
    expectNoConsoleErrors(errors);
  });
}

test("smoke: sidebar navigation between two pages preserves state", async ({ page }) => {
  // Open queue, click through to /agents via the nav. Ensures sidebar
  // links don't error out and the app shell holds its layout across navs.
  await page.goto("/queue");
  await page.waitForLoadState("networkidle");
  await page.getByRole("link", { name: /agents/i }).first().click();
  await expect(page).toHaveURL(/\/agents/);
  await expect(page.locator("h1, h2").filter({ hasText: /agents/i }).first())
    .toBeVisible();
});
