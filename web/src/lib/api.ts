import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "./toast";
import type {
  CapabilitiesData,
  DecisionPayload,
  Experiment,
  PublishedItem,
  QueueItem,
  RubricTrendPoint,
  Skill,
  VoiceQuote,
  WeekSummary,
  WeeklyReview,
} from "./types";

const BASE = "/api";

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

// ---------------- Queue ----------------

export function useQueue(channel?: string) {
  return useQuery({
    queryKey: ["queue", channel],
    queryFn: () =>
      get<QueueItem[]>(`/queue${channel ? `?channel=${channel}` : ""}`),
    // While a publish is in flight, poll briefly so the badge flips from
    // "Publishing…" → "Published" without the user reloading. Quiet down
    // once nothing is in flight.
    refetchInterval: (q) => {
      const data = q.state.data as QueueItem[] | undefined;
      const inFlight = data?.some((i) => i.publish_state === "publishing");
      return inFlight ? 4_000 : false;
    },
  });
}

export function useSubmitDecision() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: DecisionPayload) => post<{ ok: boolean }>("/decisions", p),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["queue"] });
      qc.invalidateQueries({ queryKey: ["weekly-review"] });
    },
    onError: () =>
      toast.error("Couldn't save your decision", {
        description: "Nothing was published. Check your connection and try again.",
      }),
  });
}

// ---------------- Experiments ----------------

export function useRunningExperiments() {
  return useQuery({
    queryKey: ["experiments", "running"],
    queryFn: () => get<Experiment[]>("/experiments/running"),
  });
}

export function useDecidedExperiments(limit = 10) {
  return useQuery({
    queryKey: ["experiments", "decided", limit],
    queryFn: () => get<Experiment[]>(`/experiments/decided?limit=${limit}`),
  });
}

export function useDriftInvestigations() {
  return useQuery({
    queryKey: ["experiments", "drift"],
    queryFn: () => get<Experiment[]>("/experiments/drift"),
  });
}

// ---------------- Skills ----------------

export function useSkills() {
  return useQuery({
    queryKey: ["skills"],
    queryFn: () => get<Skill[]>("/skills"),
  });
}

export function useSkill(id: string) {
  return useQuery({
    queryKey: ["skill", id],
    queryFn: () => get<Skill>(`/skills/${id}`),
    enabled: !!id,
  });
}

export function useDecidePromotion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: { skill_id: string; decision: "approve" | "reject" }) =>
      post(`/skills/${p.skill_id}/promotion`, { decision: p.decision }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["skills"] });
      qc.invalidateQueries({ queryKey: ["weekly-review"] });
    },
    onError: () =>
      toast.error("Couldn't apply that promotion decision", {
        description: "The playbook was not changed. Try again in a moment.",
      }),
  });
}

export function useDecideCritique() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: { skill_id: string; decision: "accept" | "reject" }) =>
      post(`/self-critique/${p.skill_id}`, { decision: p.decision }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["skills"] });
      qc.invalidateQueries({ queryKey: ["weekly-review"] });
    },
    onError: () =>
      toast.error("Couldn't save that decision", {
        description: "The proposal is unchanged. Try again in a moment.",
      }),
  });
}

// ---------------- Dashboards ----------------

export function useRubricTrend(days = 28) {
  return useQuery({
    queryKey: ["rubric-trend", days],
    queryFn: () => get<RubricTrendPoint[]>(`/rubric-trend?days=${days}`),
  });
}

export function useWeekSummary() {
  return useQuery({
    queryKey: ["week-summary"],
    queryFn: () => get<WeekSummary>("/this-week-summary"),
  });
}

// ---------------- Self-critique (PRD-03) ----------------

export interface UnifiedProposal {
  id: string;
  target_kind: "skill" | "paid_action" | "signal_source";
  target_id: string;
  miner: string | null;
  kind: string | null;
  issue: string;
  proposed_change: string | null;
  confidence: string | null;
  evidence_count: number | null;
  evidence: Record<string, unknown>;
  proposed_at: string | null;
  status: string | null;
}

