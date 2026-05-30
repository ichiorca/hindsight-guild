/**
 * LiveTicker — slim sticky bar showing "what's running right now".
 *
 * Connects to /api/ws/live (WebSocket) and re-renders on every push from
 * the server. The server pushes:
 *   - On every draft-job state change (kicked off by _job_set)
 *   - Every 5s as a wall-clock tick (catches cron + A2A-direct activity)
 *
 * Falls back to a one-shot REST fetch from /api/live/now if the WS can't
 * connect, with exponential-backoff reconnect attempts. Renders nothing
 * when no jobs are in flight and no agents have emitted telemetry in the
 * last 60s, so the bar collapses out of the way when the system is idle.
 */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { AGENTS, getAgent } from "@/lib/agents";
import { AgentBadge } from "@/components/AgentBadge";
import { humanizeActionType } from "@/lib/humanize";

interface ActiveJob {
  job_id: string;
  status: "pending" | "running";
  agent_id: string;
  channel: string;
  icp_segment: string;
  topic_hint: string;
  started_at?: string;
}

interface RecentAgent {
  agent_id: string;
  last_seen: string | null;
  last_action: string | null;
  last_channel: string | null;
  count_60s: number;
}

interface LiveNowPayload {
  server_time: string;
  active_jobs: ActiveJob[];
  recent_agents: RecentAgent[];
}

/**
 * Subscribe to /api/ws/live with auto-reconnect.
 * Returns the latest payload received.
 *
 * Reconnect strategy: capped exponential backoff (1s → 2s → 4s → 8s → 15s).
 * On a successful connect, the server immediately pushes a snapshot so we
 * don't need a separate REST fallback for the first paint — the WS itself
 * is the source of truth.
 *
 * If the WS keeps failing (no /api/ws/live mount, network blocked), we
 * fall back to a one-shot REST fetch every 15s so the UI still updates.
 */
function useLiveSocket(): LiveNowPayload | null {
  const [data, setData] = useState<LiveNowPayload | null>(null);
  // Hold the socket + reconnect timer across renders without retriggering effects.
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttempts = useRef(0);
  const reconnectTimer = useRef<number | null>(null);
  const restFallbackTimer = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      // Same-origin URL for the WS so vite's proxy can forward it to the
      // FastAPI backend in dev. In production they're served from the
      // same origin, so this works in both environments.
      const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${wsProto}//${window.location.host}/api/ws/live`;

      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch {
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttempts.current = 0;
        // Stop the REST fallback once we have a live socket.
        if (restFallbackTimer.current != null) {
          window.clearInterval(restFallbackTimer.current);
          restFallbackTimer.current = null;
        }
      };

      ws.onmessage = (ev) => {
        try {
          const payload = JSON.parse(ev.data) as LiveNowPayload;
          setData(payload);
        } catch {
          // Ignore malformed frames; the next push will overwrite.
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        scheduleReconnect();
      };
      ws.onerror = () => {
        // The browser will fire onclose right after; let that drive reconnect.
        // Also kick off the REST fallback so the UI keeps updating while
        // we're trying to get the socket back.
        startRestFallback();
      };
    };

    const scheduleReconnect = () => {
      if (cancelled) return;
      const attempt = reconnectAttempts.current;
      reconnectAttempts.current = Math.min(attempt + 1, 5);
      const backoffMs = Math.min(1000 * Math.pow(2, attempt), 15_000);
      reconnectTimer.current = window.setTimeout(connect, backoffMs);
      startRestFallback();
    };

    const startRestFallback = () => {
      if (restFallbackTimer.current != null) return;
      const fetchOnce = async () => {
        try {
          const r = await fetch("/api/live/now");
          if (r.ok) setData(await r.json());
        } catch {
          // Network down — leave stale data on screen.
        }
      };
      fetchOnce();
      restFallbackTimer.current = window.setInterval(fetchOnce, 15_000);
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer.current != null) window.clearTimeout(reconnectTimer.current);
      if (restFallbackTimer.current != null) window.clearInterval(restFallbackTimer.current);
      if (wsRef.current) {
        // The cleanup runs on unmount; explicitly close so we don't leak
        // a dangling socket on hot-reload during development.
        try { wsRef.current.close(); } catch { /* noop */ }
        wsRef.current = null;
      }
    };
  }, []);

  return data;
}

