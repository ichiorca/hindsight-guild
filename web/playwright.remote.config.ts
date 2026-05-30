import { defineConfig, devices } from "@playwright/test";

// Remote config: drives the DEPLOYED Firebase app (static UI + /api rewrites
// to the web-api Cloud Run service) instead of spawning local servers. The
// app's relative `/api/...` calls resolve against the Firebase host, so a
// single baseURL exercises the real production stack end-to-end.
//
//   npx playwright test -c playwright.remote.config.ts tests/00-smoke.spec.ts
//
// Override the target with PLAYWRIGHT_TEST_BASE_URL.
const BASE =
  process.env.PLAYWRIGHT_TEST_BASE_URL ||
  "https://gen-lang-client-0079238279.web.app";

export default defineConfig({
  testDir: "./tests",
  // Prod drafts go through real LLM calls — generous budgets so slowness
  // doesn't masquerade as a failure.
  timeout: 150_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: "remote-results.json" }]],
  use: {
    baseURL: BASE,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
  },
  // No webServer — we target the already-deployed app.
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
