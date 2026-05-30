/**
 * Telemetry page: brand-voice trend, model armor activity, negative-example library.
 *
 * In LOCAL_DEV the rubric_trend endpoint returns [] (Vertex Eval needs
 * ADC). The page should NOT show a blank chart — it should show the
 * "Get started" guidance card we added.
 */
import { test, expect } from "@playwright/test";
import { navigate, expectNoConsoleErrors } from "./helpers/page-helpers";

test("telemetry: page loads + renders the three primary cards", async ({ page }) => {
  const errors = await navigate(page, "/telemetry");

  // Three cards: brand voice trend / Safety filter activity / Do-not-repeat library.
  // .first() guards against strict-mode failures when card titles appear
  // both in the card header and the page table-of-contents.
  await expect(page.getByText(/brand voice trend/i).first()).toBeVisible();
  await expect(page.getByText(/safety filter|model armor/i).first()).toBeVisible();
  await expect(page.getByText(/do-not-repeat|negative-example/i).first()).toBeVisible();

  expectNoConsoleErrors(errors);
});

test("telemetry: brand-voice empty state shows TelemetryGetStarted guidance", async ({ page, request }) => {
  const trend = await (await request.get("/api/rubric-trend?days=28")).json();

  if (trend.length === 0) {
    // Empty path — guidance card should be visible.
    await navigate(page, "/telemetry");
    await expect(page.getByText(/here's how this page is fed/i)).toBeVisible();
    await expect(page.getByText(/generate drafts/i)).toBeVisible();
  } else {
    // Populated path — chart legend should be visible.
    await navigate(page, "/telemetry");
    await expect(page.locator(".recharts-legend-wrapper")).toBeVisible();
  }
});

test("telemetry: model armor card shows total + block count", async ({ page }) => {
  await navigate(page, "/telemetry");
  await expect(page.getByText(/total actions/i)).toBeVisible();
  await expect(page.getByText(/blocks/i).first()).toBeVisible();
});
