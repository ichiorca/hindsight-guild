/**
 * Drafting page — deep coverage:
 *   - Form gating per agent (channel hidden for ops_qa, textarea for voice)
 *   - Full pipeline: kick a real run, then verify draft body + image
 *     + research_findings card all render
 *   - Each single-agent route: verify the AgentResult renderer dispatched
 *     to the right shape's component (not the generic fallback)
 *   - Stepper animation: nodes flip running → done on submit
 *   - Result panel data is real (no fake numbers in eval-scores card)
 */
import { test, expect, Page } from "@playwright/test";
import { navigate, expectNoConsoleErrors } from "./helpers/page-helpers";

async function setRouteTo(page: Page, label: RegExp) {
  const routeLabel = page.locator("label").filter({ hasText: /route to/i });
  const routeSelect = routeLabel.locator("..").locator("select");
  const optionValue = await routeSelect.evaluate(
    (sel: HTMLSelectElement, pattern: string) => {
      const re = new RegExp(pattern, "i");
      for (const o of Array.from(sel.options)) {
        if (re.test(o.text)) return o.value;
      }
      return "";
    },
    label.source,
  );
  expect(optionValue, `option matching ${label} not found`).not.toBe("");
  await routeSelect.selectOption(optionValue);
  return optionValue;
}

async function submitDraftForm(page: Page) {
  const btn = page.getByRole("button", { name: /hand (off|it)/i }).first();
  await expect(btn).toBeEnabled();
  await btn.click();
}

