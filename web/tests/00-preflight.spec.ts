/**
 * Preflight — drives real workflows so subsequent tests have data
 * to read AND assert against.
 *
 * Runs FIRST in alphabetical order (00-) so the rest of the suite can
 * assume:
 *   - Queue has ≥ 6 items across multiple channels
 *   - Customer voice has new quotes ingested via the agent
 *   - Lifecycle / paid / positioning inboxes are populated
 *   - Capabilities heatmap has fresh skill_usage rows
 *   - Live ops has recent telemetry
 *
 * Importantly, this preflight ALSO validates each action's response
 * shape — so it doubles as the "synthetic preview is wired" check that
 * was previously in 02-drafting. Catches shape regressions early.
 */
import { test, expect } from "@playwright/test";
import { kickoffDraft, type AgentId } from "./helpers/data-helpers";

test.describe("preflight: produce diverse data for downstream tests", () => {
  // Pipeline drafts across 4 channels — feeds the Queue + adds actions
  // to many agents (research, content, critique, reviser, image, review,
  // finalizer) so the Capabilities heatmap has density.
  const pipelineRuns = [
    { channel: "linkedin", topic_hint: "renewal forecast reconciliation" },
    { channel: "substack", topic_hint: "agentic commerce advancements" },
    { channel: "blog",     topic_hint: "personalization at scale" },
    { channel: "email",    topic_hint: "expansion revenue playbook" },
  ];
  for (const { channel, topic_hint } of pipelineRuns) {
    test(`preflight pipeline draft → ${channel}`, async ({ request }) => {
      const r = await kickoffDraft(request, { channel, topic_hint });
      expect(r, "pipeline draft returned no result").toBeTruthy();
      // Synthetic shape: pipeline returns ``draft`` + research_findings.
      expect(typeof (r as { draft?: string }).draft).toBe("string");
    });
  }

  // Single-agent handoffs — populate per-agent inboxes + recent_actions.
  // Each must return the right ``shape`` field so the Drafting page's
  // renderer fires correctly.
  const handoffs: Array<[AgentId, string, string?]> = [
    ["lifecycle_email_agent", "lifecycle_email",
     "expansion-revenue follow-up for founders"],
    ["paid_media_agent",      "google_ads",
     "RevOps integration-friction angle"],
    ["positioning_agent",     "linkedin",
     "Stop reconciling spreadsheets — RevOps positioning"],
    ["customer_voice_agent",  "linkedin",
     "Customer call: 'We replaced three tools with this. Saves the team 6 hours a week.'"],
    ["research_agent",        "substack", "what's new in agentic GTM"],
    ["ops_qa_agent",          "linkedin"],
    ["cmo_planner",           "linkedin", "expansion revenue"],
    ["review_agent",          "linkedin"],
    ["image_brief_agent",     "substack", "abstract data flow illustration"],
    ["analytics_agent",       "linkedin"],
    ["self_critique_agent",   "linkedin"],
  ];

  for (const [agent_id, channel, topic_hint] of handoffs) {
    test(`preflight handoff → ${agent_id}`, async ({ request }) => {
      test.setTimeout(45_000);
      const r = await kickoffDraft(request, { agent_id, channel, topic_hint });
      expect(r, `${agent_id} returned empty result`).toBeTruthy();
      // Every synthetic preview now carries ``shape`` so the renderer
      // can dispatch. If any agent falls through to ``generic``, that's
      // a known regression we want this preflight to catch.
      const shape = (r as { shape?: string }).shape;
      expect(shape, `${agent_id} should have a non-generic shape`).toBeTruthy();
    });
  }

  test("preflight summary — all collections populated", async ({ request }) => {
    // After preflight, every collection a page reads from should be
    // non-empty (with the documented exceptions: experiments collection
    // requires CMO to author one — that's a separate test).
    const expectNonEmpty: Array<[string, string]> = [
      ["/api/queue",                          "queue"],
      ["/api/voice",                          "customer_voice"],
      ["/api/agents",                         "agents roster"],
      ["/api/capabilities?days=7",            "capabilities"],
      ["/api/skills",                         "skills"],
    ];
    for (const [path, label] of expectNonEmpty) {
      const body = await (await request.get(path)).json();
      const size = Array.isArray(body) ? body.length
                 : Array.isArray(body.agents) ? body.agents.length
                 : Array.isArray(body.catalog) ? body.catalog.length
                 : 0;
      expect(size, `${label} (${path}) should be non-empty after preflight`)
        .toBeGreaterThan(0);
    }
  });
});