export interface LearningSummary {
  days: number;
  this_window: {
    proposals_emitted: number;
    proposals_accepted: number;
    proposals_dismissed: number;
    promotions: number;
    miner_runs: number;
  };
  pending_proposals: number;
  per_miner_28d: Array<{
    miner: string;
    runs: number;
    emitted: number;
    accepted: number;
    dismissed: number;
  }>;
  events: Array<{
    ts: string | null;
    kind: "miner_run" | "proposal_accepted" | "proposal_dismissed" | "promotion";
    summary: string;
    miner?: string | null;
    target?: string | null;
  }>;
  voice_delta: number | null;
}

export function useLearningSummary(days = 7) {
  return useQuery({
    queryKey: ["learning-summary", days],
    queryFn: () => get<LearningSummary>(`/self-critique/summary?days=${days}`),
    refetchInterval: 60_000,
  });
}

export interface SelfCritiqueRun {
  id: string;
  started_at: string | null;
  completed_at: string | null;
  status: string | null;
  lookback_days: number | null;
  total_proposals: number;
  miners: Record<string, {
    proposals: number;
    evidence_scanned?: number;
    errors: string[];
    disabled?: boolean;
  }>;
}

export function useUnifiedProposals() {
  return useQuery({
    queryKey: ["self-critique-proposals"],
    queryFn: () => get<UnifiedProposal[]>("/self-critique/proposals"),
    refetchInterval: 60_000,
  });
}

export function useSelfCritiqueRuns(limit = 14) {
  return useQuery({
    queryKey: ["self-critique-runs", limit],
    queryFn: () => get<SelfCritiqueRun[]>(`/self-critique/runs?limit=${limit}`),
  });
}

export function useApproveProposal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      post<{ ok: boolean }>(`/self-critique/proposals/${encodeURIComponent(id)}/approve`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["self-critique-proposals"] });
      qc.invalidateQueries({ queryKey: ["learning-summary"] });
      qc.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: () =>
      toast.error("Couldn't accept that proposal", {
        description: "Nothing changed. Try again in a moment.",
      }),
  });
}

export function useDismissProposal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      post<{ ok: boolean }>(`/self-critique/proposals/${encodeURIComponent(id)}/dismiss`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["self-critique-proposals"] });
      qc.invalidateQueries({ queryKey: ["learning-summary"] });
    },
    onError: () =>
      toast.error("Couldn't dismiss that proposal", {
        description: "It's still in your queue. Try again in a moment.",
      }),
  });
}

export function useRunSelfCritiqueNow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => post<SelfCritiqueRun>("/self-critique/run-now", {}),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["self-critique-proposals"] });
      qc.invalidateQueries({ queryKey: ["self-critique-runs"] });
      qc.invalidateQueries({ queryKey: ["learning-summary"] });
      const n = run?.total_proposals ?? 0;
      toast.success(
        n > 0 ? `Miners ran — ${n} new proposal${n === 1 ? "" : "s"}` : "Miners ran — no new proposals",
        { description: "The team re-scanned recent activity for improvements." },
      );
    },
    onError: () =>
      toast.error("Couldn't run the miners", {
        description: "Try again in a moment.",
      }),
  });
}

// ---------------- Signals (PRD-02) ----------------

export interface Signal {
  id: string;
  source: string | null;
  ts: string | null;
  evidence_url: string | null;
  evidence_excerpt: string | null;
  icp_segment: string | null;
  icp_keywords_hit: string[];
  score: number | null;
  processed: boolean;
  processed_at: string | null;
  suppressed_reason: string | null;
  triggered_telemetry_id: string | null;
  router_job_id: string | null;
}

export interface SignalSource {
  name: string;
  source: string;
  enabled: boolean;
  icp_segment: string | null;
  poll_interval_sec: number | null;
  score_floor: number | null;
  default_channel: string | null;
  last_polled_at: string | null;
  signals_24h: number;
  drafts_24h: number;
}

export function useSignals(opts?: { source?: string; status?: string; limit?: number }) {
  const qs = new URLSearchParams();
  if (opts?.source) qs.set("source", opts.source);
  if (opts?.status) qs.set("status", opts.status);
  if (opts?.limit) qs.set("limit", String(opts.limit));
  return useQuery({
    queryKey: ["signals", opts?.source, opts?.status, opts?.limit],
    queryFn: () => get<Signal[]>(`/signals?${qs.toString()}`),
    refetchInterval: 30_000,
  });
}