export function LiveTicker() {
  const data = useLiveSocket();
  // Learning events were briefly surfaced as ticker chips during the
  // self-learning visibility revamp. Removed because the chip mount
  // re-rendered the LiveTicker AFTER `networkidle` had settled,
  // shifting page layout under Playwright's resolved click coords
  // and breaking the Queue reject flow under suite-wide contention.
  // The visibility win is well-served by the sidebar badge + /learning
  // page; the LiveTicker stays focused on its original job: showing
  // live agent + job activity right now.

  const activeJobs = data?.active_jobs ?? [];
  const recentAgents = data?.recent_agents ?? [];

  if (activeJobs.length === 0 && recentAgents.length === 0) {
    // System idle — collapse the bar entirely so we don't waste space.
    return null;
  }

  return (
    <div className="relative z-20 border-b bg-card/95 backdrop-blur-md">
      <div className="px-5 sm:px-8 py-2 flex items-center gap-3 overflow-x-auto">
        <div className="flex items-center gap-1.5 shrink-0 text-[11px] uppercase tracking-wider text-muted-foreground font-medium">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-success" />
          </span>
          Live
        </div>

        {/* Active jobs come first (UI-initiated; we know agent + topic) */}
        {activeJobs.map((job) => (
          <JobChip key={job.job_id} job={job} />
        ))}

        {/* Recent agents — anything that emitted telemetry in the last 60s
            but isn't already represented as an active job */}
        {recentAgents
          .filter((a) => !activeJobs.some((j) => j.agent_id === a.agent_id))
          .map((a) => (
            <RecentAgentChip key={a.agent_id} agent={a} />
          ))}

        <Link
          to="/live"
          className="ml-auto shrink-0 inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
        >
          See all
          <ChevronRight className="h-3 w-3" />
        </Link>
      </div>
    </div>
  );
}

// One in-flight job — agent + topic + channel.
function JobChip({ job }: { job: ActiveJob }) {
  const profile = getAgent(job.agent_id);
  // Render a concise, readable summary. Avoid the raw job_id.
  const verb = verbFor(job.agent_id);
  const subject = job.topic_hint
    ? `"${truncate(job.topic_hint, 40)}"`
    : (job.channel ? job.channel : "");

  return (
    <Link
      to={`/draft?agent=${encodeURIComponent(job.agent_id)}`}
      className="shrink-0 inline-flex items-center gap-2 px-2.5 py-1 rounded-full border border-primary/30 bg-primary/5 hover:bg-primary/10 transition-colors text-[12px]"
    >
      {profile && <AgentBadge id={profile.id} size="sm" />}
      <span className="font-medium">
        {profile?.shortName ?? job.agent_id}
      </span>
      <span className="text-muted-foreground">
        {verb} {subject}
      </span>
      <span
        className={cn(
          "inline-flex h-1.5 w-1.5 rounded-full",
          job.status === "running"
            ? "bg-primary animate-pulse"
            : "bg-muted-foreground/40",
        )}
        title={job.status}
      />
    </Link>
  );
}

// Agent recently active (saw a telemetry row in the last 60s) but no
// UI-initiated job — likely a cron or A2A-direct invocation.
function RecentAgentChip({ agent }: { agent: RecentAgent }) {
  const profile = getAgent(agent.agent_id);
  return (
    <Link
      to="/live"
      className="shrink-0 inline-flex items-center gap-2 px-2.5 py-1 rounded-full border bg-card hover:bg-muted/60 transition-colors text-[12px]"
    >
      {profile && <AgentBadge id={profile.id} size="sm" />}
      <span className="font-medium">
        {profile?.shortName ?? agent.agent_id}
      </span>
      <span className="text-muted-foreground">
        {humanizeActionType(agent.last_action) || "Active"}
      </span>
      {agent.count_60s > 1 && (
        <span className="text-[10px] font-mono text-muted-foreground bg-subtle/60 px-1.5 py-0.5 rounded">
          {agent.count_60s}×
        </span>
      )}
    </Link>
  );
}

// One-word verb per agent kind so the ticker reads like a sentence.
function verbFor(agentId: string): string {
  const verbs: Record<string, string> = {
    pipeline:              "drafting",
    content_agent:         "drafting",
    research_agent:        "researching",
    review_agent:          "reviewing",
    image_brief_agent:     "briefing image for",
    lifecycle_email_agent: "writing sequence for",
    paid_media_agent:      "proposing variants on",
    positioning_agent:     "proposing positioning for",
    customer_voice_agent:  "ingesting voice on",
    ops_qa_agent:          "running ops sweep",
    cmo_planner:           "composing memo for",
    self_critique_agent:   "critiquing skills for",
    analytics_agent:       "pulling analytics for",
  };
  return verbs[agentId] ?? "running";
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}

// Re-export the agent registry size for the sidebar copy.
export const AGENT_COUNT = Object.keys(AGENTS).length;
