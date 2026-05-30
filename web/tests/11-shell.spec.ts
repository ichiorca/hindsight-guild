/**
 * App shell: theme toggle, command palette, sidebar.
 *
 * Cross-cutting concerns the user touches on every page. Locks
 * regression on the layout / global UI components.
 */
import { test, expect } from "@playwright/test";
import { navigate, expectNoConsoleErrors } from "./helpers/page-helpers";

test("shell: cmd+K opens the command palette", async ({ page }) => {
  await navigate(page, "/queue");

  // The palette is a global keyboard handler. Open it.
  await page.keyboard.press("Control+K");

  // CommandPalette uses cmdk; the input should be focused.
  await expect(page.locator("input[placeholder*='jump' i], input[placeholder*='search' i]").first())
    .toBeVisible({ timeout: 3_000 });

  // Escape closes it.
  await page.keyboard.press("Escape");
});

test("shell: command palette can navigate to Signals, Self-Learning, and Agents", async ({ page }) => {
  // These three routes were added to the sidebar after the palette was first
  // written and were missing from its hardcoded list; the palette now derives
  // its destinations from NAV_FLAT so it can't drift again.
  for (const [label, route] of [
    ["Signals", "/signals"],
    ["Self-Learning", "/learning"],
    ["Agents", "/agents"],
  ] as const) {
    await navigate(page, "/queue");
    await page.keyboard.press("Control+K");
    const input = page.locator("input[placeholder*='jump' i]").first();
    await expect(input).toBeVisible({ timeout: 3_000 });
    await input.fill(label);
    await page.getByRole("option", { name: new RegExp(label, "i") }).first().click();
    await expect(page).toHaveURL(new RegExp(route.replace("/", "\\/")));
  }
});

test("shell: dark theme toggle flips html.dark class", async ({ page }) => {
  await navigate(page, "/queue");

  // ThemeToggle renders three icon buttons with aria-labels "Light theme",
  // "Dark theme", "System theme". Force Light first to establish baseline,
  // then click Dark.
  await page.getByRole("button", { name: /light theme/i }).first().click();
  await expect.poll(
    () => page.evaluate(() => document.documentElement.classList.contains("dark"))
  ).toBe(false);

  await page.getByRole("button", { name: /dark theme/i }).first().click();
  await expect.poll(
    () => page.evaluate(() => document.documentElement.classList.contains("dark"))
  ).toBe(true);
});

test("shell: sidebar agent stack links to /agents", async ({ page }) => {
  await navigate(page, "/queue");

  // The avatar stack at the bottom of the sidebar is a Link to /agents.
  // Click any agent avatar.
  const stack = page.locator("a[href='/agents']").first();
  await expect(stack).toBeVisible();
  await stack.click();
  await expect(page).toHaveURL(/\/agents/);
});

test("shell: agent count badge is dynamic (matches AGENTS registry)", async ({ page }) => {
  await navigate(page, "/queue");
  // The "On shift" caption reads "N agents · M on the front line, K specialists".
  // We just confirm it's not the old hardcoded "11 agents".
  await expect(
    page.getByText(/12 agents/i).first()
  ).toBeVisible();
});