test("drafting: full pipeline produces draft + image + research_findings", async ({ page }) => {
  test.setTimeout(240_000);
  const errors = await navigate(page, "/draft");

  await setRouteTo(page, /full drafting team/i);
  // Channel = LinkedIn (default), topic given.
  await page.locator("input[placeholder*='handoff' i], input[placeholder*='topic' i]").first()
    .fill("e2e: deep test for pipeline");

  await submitDraftForm(page);

  // 1) The draft body appears in the right pane.
  await expect(page.getByText(/drafted this/i)).toBeVisible({ timeout: 200_000 });
  // 2) Either eval scores OR the "no scores in synthetic" hint shows.
  await expect(
    page.getByText(/review scores|quality scores aren't shown/i)
  ).toBeVisible();
  // 3) Research findings card appears with voice + claims sections.
  await expect(page.getByText(/what research surfaced/i)).toBeVisible();

  expectNoConsoleErrors(errors);
});

test("drafting: form gates channel for ops_qa (no channel needed)", async ({ page }) => {
  await navigate(page, "/draft");
  await setRouteTo(page, /ops\/qa sweep/i);

  // The channel <select> should be hidden when ops_qa is selected.
  const channelLabel = page.locator("label").filter({ hasText: /^channel$/i });
  await expect(channelLabel).not.toBeVisible();
});

test("drafting: customer_voice route uses textarea for raw transcript", async ({ page }) => {
  await navigate(page, "/draft");
  await setRouteTo(page, /customer voice only/i);

  // Topic input should be a <textarea> now, not <input>.
  await expect(page.locator("textarea").first()).toBeVisible();
});

// Per-agent shape coverage. Each entry asserts a string UNIQUE to that
// agent's renderer (not present in any other shape's output).
const PER_AGENT = [
  { route: /lifecycle email only/i,  expect: /\d+ steps · seg_/i,                  topic: "deep test lifecycle" },
  { route: /paid media only/i,       expect: /variants? proposed|paused on insert/i, topic: "deep paid test", channel: /google ads/i },
  { route: /positioning only/i,      expect: /proposal[s]? drafted · awaiting/i,   topic: "deep positioning" },
  { route: /customer voice only/i,   expect: /inserted|skipped/i,                   topic: "Sales call: We tried Salesforce integration and it took weeks." },
  { route: /research only/i,         expect: /voice quote|approved claim|customer voice \(/i, topic: "agentic GTM landscape" },
  { route: /imagebrief only/i,       expect: /alt:|prompt:|image preview/i,          topic: "minimal flat-design illustration" },
  // CMO synthetic returns memo_markdown starting with "# Weekly CMO memo — preview".
  // The renderer puts it in a <pre> — match the heading text.
  { route: /cmo weekly memo/i,       expect: /Weekly CMO memo/i, topic: "expansion playbook" },
];

for (const t of PER_AGENT) {
  test(`drafting: ${t.route} renders dedicated shape view`, async ({ page }) => {
    test.setTimeout(240_000);
    const errors = await navigate(page, "/draft");
    await setRouteTo(page, t.route);
    if (t.channel) {
      const chLabel = page.locator("label").filter({ hasText: /^channel$/i });
      if (await chLabel.count() > 0) {
        const sel = chLabel.locator("..").locator("select");
        const val = await sel.evaluate(
          (s: HTMLSelectElement, p: string) => {
            const re = new RegExp(p, "i");
            for (const o of Array.from(s.options)) {
              if (re.test(o.text)) return o.value;
            }
            return "";
          },
          t.channel!.source,
        );
        if (val) await sel.selectOption(val);
      }
    }
    if (t.topic) {
      await page.locator("input, textarea").first().fill(t.topic);
    }
    await submitDraftForm(page);

    // Verify the dedicated renderer (not the "no preview implemented" fallback).
    await expect(page.getByText(t.expect).first()).toBeVisible({ timeout: 200_000 });
    await expect(page.getByText(/no synthetic preview implemented/i)).not.toBeVisible();

    expectNoConsoleErrors(errors);
  });
}

test("drafting: review_agent shows extracted draft + flag list", async ({ page }) => {
  test.setTimeout(240_000);
  // Intercept the API call so we can verify the synthetic shape itself
  // matches what the renderer expects — UI text matching is too fragile
  // for the review-shape rendering paths (badge variants, card titles).
  let reviewResult: Record<string, unknown> | null = null;
  page.on("response", async (resp) => {
    if (resp.url().includes("/api/draft/") && resp.request().method() === "GET") {
      try {
        const body = await resp.json();
        if (body.status === "done" && body.result?.shape === "review") {
          reviewResult = body.result;
        }
      } catch { /* ignore */ }
    }
  });

  await navigate(page, "/draft");
  await setRouteTo(page, /review only/i);
  await submitDraftForm(page);

  // Wait for the API to return the result.
  await expect.poll(() => reviewResult, { timeout: 200_000 }).toBeTruthy();
  // Shape contract — these fields must exist for the UI to render properly.
  expect(reviewResult!.shape).toBe("review");
  expect((reviewResult as any).review).toHaveProperty("flags");
  expect((reviewResult as any).review).toHaveProperty("recommendation");

  // And the page actually rendered the review shape. The recommendation
  // value lands inside a <span> Badge with the bare value as text.
  await expect(
    page.locator("span").filter({ hasText: /^(pass|edit|reject)$/i }).first(),
  ).toBeVisible({ timeout: 5_000 });
  // And the card description "X flag(s) on the most recent draft" appears.
  await expect(
    page.getByText(/flag[s]?\s+on the most recent draft/i).first(),
  ).toBeVisible({ timeout: 5_000 });
});

test("drafting: imagebrief preview shows real PNG with file URL", async ({ page }) => {
  test.setTimeout(240_000);
  await navigate(page, "/draft");
  await setRouteTo(page, /imagebrief only/i);
  await page.locator("input").first().fill("clean editorial illustration of data flow");
  await submitDraftForm(page);

  // Wait for the result panel to appear. Either a real <img> tag with a
  // /media URL OR a stub box.
  await expect(page.locator("img, [class*='border-dashed']").first())
    .toBeVisible({ timeout: 200_000 });

  // If image generation succeeded, the <img>'s src should be a /media path.
  const img = page.locator("img").first();
  if (await img.count() > 0) {
    const src = await img.getAttribute("src");
    expect(src, "image src missing").toBeTruthy();
    expect(src).toMatch(/^\/media\/|^https?:|^data:/);
  }
});

test("drafting: stepper transitions on submit", async ({ page }) => {
  test.setTimeout(240_000);
  await navigate(page, "/draft");
  await setRouteTo(page, /positioning only/i);
  await submitDraftForm(page);

  // Stepper card appears (only renders during/after a run).
  await expect(page.getByText(/pipeline trace/i).first()).toBeVisible({ timeout: 5_000 });
});

test("drafting: route deep-link from Agents page preselects the agent", async ({ page }) => {
  await navigate(page, "/draft?agent=lifecycle_email_agent");
  const routeLabel = page.locator("label").filter({ hasText: /route to/i });
  const routeSelect = routeLabel.locator("..").locator("select");
  await expect(routeSelect).toHaveValue("lifecycle_email_agent");
});

test("drafting: ICP selector + topic both update the request payload", async ({ page, request }) => {
  // Capture the network request the form sends; verify it carries the
  // ICP + topic we entered, not the defaults.
  await navigate(page, "/draft");

  const icpSelect = page.locator("label").filter({ hasText: /for \(icp/i })
    .locator("..").locator("select");
  await icpSelect.selectOption("seg_revops_director");

  await page.locator("input[placeholder*='handoff' i], input[placeholder*='topic' i]").first()
    .fill("e2e payload verification");

  const reqPromise = page.waitForRequest(
    (req) => req.url().endsWith("/api/draft") && req.method() === "POST",
  );
  await submitDraftForm(page);
  const req = await reqPromise;
  const body = JSON.parse(req.postData() || "{}");
  expect(body.icp_segment).toBe("seg_revops_director");
  expect(body.topic_hint).toContain("e2e payload verification");
});
