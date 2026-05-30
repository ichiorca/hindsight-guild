/**
 * Playwright config — full-stack e2e UI tests.
 *
 * Both the FastAPI backend and the Vite dev server are spawned as
 * webServers so the test suite is self-contained. Tests assume the
 * Mongo store has been populated by ``python -m scripts.e2e_workflows``
 * (real transactional data from agent runs — no mocks).
 *
 * Selectors deliberately avoid CSS classes (Tailwind utility classes
 * change too often) and prefer role + accessible name, then text content.
 *
 * Default browser is chromium only to keep the install fast on Windows
 * laptops; add 'firefox' or 'webkit' projects below if cross-browser
 * coverage becomes needed.
 */
import { defineConfig, devices } from "@playwright/test";

const UI_PORT = 5173;
const API_PORT = 8081;

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,        // some tests depend on shared Mongo state (queue → decision)
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,                  // serial — same reason as fullyParallel: false
  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report", open: "never" }],
  ],

  // 30s default per test action. Drafting handoffs go through /api/draft
  // which spawns a job + polls — those tests bump locally via test.setTimeout.
  timeout: 30_000,
  expect: { timeout: 8_000 },

  use: {
    baseURL: `http://localhost:${UI_PORT}`,
    trace: "retain-on-failure",     // capture full traces on red runs
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 8_000,
    navigationTimeout: 15_000,
  },

  // Spawn both servers. Playwright re-uses ones already listening, so
  // running tests against a hand-started API/UI is fine too.
  webServer: [
    {
      // FastAPI backend with LOCAL_DEV path: synthetic fallback for drafts,
      // Mongo writes, no GCP creds required. cmd.exe (Playwright's default
      // shell on Windows) requires an explicit ``.\`` prefix for relative
      // paths — bare ``.venv\...`` is treated as a command lookup, not
      // a path. Encode that here so Windows-first laptops just work.
      command:
        process.platform === "win32"
          ? `.\\.venv\\Scripts\\python.exe -m uvicorn services.web_api.main:app --port ${API_PORT} --log-level warning`
          : `./.venv/bin/python -m uvicorn services.web_api.main:app --port ${API_PORT} --log-level warning`,
      cwd: "..",
      url: `http://localhost:${API_PORT}/api/health`,
      reuseExistingServer: true,
      timeout: 60_000,
      env: {
        LOCAL_DEV: "1",
        PROJECT_ID: "local-dev",
        MONGO_DB: "hindsight_guild",
        MONGO_URI_DIRECT: "mongodb://localhost:27017",
        DRAFTING_FALLBACK: "synthetic",
        PYTHONIOENCODING: "utf-8",
      },
    },
    {
      // Vite dev server. VITE_API_URL points the /api proxy at the FastAPI
      // backend we just spawned.
      command: "npm run dev",
      cwd: ".",
      url: `http://localhost:${UI_PORT}`,
      reuseExistingServer: true,
      timeout: 60_000,
      env: {
        VITE_API_URL: `http://localhost:${API_PORT}`,
      },
    },
  ],

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