export function useSignalSources() {
  return useQuery({
    queryKey: ["signal-sources"],
    queryFn: () => get<SignalSource[]>("/signals/sources"),
    refetchInterval: 60_000,
  });
}

export function usePollSignalsNow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => post<{ status: string; total_new: number }>("/signals/poll-now", {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["signals"] });
      qc.invalidateQueries({ queryKey: ["signal-sources"] });
    },
  });
}

export function useSuppressSignal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (signalId: string) =>
      post<{ status: string }>(`/signals/${signalId}/suppress`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["signals"] }),
  });
}

export function useManualSignal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: { url: string; icp_segment?: string; channel?: string }) =>
      post<{ status: string; signal_id: string }>("/signals/manual", p),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["signals"] }),
  });
}

// ---------------- AEO (PRD-01) ----------------

export interface AeoCitation {
  id: string;
  ts: string | null;
  platform: string | null;
  query: string | null;
  cited_url: string | null;
  evidence_url: string | null;
  telemetry_id: string | null;
  added_by: string | null;
}

export function useAeoCitedBy(days = 28) {
  return useQuery({
    queryKey: ["aeo-cited-by", days],
    queryFn: () => get<AeoCitation[]>(`/aeo/cited-by?days=${days}`),
    // Citations are logged manually in MVP 1; refetch on focus so a CLI
    // log_citation from another terminal surfaces without a hard refresh.
    refetchOnWindowFocus: true,
  });
}

export function useWeeklyReview() {
  return useQuery({
    queryKey: ["weekly-review"],
    queryFn: () => get<WeeklyReview>("/weekly-review"),
  });
}

// ---------------- Voice + Negatives ----------------

export function useVoice(icp?: string, theme?: string) {
  const qs = new URLSearchParams();
  if (icp) qs.set("icp_segment", icp);
  if (theme) qs.set("theme", theme);
  return useQuery({
    queryKey: ["voice", icp, theme],
    queryFn: () => get<VoiceQuote[]>(`/voice?${qs.toString()}`),
  });
}

export function useNegatives(channel?: string, category?: string) {
  const qs = new URLSearchParams();
  if (channel) qs.set("channel", channel);
  if (category) qs.set("category", category);
  return useQuery({
    queryKey: ["negatives", channel, category],
    queryFn: () => get<Array<Record<string, unknown>>>(`/negatives?${qs.toString()}`),
  });
}

export interface SampleDraft {
  telemetry_id: string;
  ts: string;
  skill_version: string;
  channel: string | null;
  draft_text: string;
  eval_scores: Record<string, number>;
}

export function useSkillSamples(skill_id: string | undefined, version: string | undefined, n = 3) {
  return useQuery({
    queryKey: ["skill-samples", skill_id, version, n],
    queryFn: () =>
      get<SampleDraft[]>(`/skills/${skill_id}/samples?version=${encodeURIComponent(version!)}&n=${n}`),
    enabled: !!skill_id && !!version,
  });
}

export interface SkillBody {
  body: string;
  format: "markdown" | "text";
  version_label: string;
  source_path: string;
}

// ---------------- Agents (per-agent roster + inboxes) ----------------

export interface AgentRosterEntry {
  agent_id: string;
  skills_allowed: string[];
  tools: string[];
  a2a_port: number | null;
  inbox: {
    count: number;
    label: string;
    sample: Array<Record<string, unknown>>;
  };
  // Last 5 telemetry rows authored by this agent (7-day window). Used
  // to surface the contribution timeline below the inbox in the Agents
  // page detail panel.
  recent_actions: Array<{
    ts: string | null;
    action_type: string | null;
    channel: string | null;
    skill_id: string | null;
    telemetry_id: string | null;
  }>;
}

export function useAgents(inboxLimit = 5) {
  return useQuery({
    queryKey: ["agents", inboxLimit],
    queryFn: () =>
      get<{ agents: AgentRosterEntry[]; as_of: string }>(
        `/agents?inbox_limit=${inboxLimit}`,
      ),
    // Inbox counts shift when a new draft completes; refresh on a slow tick.
    refetchInterval: 30_000,
  });
}

