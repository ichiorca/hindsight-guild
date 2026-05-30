/**
 * Experiments page: running / decided / drift sections.
 *
 * Empty state is the most common case in LOCAL_DEV (the agents haven't
 * authored any experiments yet). Lock that the "how this list fills"
 * guidance card is shown when each section is empty.
 */
import { test, expect } from "@playwright/test";
import { navigate, expectNoConsoleErrors } from "./helpers/page-helpers";

test("experiments: page loads + shows the 'how experiments enter' guidance", async ({ page }) => {
  const errors = await navigate(page, "/experiments");

  await expect(page.locator("h1, h2").filter({ hasText: /experiments/i }).first())
    .toBeVisible();

  // Static guidance card mentions the CMO agent.
  await expect(page.getByText(/cmo agent|cmo planner/i).first()).toBeVisible();
  // Guidance card uses business-language now ("Drift detection",
  // "Outcome attribution") rather than the raw backend job names.
  await expect(
    page.getByText(/drift detection|outcome attribution|drift_detect|outcome_attach/i).first()
  ).toBeVisible();

  expectNoConsoleErrors(errors);
});

test("experiments: each of the 3 sections (Running / Drift / Decided) renders", async ({ page }) => {
  await navigate(page, "/experiments");

  for (const heading of [/^running$/i, /drift investigations/i, /recently decided/i]) {
    await expect(page.getByText(heading).first()).toBeVisible();
  }
});
