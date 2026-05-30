/**
 * Shared helpers for Playwright tests.
 *
 * Encapsulates the common patterns: navigate-and-wait, capture console
 * errors (any uncaught JS error → test failure), wait for data to load
 * past loading skeletons.
 */
import { expect, Page } from "@playwright/test";

/**
 * Navigate to a route and wait until network is idle (TanStack Query
 * has finished its initial fetch). Also attaches a console-error listener
 * that surfaces any uncaught JS error as a test failure — silent UI
 * crashes ("nothing happens when I click X") are the most common
 * customer-visible failure mode we want to catch.
 */
export async function navigate(page: Page, path: string): Promise<string[]> {
  const consoleErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      consoleErrors.push(msg.text());
    }
  });
  page.on("pageerror", (err) => {
    consoleErrors.push(`pageerror: ${err.message}`);
  });
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  return consoleErrors;
}

/**
 * Assert no uncaught JS errors landed in the console while interacting
 * with the page. Filters known noise (e.g., dev-mode HMR warnings) that
 * isn't actionable for the test.
 */
export function expectNoConsoleErrors(errors: string[]) {
  const noise = [
    /Download the React DevTools/i,
    /\[vite\]/i,
    /react-router-dom/i,
    /HMR/i,
    // /media file loads via http proxy — the broken-endpoint test target
    // we seeded intentionally 404s; that's fixture noise, not a bug.
    /this-endpoint-does-not-exist-xyz/i,
  ];
  const real = errors.filter((e) => !noise.some((rx) => rx.test(e)));
  if (real.length > 0) {
    throw new Error(
      `Page emitted ${real.length} console error(s):\n  ${real.join("\n  ")}`,
    );
  }
}

/**
 * Wait for any "Loading…" skeleton to disappear. Used right after
 * navigate() before assertions on data-bearing elements.
 */
export async function waitForLoaded(page: Page) {
  // Skeleton components in this app render as divs with class containing
  // ``animate-pulse``. We just wait for them to be gone.
  await page.waitForFunction(
    () => document.querySelectorAll(".animate-pulse").length === 0,
    null,
    { timeout: 10_000 },
  ).catch(() => {
    // Some pages don't have skeletons; that's fine.
  });
}

/**
 * Resolve to the first card in the queue (Approval Queue page). Returns
 * null when the queue is empty — caller decides whether that's a fail
 * or an acceptable state.
 */
export async function firstQueueCard(page: Page) {
  const card = page.locator('[data-testid="queue-item"]').first();
  if (await card.count() === 0) return null;
  return card;
}

/**
 * Assert the page rendered its header (eyebrow + title). Almost every
 * route uses the same PageHeader component, so this catches the case
 * where the route mounted but the shell didn't render.
 */
export async function expectPageHeader(page: Page, title: string | RegExp) {
  await expect(page.locator("h1, h2").filter({ hasText: title }).first())
    .toBeVisible();
}