export function useSkillBody(skill_id: string | undefined, version?: string) {
  return useQuery({
    queryKey: ["skill-body", skill_id, version ?? null],
    queryFn: () =>
      get<SkillBody>(
        `/skills/${skill_id}/body${version ? `?version=${encodeURIComponent(version)}` : ""}`,
      ),
    enabled: !!skill_id,
    // Bodies are big-ish; cache aggressively. The user expanding the panel
    // is the trigger, not a poll.
    staleTime: 5 * 60_000,
  });
}

// ---------------- Live Ops ----------------

export interface LiveAction {
  telemetry_id: string;
  ts: string;
  agent: string;
  action_type: string;
  channel: string | null;
  skill_id: string;
  skill_version: string;
  eval_scores?: Record<string, number>;
  model_armor?: { decision: string; categories: string[] };
}

export interface LiveHeartbeat {
  agent: string;
  last_seen: string;
  n_24h: number;
}

export interface LiveScheduledJob {
  name: string;
  schedule: string;
  category: string;
}

export interface LiveData {
  recent_actions: LiveAction[];
  heartbeats: LiveHeartbeat[];
  outcome_status: Record<string, number>;
  scheduled_jobs: LiveScheduledJob[];
  server_time: string;
}

export function useLive(refetchInterval = 8000) {
  return useQuery({
    queryKey: ["live"],
    queryFn: () => get<LiveData>("/live"),
    refetchInterval,
    refetchIntervalInBackground: false,
  });
}

// ---------------- Capabilities (Agent Skills + usage) ----------------

export function useCapabilities(days: number = 7) {
  return useQuery({
    queryKey: ["capabilities", days],
    queryFn: () => get<CapabilitiesData>(`/capabilities?days=${days}`),
    staleTime: 60_000,
  });
}

// ---------------- Drafting ----------------

/**
 * Drafting is async: POST /api/draft returns immediately with a job_id, and
 * we poll GET /api/draft/{job_id} until status === "done" or "failed".
 *
 * The mutation's external contract is unchanged — components await draft.mutate
 * the same way as before and get the flat draft envelope back. The poll loop
 * is hidden inside mutationFn so existing consumers (Drafting.tsx) need no
 * changes. The poll cadence is 2s — long enough to be polite to the API,
 * short enough that a 60s pipeline completes in ~30 polls.
 */
// ---------------- Outbound publishing integration ----------------

export interface PublishedInfo {
  platform?: string;
  external_url?: string;
  external_id?: number | string;
  title?: string;
  published_at?: string;
}

// Publish history — everything that reached an external platform, newest
// first. Backs the /published page. Polls lightly so a fresh publish appears
// without a manual reload.
export function usePublishedHistory(channel?: string) {
  return useQuery({
    queryKey: ["published-history", channel],
    queryFn: () =>
      get<PublishedItem[]>(`/published${channel ? `?channel=${channel}` : ""}`),
    refetchInterval: 30_000,
  });
}

export function usePublished(telemetryId: string | undefined) {
  return useQuery({
    queryKey: ["published", telemetryId],
    queryFn: () => get<PublishedInfo>(`/published/${telemetryId}`),
    enabled: !!telemetryId,
    // Published URLs don't change — cache aggressively to avoid spamming
    // the API for every queue card render.
    staleTime: 5 * 60_000,
  });
}

// One entry per outbound integration adapter. ``platform`` is the
// surface label the UI shows ("Dev.to", "LinkedIn", "Google Ads",
// "Meta Ads"); ``channels`` is every queue-row channel that adapter
// will publish to when configured.
export interface IntegrationEntry {
  configured: boolean;
  platform: string;
  channels: string[];
}

export type IntegrationsStatus = Record<string, IntegrationEntry>;

export function useIntegrationsStatus() {
  return useQuery({
    queryKey: ["integrations-status"],
    queryFn: () => get<IntegrationsStatus>("/integrations/status"),
    staleTime: 5 * 60_000,
  });
}

// Look up the configured integration responsible for a given queue
// channel. Returns ``null`` when no adapter is wired (channel has no
// route) or when the adapter exists but credentials are missing.
export function integrationForChannel(
  status: IntegrationsStatus | undefined,
  channel: string | null | undefined,
): IntegrationEntry | null {
  if (!status || !channel) return null;
  const c = channel.toLowerCase();
  for (const entry of Object.values(status)) {
    if (entry.configured && entry.channels.includes(c)) return entry;
  }
  return null;
}

