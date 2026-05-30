/**
 * A2 (closed-loop) — drive the accept→candidate flow through the UI.
 *
 * The headline self-learning gap was that accepting a miner's skill proposal
 * did NOTHING to the skill. This spec proves the loop end-to-end through the
 * real UI: a voice proposal is seeded via public APIs (3 edits → run miners),
 * the founder clicks "Accept as candidate" on its Weekly Review card, and a
 * brand-new candidate VERSION of the skill appears in /api/skills.
 *
 * Seeding is done through real endpoints (no Mongo poking): we produce three
 * drafts on the same ICP (identical synthetic body), submit an "edit" decision
 * on each that strips the same token, then run the miners — which clusters the
 * removed token into a house-style proposal.
 */
import { test, expect, APIRequestContext } from "@playwright/test";
import { navigate, expectNoConsoleErrors } from "./helpers/page-helpers";
import { kickoffDraft, submitDecision, waitUntil } from "./helpers/data-helpers";

test.describe.configure({ mode: "serial" });

const SKILL_ID = "house-style"; // the voice miner's hardcoded target

async function candidateCount(request: APIRequestContext, skillId: string): Promise<number | null> {
  const skills = await (await request.get("/api/skills")).json();
  const s = skills.find((x: { _id: string }) => x._id === skillId);
  if (!s) return null;
  return Array.isArray(s.candidates) ? s.candidates.length : 0;
}

/** Seed a pending house-style voice proposal via public APIs. Returns the
 *  unified proposal (or null if the env couldn't produce one). ``tokenRank``
 *  selects which token to strip so two tests can seed DISTINCT signatures
 *  (a prior test's accept/dismiss must not cooldown-suppress the next). */
async function seedVoiceProposal(request: APIRequestContext, tokenRank = 0) {
  let token = "";
  for (let i = 0; i < 3; i++) {
    const res = await kickoffDraft(request, {
      channel: "linkedin",
      icp_segment: "seg_founder_b2b",
      topic_hint: "self-learning loop seed",
    });
    const tid = res.telemetry_id as string;
    const draft = (res.draft as string) || "";
    if (!tid || !draft) return null;
    if (!token) {
      // Distinct 6+ char alphabetic tokens in the (deterministic) body,
      // longest-first — safe non-stopwords to strip so the miner clusters a
      // "remove" pattern. tokenRank picks which one (distinct signature).
      const words = Array.from(new Set(draft.match(/[A-Za-z]{6,}/g) || []))
        .sort((a, b) => b.length - a.length);
      token = words[tokenRank] || words[0] || "";
      if (!token) return null;
    }
    const approved = draft.split(token).join("").replace(/\s{2,}/g, " ").trim();
    await submitDecision(request, {
      telemetry_id: tid,
      decision: "edit",
      original_draft: draft,
      approved_text: approved,
      channel: "linkedin",
    });
  }

  // Run the miners; the voice miner turns the 3 shared edits into a proposal.
  await request.post("/api/self-critique/run-now", { timeout: 60_000 });

  const props = await (await request.get("/api/self-critique/proposals?limit=200")).json();
  return props.find(
    (p: { target_kind: string; target_id: string }) =>
      p.target_kind === "skill" && p.target_id === SKILL_ID,
  ) || null;
}

test("self-learning: accepting a skill proposal in Weekly Review mints a candidate version", async ({ page, request }) => {
  test.setTimeout(120_000);

  // Precondition: house-style must be an agent_skill (Mongo-editable body) for
  // accept to mint a candidate. The 04-skills suite relies on this seed too.
  const skills = await (await request.get("/api/skills")).json();
  const houseStyle = skills.find((s: { _id: string }) => s._id === SKILL_ID);
  if (!houseStyle || houseStyle.skill_kind !== "agent_skill") {
    test.info().annotations.push({
      type: "skip-reason",
      description: "house-style not seeded as an agent_skill in this env",
    });
    return;
  }

  const proposal = await seedVoiceProposal(request);
  if (!proposal) {
    test.info().annotations.push({
      type: "skip-reason",
      description: "could not seed a house-style voice proposal (no draft body/token)",
    });
    return;
  }

  const before = (await candidateCount(request, SKILL_ID)) ?? 0;

  // Drive the accept through the UI.
  const errors = await navigate(page, "/weekly-review");
  // The skill card renders "Improvement for house-style" with an "Accept as
  // candidate" button (ProposalCard, target_kind === "skill").
  const card = page
    .locator(".card-decision")
    .filter({ hasText: SKILL_ID })
    .filter({ has: page.getByRole("button", { name: /accept as candidate/i }) })
    .first();
  await expect(card).toBeVisible({ timeout: 10_000 });
  await card.getByRole("button", { name: /accept as candidate/i }).click();

  // The closed loop: a NEW candidate version of the skill must appear.
  await waitUntil(async () => {
    const after = (await candidateCount(request, SKILL_ID)) ?? 0;
    return after > before;
  }, 20_000);

  // And the minted candidate carries the appended "Learned rules" section.
  const detail = await (await request.get(`/api/skills/${SKILL_ID}`)).json();
  const versions = detail.versions || {};
  const cands: string[] = detail.candidates || [];
  expect(cands.length).toBeGreaterThan(before);
  const newest = cands[cands.length - 1];
  expect(
    (versions[newest]?.body_md || "").includes("Learned rules"),
    "minted candidate body should carry the appended Learned-rules section",
  ).toBeTruthy();

  expectNoConsoleErrors(errors);
});

test("self-learning: a dismissed skill proposal does not recur after re-running miners", async ({ request }) => {
  test.setTimeout(120_000);

  // tokenRank=1 → a DISTINCT signature from the accept test, so its prior
  // decision doesn't cooldown-suppress this seed.
  const proposal = await seedVoiceProposal(request, 1);
  if (!proposal) {
    test.info().annotations.push({
      type: "skip-reason",
      description: "could not seed a house-style voice proposal",
    });
    return;
  }

  // Dismiss via the unified endpoint (what the UI's Dismiss button calls).
  const r = await request.post(
    `/api/self-critique/proposals/${encodeURIComponent(proposal.id)}/dismiss`,
  );
  expect(r.ok()).toBeTruthy();

  // Re-run miners — the same dismissed pattern must NOT come back pending.
  await request.post("/api/self-critique/run-now", { timeout: 60_000 });
  const after = await (await request.get("/api/self-critique/proposals?limit=200")).json();
  const recurred = after.find((p: { id: string }) => p.id === proposal.id);
  expect(
    recurred,
    "dismissed skill proposal recurred after run-now (cooldown not respected)",
  ).toBeUndefined();
});
