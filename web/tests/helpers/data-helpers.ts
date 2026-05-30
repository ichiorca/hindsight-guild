/**
 * Data-producing helpers used by tests to actively populate Mongo via
 * /api/draft handoffs, rather than passively assume the data exists.
 *
 * Every spec file uses these to either:
 *   - generate the data its assertions then read, or
 *   - verify that data produced earlier in the suite is now visible.
 *
 * Designed to be idempotent + cheap (synthetic-mode jobs complete in
 * ~5 seconds; the whole suite produces 30-50 jobs total).
 */
import { APIRequestContext, expect } from "@playwright/test";

export type AgentId =
  | "pipeline"
  | "content_agent"
  | "research_agent"
  | "review_agent"
  | "image_brief_agent"
  | "analytics_agent"
  | "cmo_planner"
  | "positioning_agent"
  | "customer_voice_agent"
  | "lifecycle_email_agent"
  | "paid_media_agent"
  | "ops_qa_agent"
  | "self_critique_agent";

export interface DraftOpts {
  icp_segment?: string;
  channel?: string;
  topic_hint?: string;
  agent_id?: AgentId;
}

/**
 * Fire one /api/draft job and wait until it terminates (done | failed).
 * Returns the unwrapped ``result`` field on success.
 * Throws (test fails) if the job times out or returns failed status.
 */
export async function kickoffDraft(
  request: APIRequestContext,
  opts: DraftOpts = {},
  // Synthetic-mode jobs finish in ~5s, but a REAL pipeline draft against the
  // deployed app runs every LLM stage and takes ~120-180s. Default high so the
  // same helper works against prod; local synthetic runs still return the
  // instant the job is done, so the ceiling costs them nothing.
  maxWaitMs = 220_000,
): Promise<Record<string, unknown>> {
  const payload = {
    icp_segment: opts.icp_segment ?? "seg_founder_b2b",
    channel: opts.channel ?? "linkedin",
    topic_hint: opts.topic_hint ?? "",
    ...(opts.agent_id && opts.agent_id !== "pipeline"
      ? { agent_id: opts.agent_id } : {}),
  };
  const submit = await request.post("/api/draft", { data: payload });
  expect(submit.ok(), `POST /api/draft failed: ${submit.status()}`).toBeTruthy();
  const { job_id } = await submit.json();

  const started = Date.now();
  while (Date.now() - started < maxWaitMs) {
    await new Promise((r) => setTimeout(r, 800));
    const r = await request.get(`/api/draft/${job_id}`);
    const job = await r.json();
    if (job.status === "done") return job.result;
    if (job.status === "failed") {
      throw new Error(
        `Draft job ${job_id} failed: ${job.error}`,
      );
    }
  }
  throw new Error(`Draft job ${job_id} timed out after ${maxWaitMs}ms`);
}

/**
 * Ensure the Approval Queue has at least ``minCount`` items. If short,
 * kicks off ``pipeline`` drafts until the queue grows enough. Used by
 * queue-action tests so a previous test's approvals don't leave them
 * with nothing to act on.
 */
export async function ensureQueueHas(
  request: APIRequestContext,
  minCount: number,
  diverseChannels = true,
): Promise<number> {
  const channels = diverseChannels
    ? ["linkedin", "substack", "blog", "email"]
    : ["linkedin"];

  let queue = await (await request.get("/api/queue")).json();
  let i = 0;
  while (queue.length < minCount) {
    const ch = channels[i % channels.length];
    await kickoffDraft(request, {
      channel: ch,
      topic_hint: `e2e queue top-up #${i + 1}`,
      // agent_id omitted = full pipeline → produces a draft action
      // and lands as a queue item.
    });
    queue = await (await request.get("/api/queue")).json();
    i += 1;
    if (i > 8) {
      // Safety stop — if 8 drafts didn't grow the queue, something's wrong.
      break;
    }
  }
  return queue.length;
}

/**
 * Submit a decision via /api/decisions. Returns immediately — caller
 * is responsible for polling the queue if they need to see it shrink.
 */
export async function submitDecision(
  request: APIRequestContext,
  args: {
    telemetry_id: string;
    decision: "approve" | "edit" | "reject";
    original_draft?: string;
    approved_text?: string;
    rejection_reason?: string;
    channel?: string | null;
  },
) {
  const r = await request.post("/api/decisions", {
    data: {
      decision: args.decision,
      telemetry_id: args.telemetry_id,
      original_draft: args.original_draft ?? "",
      approved_text: args.approved_text ?? args.original_draft ?? "",
      rejection_reason: args.rejection_reason ?? "",
      channel: args.channel ?? null,
    },
  });
  expect(r.ok(), `POST /api/decisions failed: ${r.status()}`).toBeTruthy();
  return r.json();
}

/**
 * Drain the entire queue by approving every item. Used by tests that
 * want a clean empty-state baseline.
 */
export async function drainQueue(request: APIRequestContext): Promise<number> {
  const items = await (await request.get("/api/queue")).json();
  for (const item of items) {
    await submitDecision(request, {
      telemetry_id: item.telemetry_id,
      decision: "approve",
      original_draft: item.draft_text ?? "",
      approved_text: item.draft_text ?? "",
      channel: item.channel,
    });
  }
  return items.length;
}

/**
 * Read a Mongo collection's count via the API endpoint that exposes it.
 * Used by tests asserting "count went up after action X".
 */
export async function getCount(
  request: APIRequestContext,
  endpoint: string,
): Promise<number> {
  const r = await request.get(endpoint);
  if (!r.ok()) return -1;
  const body = await r.json();
  if (Array.isArray(body)) return body.length;
  if (typeof body === "object" && body !== null) {
    if ("agents" in body && Array.isArray((body as { agents: unknown[] }).agents)) {
      return (body as { agents: unknown[] }).agents.length;
    }
    if ("total_actions" in body) return (body as { total_actions: number }).total_actions;
  }
  return -1;
}

/**
 * Wait for an async condition with a poll loop. Used in tests where a
 * write needs to propagate (e.g., approval → queue refresh).
 */
export async function waitUntil(
  check: () => Promise<boolean>,
  // Atlas (cloud Mongo) write→read propagation is slower than a local Mongo,
  // so give post-action assertions (approval shrinks the queue, etc.) more
  // headroom than the original local-only 8s.
  timeoutMs = 20_000,
  intervalMs = 400,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastErr: unknown = null;
  while (Date.now() < deadline) {
    try {
      if (await check()) return;
    } catch (e) {
      lastErr = e;
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw lastErr ?? new Error(`Condition not met within ${timeoutMs}ms`);
}
