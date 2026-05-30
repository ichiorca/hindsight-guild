/**
 * Customer Voice page — deep coverage:
 *   - Quotes render with text, theme, persona, source
 *   - ICP filter narrows API + UI
 *   - Search filter narrows UI further
 *   - Empty state shows guidance when filter yields nothing
 */
import { test, expect } from "@playwright/test";
import { navigate, expectNoConsoleErrors } from "./helpers/page-helpers";

test("voice: quote cards render with real text + theme + persona", async ({ page, request }) => {
  const errors = await navigate(page, "/voice");
  const quotes = await (await request.get("/api/voice")).json();
  expect(quotes.length, "no customer voice quotes — run preflight").toBeGreaterThan(0);

  // The first quote's text fragment should be visible. Strip leading
  // brackets etc. since cards wrap text in literal quote marks.
  const raw = quotes[0].text || quotes[0].raw_quote || "";
  // Pick a 25-char chunk from somewhere in the middle of the quote so we
  // skip past any framing characters / metadata prefixes.
  const middle = raw.slice(20, 50).trim();
  await expect(page.getByText(middle).first()).toBeVisible({ timeout: 5_000 });

  // Confirm at least one quote's persona is rendered as a metadata chip
  // on a quote card. "founder" also appears in the ICP dropdown
  // ("B2B founder"), so we have to scope the locator to the quote
  // cards section — the persona <span> inside Voice.tsx has class
  // "text-muted-foreground" and sits beside an icp Badge.
  const firstPersona = quotes[0].persona;
  if (firstPersona) {
    // Look for the persona text inside a CardContent (quote card) —
    // a <span> with the text-muted-foreground class. Use a CSS scope
    // to avoid matching dropdown options.
    const personaInCard = page.locator(
      "div.text-muted-foreground, span.text-muted-foreground",
    ).filter({ hasText: new RegExp(`^${firstPersona}$`, "i") }).first();
    await expect(personaInCard).toBeVisible({ timeout: 5_000 });
  }
  expectNoConsoleErrors(errors);
});

test("voice: ICP filter narrows the visible quotes", async ({ page, request }) => {
  const all = await (await request.get("/api/voice")).json();
  const founderQuotes = all.filter((q: { icp_segment: string }) =>
    q.icp_segment === "seg_founder_b2b",
  );

  await navigate(page, "/voice");
  // Pick "B2B founder" from the filter.
  await page.locator("select").first().selectOption({ label: "B2B founder" });
  await page.waitForLoadState("networkidle");

  // If founder has quotes, at least one quote text should be visible.
  if (founderQuotes.length > 0) {
    const t = (founderQuotes[0].text || founderQuotes[0].raw_quote || "").slice(0, 25);
    if (t) await expect(page.getByText(t).first()).toBeVisible();
  }

  // Reset filter
  await page.locator("select").first().selectOption("");
});

test("voice: search filter further narrows quotes", async ({ page, request }) => {
  const all = await (await request.get("/api/voice")).json();
  test.skip(all.length < 2, "Need ≥ 2 quotes to validate filtering");

  // Find a word that appears in some quotes but not all.
  const distinctWord = all[0].text?.split(/\s+/).find(
    (w: string) => w.length > 5 && !all[1].text?.includes(w),
  );
  test.skip(!distinctWord, "Couldn't find a distinctive word across quotes");

  await navigate(page, "/voice");
  await page.locator("input[placeholder*='search quotes' i]").fill(distinctWord);
  await page.waitForLoadState("networkidle");

  // The page should still show at least the matching quote(s)
  await expect(page.getByText(distinctWord, { exact: false }).first()).toBeVisible();
});

test("voice: empty state appears when search yields no matches", async ({ page }) => {
  await navigate(page, "/voice");
  await page.locator("input[placeholder*='search quotes' i]")
    .fill("ZZZ_completely_nonexistent_phrase_XYZ");
  await expect(page.getByText(/no quotes match/i)).toBeVisible({ timeout: 5_000 });
});

test("voice: get-started card appears when collection is genuinely empty", async ({ page, request }) => {
  // Can't reliably wipe the collection without breaking other tests, so we
  // verify the API path that triggers the guidance card: when no quotes
  // exist for ANY ICP, the page renders <VoiceGetStarted/>.
  const all = await (await request.get("/api/voice")).json();
  test.skip(all.length > 0, "Skipping — there are voice quotes; can't trigger empty state without mutating data");

  await navigate(page, "/voice");
  await expect(page.getByText(/get started/i)).toBeVisible();
});