// ---------------- Agent config (editable skill loadout) ----------------

export interface AgentConfig {
  agent_id: string;
  skills_allowed: string[];
  skills_required: string[];
  defaults: { skills_allowed: string[]; skills_required: string[] };
  available_skills: string[];
  tools: string[];
}

export function useAgentConfig(agentId: string | null | undefined) {
  return useQuery({
    queryKey: ["agent-config", agentId],
    queryFn: () => get<AgentConfig>(`/agents/${agentId}/config`),
    enabled: !!agentId,
    staleTime: 30_000,
  });
}

export function useSaveAgentSkills() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: { agentId: string; skills_allowed: string[]; skills_required: string[] }) =>
      post<{ ok: boolean }>(`/agents/${p.agentId}/skills`, {
        skills_allowed: p.skills_allowed,
        skills_required: p.skills_required,
      }),
    onSuccess: (_d, p) => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      qc.invalidateQueries({ queryKey: ["agent-config", p.agentId] });
      toast.success("Skill loadout saved", {
        description: "Reflected here now; takes effect on the agent's next (cold) start.",
      });
    },
    onError: (e) =>
      toast.error("Couldn't save the skill loadout", { description: String((e as Error).message) }),
  });
}

// ---------------- Admin · cron control ----------------

// Optional admin token (sent as X-Admin-Token). The cron panel is open until
// ADMIN_SEED_TOKEN is set on the service; once it is, paste the token here and
// it's persisted to localStorage + attached to admin calls.
const ADMIN_TOKEN_KEY = "hg_admin_token";
export function getAdminToken(): string {
  try { return localStorage.getItem(ADMIN_TOKEN_KEY) ?? ""; } catch { return ""; }
}
export function setAdminToken(t: string): void {
  try { t ? localStorage.setItem(ADMIN_TOKEN_KEY, t) : localStorage.removeItem(ADMIN_TOKEN_KEY); } catch { /* ignore */ }
}
function adminHeaders(): Record<string, string> {
  const t = getAdminToken();
  return t ? { "X-Admin-Token": t } : {};
}

export interface Cron {
  name: string;
  kind: "job" | "endpoint";
  schedule: string;
  description: string;
  danger: boolean;
  target?: string;
}
export interface CronsResponse {
  secured: boolean;
  region: string;
  crons: Cron[];
}

export function useCrons() {
  return useQuery({
    queryKey: ["admin-crons"],
    queryFn: async () => {
      const r = await fetch(`${BASE}/admin/crons`, { headers: adminHeaders() });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      return r.json() as Promise<CronsResponse>;
    },
    staleTime: 60_000,
  });
}

