/**
 * UX feedback contract — the P0 trust fixes:
 *   - every queue decision produces a visible toast (no more silent success).
 *   - the Approval Queue surfaces a real error state, never a false
 *     "Inbox zero", when the API fails.
 */
import { test, expect } from "@playwright/test";
import { navigate } from "./helpers/page-helpers";
import { ensureQueueHas } from "./helpers/data-helpers";

test.describe.configure({ mode: "serial" });

test("queue: rejecting a draft shows a confirmation toast", async ({ page, request }) => {
  await ensureQueueHas(request, 1);
  await navigate(page, "/queue");

  await page.getByRole("button", { name: /^reject/i }).first().click();
  // Pick a reason, then confirm.
  const reasonSelect = page.locator("select").last();
  await reasonSelect.evaluate((sel: HTMLSelectElement) => {
    if (sel.options.length > 0) {
      sel.value = sel.options[0].value;
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });
  await page.getByRole("button", { name: /confirm reject/i }).first().click();

  // A toast (role=status) confirms the action — this is the feedback that
  // used to be entirely absent.
  await expect(
    page.getByRole("status").filter({ hasText: /rejected/i }).first(),
  ).toBeVisible({ timeout: 6_000 });
});

test("queue: a failed queue fetch shows an error state, not 'Inbox zero'", async ({ page }) => {
  // Force the queue endpoint to fail so we exercise the error branch.
  await page.route("**/api/queue**", (route) =>
    route.fulfill({ status: 500, contentType: "application/json", body: "{}" }),
  );
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

  await page.goto("/queue");
  await page.waitForLoadState("networkidle");

  // The honest error card appears…
  await expect(page.getByText(/couldn't load the approval queue/i)).toBeVisible({ timeout: 10_000 });
  // …and the dishonest "Inbox zero." empty state does NOT.
  await expect(page.getByText(/inbox zero/i)).toHaveCount(0);
  // A retry affordance is offered.
  await expect(page.getByRole("button", { name: /try again/i })).toBeVisible();
});