export function useRunCron() {
  return useMutation({
    mutationFn: async (name: string) => {
      const r = await fetch(`${BASE}/admin/crons/${name}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...adminHeaders() },
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error((body as { detail?: string })?.detail || `${r.status} ${r.statusText}`);
      }
      return r.json() as Promise<{ ok: boolean; name: string; kind: string; status?: string; result?: unknown }>;
    },
    onSuccess: (data) =>
      toast.success(`Triggered ${data.name}`, {
        description: data.kind === "job"
          ? "Job execution started — check Cloud Run / the relevant page for results."
          : "Ran in-process.",
      }),
    onError: (e) =>
      toast.error("Couldn't trigger that cron", { description: String((e as Error).message) }),
  });
}

interface DraftJob {
  job_id: string;
  status: "pending" | "running" | "done" | "failed";
  result?: Record<string, unknown>;
  error?: string;
  started_at?: string;
  completed_at?: string;
}

const DRAFT_POLL_INTERVAL_MS = 2_000;
// ~8 min ceiling. Must exceed the web-api → pipeline read timeout (300s in
// services/web_api/routers/drafting.py) so the UI sees the final done/failed
// state rather than giving up early — gemini-2.5-pro drafts run ~4-5 min,
// longer on a cold start.
const DRAFT_POLL_MAX_ATTEMPTS = 240;
// A 4-5 min draft makes ~150 poll requests through Firebase Hosting → Cloud
// Run; the odd transient 502/503 (or a wifi blip) is expected. One bad poll
// must NOT fail the whole draft — only losing this many CONSECUTIVE polls
// counts as having lost the job.
const DRAFT_POLL_MAX_CONSECUTIVE_MISSES = 5;

export function useDraft() {
  return useMutation({
    mutationFn: async (p: {
      icp_segment: string;
      channel: string;
      topic_hint?: string;
      // Optional secondary angles to also cover (sharpens content accuracy).
      subtopics?: string[];
      experiment_id?: string;
      // Optional single-agent handoff. Omitted = full drafting team.
      // See ROUTING_OPTIONS in Drafting.tsx for the supported values.
      agent_id?: string;
      // Visualization style for ImageBrief: contextual | infographic |
      // excalidraw | auto (let the agent choose per channel + topic).
      visual_pref?: string;
    }): Promise<Record<string, unknown>> => {
      const { job_id } = await post<{ job_id: string; status: string }>("/draft", p);
      let misses = 0;
      for (let i = 0; i < DRAFT_POLL_MAX_ATTEMPTS; i++) {
        await new Promise((r) => setTimeout(r, DRAFT_POLL_INTERVAL_MS));
        let job: DraftJob;
        try {
          job = await get<DraftJob>(`/draft/${job_id}`);
          misses = 0;
        } catch {
          // Transient poll failure — the job is still running server-side.
          if (++misses >= DRAFT_POLL_MAX_CONSECUTIVE_MISSES) {
            throw new Error(
              `lost contact with draft job ${job_id} after ${misses} ` +
              "consecutive poll failures — check the Queue in a minute; " +
              "the draft may still land there"
            );
          }
          continue;
        }
        if (job.status === "done" && job.result) return job.result;
        if (job.status === "failed") {
          throw new Error(job.error || "draft job failed");
        }
      }
      throw new Error(
        `draft job ${job_id} did not complete after ` +
        `${(DRAFT_POLL_MAX_ATTEMPTS * DRAFT_POLL_INTERVAL_MS) / 1000}s`
      );
    },
  });
}

// ---------------------------------------------------------------------------
// Founder Dashboard (ROI) + learning receipts/curve
// ---------------------------------------------------------------------------

export interface FounderDashboardData {
  days: number;
  assets: {
    drafted: number;
    decided: number;
    approved: number;
    edited: number;
    rejected: number;
    published: number;
    by_channel: Record<string, number>;
  };
  approval_trend: { day: string; approve: number; edit: number; reject: number; rate: number }[];
  outcomes: { filled: number; pending: number; total_value: number };
  roi: {
    founder_minutes: number;
    freelancer_equivalent_usd: number;
    api_cost_usd: number;
    leverage: number | null;
    assumptions: { note?: string } & Record<string, unknown>;
  };
}

export function useFounderDashboard(days = 7) {
  return useQuery({
    queryKey: ["founder-dashboard", days],
    queryFn: () => get<FounderDashboardData>(`/founder-dashboard?days=${days}`),
    refetchInterval: 60_000,
  });
}

export interface LearningReceipt {
  channel: string | null;
  decision: "reject" | "edit";
  reason: string;
  decided_at: string | null;
  before: { telemetry_id: string; ts: string; scores: Record<string, number>; snippet: string };
  after: { telemetry_id: string; ts: string; scores: Record<string, number>; snippet: string };
  deltas: Record<string, number>;
  top_rubric: string;
  top_delta: number;
  improved: boolean;
}

export function useLearningReceipts(days = 28, limit = 8) {
  return useQuery({
    queryKey: ["learning-receipts", days, limit],
    queryFn: () => get<LearningReceipt[]>(`/learning-receipts?days=${days}&limit=${limit}`),
    refetchInterval: 60_000,
  });
}

export interface LearningCurvePoint {
  day: string;
  quality: number | null;
  n: number;
  rejections: number;
}

export function useLearningCurve(days = 28) {
  return useQuery({
    queryKey: ["learning-curve", days],
    queryFn: () => get<LearningCurvePoint[]>(`/learning-curve?days=${days}`),
  });
}
