import { Brain, ArrowRight, Lightbulb, Trophy, Sparkles, RefreshCw, X, Receipt } from "lucide-react";
import {
  ComposedChart, Line, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Legend,
} from "recharts";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Empty } from "@/components/ui/Empty";
import { ErrorState } from "@/components/ui/ErrorState";
import {
  useLearningSummary, useRunSelfCritiqueNow, useSelfCritiqueRuns,
  useLearningCurve, useLearningReceipts,
} from "@/lib/api";
import type { LearningSummary, SelfCritiqueRun, LearningReceipt } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { relativeTime } from "@/lib/humanize";

/**
 * Self-Learning — the closed-loop story in one page.
 *
 * Designed for a business user (founder, exec, a future hire) who wants
 * to answer in 30 seconds:
 *   1. Is this thing getting better?
 *   2. What changed this week?
 *   3. What will it learn next?
 *
 * The page composes four panels:
 *   - "This week" KPI band — five tiles with the closed-loop counters
 *   - "The loop" — a 4-stage visual showing where work is right now
 *   - "Recent events" — feed of miner runs, decisions, promotions
 *   - "Per miner" — 28-day per-miner activity table
 *
 * All numbers come from /api/self-critique/summary; no client-side
 * fan-out. The page refetches every 60s so a nightly run shows up
 * within a minute even if the founder leaves the tab open.
 */
export default function LearningPage() {
  const { data, isLoading, isError, refetch } = useLearningSummary(7);
  const runNow = useRunSelfCritiqueNow();
  const runs = useSelfCritiqueRuns(14);
  const curve = useLearningCurve(28);
  const receipts = useLearningReceipts(28, 6);

  return (
    <div className="flex flex-col">
      <PageHeader
        eyebrow="System health"
        title="Self-Learning"
        description="How the team gets better over time. Nightly miners scan recent telemetry, surface patterns as proposals, and your decisions roll into the next skill version."
      >
        <Button
          variant="outline"
          size="sm"
          onClick={() => runNow.mutate()}
          disabled={runNow.isPending}
          title="Trigger an immediate miner run"
        >
          <RefreshCw className={runNow.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
          Run miners now
        </Button>
      </PageHeader>

      <div className="p-5 sm:p-8 space-y-6 max-w-7xl">
        {isLoading && !data && (
          <div className="space-y-4">
            <Skeleton className="h-24" />
            <Skeleton className="h-40" />
            <Skeleton className="h-64" />
          </div>
        )}

        {isError && !data && (
          <ErrorState what="the self-learning summary" onRetry={() => refetch()} />
        )}

        {data && (
          <>
            <KpiBand summary={data} />
            <TheLoop summary={data} />
            <Receipts receipts={receipts.data} loading={receipts.isLoading} />
            <LearningCurve points={curve.data} loading={curve.isLoading} />
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <RecentEvents events={data.events} />
              <PerMinerTable rows={data.per_miner_28d} />
            </div>
            <RunsHistory runs={runs.data} loading={runs.isLoading} />
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Receipts — concrete before→after proof: a rejected draft, the next draft on
// the same channel, and the rubric that moved. The judge doesn't have to take
// "one row in MongoDB changed behavior" on faith — here's the row and the delta.
// ---------------------------------------------------------------------------

const RUBRIC_LABELS: Record<string, string> = {
  brand_voice: "Brand voice",
  claim_support: "Claim support",
  claim_risk: "Claim risk",
  icp_relevance: "ICP relevance",
  originality: "Originality",
  conversion_intent: "Conversion intent",
  answer_extractability: "AEO",
};

function Receipts({ receipts, loading }: { receipts?: LearningReceipt[]; loading: boolean }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Receipt className="h-4 w-4 text-primary" /> Learning receipts
        </CardTitle>
        <CardDescription>
          A rejection becomes a negative example in MongoDB; the next draft on that channel is graded
          against it. Before → after, with the rubric that moved.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading && <Skeleton className="h-32" />}
        {!loading && (receipts?.length ?? 0) === 0 && (
          <Empty
            icon={Receipt}
            title="No receipts yet"
            description="Reject a draft in the Queue with a reason, then draft a similar brief — the before/after pair lands here."
          />
        )}
        {!loading && (receipts?.length ?? 0) > 0 && (
          <ul className="divide-y">
            {receipts!.map((r, i) => {
              const before = r.before.scores[r.top_rubric];
              const after = r.after.scores[r.top_rubric];
              return (
                <li key={i} className="py-3 first:pt-0 last:pb-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge variant="muted" className="text-[10px]">{r.channel}</Badge>
                    <Badge
                      variant={r.decision === "reject" ? "destructive" : "muted"}
                      className="text-[10px]"
                    >
                      {r.decision === "reject" ? "rejected" : "edited"}
                    </Badge>
                    {r.reason && (
                      <span className="text-xs text-muted-foreground italic truncate max-w-md">
                        “{r.reason}”
                      </span>
                    )}
                  </div>
                  <div className="mt-2 flex items-center gap-3 flex-wrap text-sm">
                    <span className="text-muted-foreground">
                      {RUBRIC_LABELS[r.top_rubric] ?? r.top_rubric}
                    </span>
                    <span className="tabular-nums">{(before * 100).toFixed(0)}%</span>
                    <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
                    <span className={cn(
                      "tabular-nums font-semibold",
                      r.improved ? "text-success" : "text-destructive",
                    )}>
                      {(after * 100).toFixed(0)}%
                    </span>
                    <Badge
                      variant={r.improved ? "muted" : "destructive"}
                      className={cn("text-[10px] tabular-nums", r.improved && "text-success")}
                    >
                      {r.top_delta > 0 ? "+" : ""}{(r.top_delta * 100).toFixed(0)} pts
                    </Badge>
                  </div>
                  {(r.before.snippet || r.after.snippet) && (
                    <div className="mt-1.5 grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px] text-muted-foreground">
                      <p className="truncate"><b>before:</b> {r.before.snippet}</p>
                      <p className="truncate"><b>after:</b> {r.after.snippet}</p>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Learning curve — daily quality index vs the rejections that taught it
// ---------------------------------------------------------------------------

function LearningCurve({ points, loading }: {
  points?: { day: string; quality: number | null; n: number; rejections: number }[];
  loading: boolean;
}) {
  const data = (points ?? []).filter((p) => p.quality != null || p.rejections > 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Learning curve · 28 days</CardTitle>
        <CardDescription>
          Daily quality index (brand voice + claim support + AEO) over the rejections fed back into the rubric.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading && <Skeleton className="h-56" />}
        {!loading && data.length === 0 && (
          <Empty title="No scored drafts yet" description="Draft something — every run is rubric-scored." />
        )}
        {!loading && data.length > 0 && (
          <ResponsiveContainer width="100%" height={220}>
            <ComposedChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
              <XAxis dataKey="day" tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} />
              <YAxis
                yAxisId="q"
                domain={[0.5, 1]}
                tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                tickFormatter={(v) => `${Math.round(Number(v) * 100)}%`}
              />
              <YAxis yAxisId="rej" orientation="right" allowDecimals={false}
                tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} />
              <Tooltip
                contentStyle={{ borderRadius: 8, fontSize: 12, border: "1px solid hsl(var(--border))" }}
                formatter={(v: number | string, name: string) =>
                  name === "quality index" ? `${(Number(v) * 100).toFixed(1)}%` : v}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar yAxisId="rej" dataKey="rejections" name="rejections" fill="#e4a11b" opacity={0.5} />
              <Line yAxisId="q" type="monotone" dataKey="quality" name="quality index"
                stroke="#00b8b8" strokeWidth={2} dot={false} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// KPI band — "this week" headline numbers
// ---------------------------------------------------------------------------

function KpiBand({ summary }: { summary: LearningSummary }) {
  const w = summary.this_window;
  const acceptRate = w.proposals_emitted > 0
    ? (w.proposals_accepted / w.proposals_emitted)
    : null;

  const tiles = [
    {
      label: "Proposals emitted",
      value: w.proposals_emitted,
      hint: `${w.miner_runs} miner run${w.miner_runs === 1 ? "" : "s"} in ${summary.days}d`,
      tone: "default" as const,
    },
    {
      label: "Accepted",
      value: w.proposals_accepted,
      hint: acceptRate != null ? `${(acceptRate * 100).toFixed(0)}% accept-rate` : "—",
      tone: "success" as const,
    },
    {
      label: "Dismissed",
      value: w.proposals_dismissed,
      hint: "marked dismissed in window",
      tone: "warning" as const,
    },
    {
      label: "Promotions",
      value: w.promotions,
      hint: "skill versions promoted",
      tone: "info" as const,
    },
    {
      label: "Voice trend",
      value: summary.voice_delta != null
        ? (summary.voice_delta >= 0 ? "+" : "") + summary.voice_delta.toFixed(3)
        : "—",
      hint: summary.voice_delta != null
        ? `vs prior ${summary.days}d`
        : "not enough rubric data yet",
      tone: summary.voice_delta != null
        ? (summary.voice_delta >= 0 ? "success" as const : "destructive" as const)
        : "default" as const,
    },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" /> This week
        </CardTitle>
        <CardDescription>
          Last {summary.days} days. Updated every minute · runner fires nightly at 02:00 UTC.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          {tiles.map((t) => (
            <div key={t.label} className={cn(
              "rounded-lg border p-3 transition-colors",
              t.tone === "success" && "bg-success/5 border-success/20",
              t.tone === "warning" && "bg-warning/5 border-warning/20",
              t.tone === "info" && "bg-info/5 border-info/20",
              t.tone === "destructive" && "bg-destructive/5 border-destructive/20",
            )}>
              <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
                {t.label}
              </p>
              <p className={cn(
                "text-2xl font-semibold tabular-nums mt-1",
                t.tone === "success" && "text-success",
                t.tone === "warning" && "text-foreground",
                t.tone === "info" && "text-info",
                t.tone === "destructive" && "text-destructive",
              )}>
                {t.value}
              </p>
              <p className="text-[11px] text-muted-foreground mt-1 leading-tight">
                {t.hint}
              </p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// "The loop" — 4-stage visual
// ---------------------------------------------------------------------------

function TheLoop({ summary }: { summary: LearningSummary }) {
  const w = summary.this_window;
  // Live miners — names present in per_miner with any activity in 28d.
  const liveMiners = summary.per_miner_28d
    .filter((m) => m.runs > 0)
    .map((m) => m.miner);

  const stages: Array<{
    label: string;
    count: string | number;
    sub: string;
    icon: typeof Brain;
  }> = [
    {
      label: "Miners",
      icon: Brain,
      count: liveMiners.length,
      sub: liveMiners.join(", ") || "none active",
    },
    {
      label: "Proposals",
      icon: Lightbulb,
      count: w.proposals_emitted,
      sub: `${summary.pending_proposals} pending decision`,
    },
    {
      label: "Decisions",
      icon: Sparkles,
      count: w.proposals_accepted + w.proposals_dismissed,
      sub: `${w.proposals_accepted} accepted · ${w.proposals_dismissed} dismissed`,
    },
    {
      label: "Promotions",
      icon: Trophy,
      count: w.promotions,
      sub: "new skill versions live",
    },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle>The loop</CardTitle>
        <CardDescription>
          Where this week's work sits in the closed-loop pipeline.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 sm:grid-cols-7 gap-2 items-center">
          {stages.map((s, i) => (
            <div key={s.label} className="contents">
              <div className="rounded-lg border p-3 bg-card sm:col-span-1">
                <div className="flex items-center gap-2 text-muted-foreground mb-2">
                  <s.icon className="h-3.5 w-3.5" />
                  <span className="text-[11px] uppercase tracking-wider font-medium">
                    {s.label}
                  </span>
                </div>
                <p className="text-2xl font-semibold tabular-nums">{s.count}</p>
                <p className="text-[11px] text-muted-foreground leading-tight mt-0.5">
                  {s.sub}
                </p>
              </div>
              {i < stages.length - 1 && (
                <div className="hidden sm:flex items-center justify-center text-muted-foreground/60">
                  <ArrowRight className="h-4 w-4" />
                </div>
              )}
            </div>
          ))}
        </div>
        <p className="text-[11px] text-muted-foreground mt-4 leading-relaxed">
          Each <b>miner</b> scans recent telemetry every night and writes proposals.
          Pending <b>proposals</b> wait on Weekly Review. Your <b>decisions</b> (accept / dismiss)
          land as candidate skill versions that the promotion gate evaluates.
          A winning candidate becomes a <b>promotion</b> — a new "current" version
          that the drafting pipeline picks up immediately.
        </p>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Recent events feed
// ---------------------------------------------------------------------------

function RecentEvents({ events }: { events: LearningSummary["events"] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent events</CardTitle>
        <CardDescription>
          Miner runs, decisions, promotions — most-recent first.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {events.length === 0 && (
          <Empty
            icon={Sparkles}
            title="No events yet."
            description="The nightly runner hasn't fired since this window started. Click 'Run miners now' above to seed the page."
          />
        )}
        {events.length > 0 && (
          <ol className="relative">
            <span className="absolute left-3 top-2 bottom-2 w-px bg-border" aria-hidden />
            {events.map((e, i) => {
              const meta = EVENT_META[e.kind] ?? {
                icon: Sparkles,
                bg: "bg-muted text-muted-foreground",
              };
              const Icon = meta.icon;
              return (
                <li key={i} className="relative flex items-start gap-3 pl-0.5 pb-3 last:pb-0">
                  <span className={cn(
                    "relative z-10 inline-flex items-center justify-center h-6 w-6 rounded-full shrink-0 ring-2 ring-card",
                    meta.bg,
                  )}>
                    <Icon className="h-3 w-3" />
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline gap-2 flex-wrap">
                      <p className="text-sm">{e.summary}</p>
                      {e.miner && (
                        <Badge variant="muted" className="text-[10px]">{e.miner}</Badge>
                      )}
                    </div>
                    {e.ts && (
                      <p className="text-[11px] text-muted-foreground tabular-nums mt-0.5">
                        {relativeTime(e.ts)}
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

const EVENT_META = {
  miner_run: {
    icon: Brain,
    bg: "bg-primary/15 text-primary",
  },
  proposal_accepted: {
    icon: Lightbulb,
    bg: "bg-success/15 text-success",
  },
  proposal_dismissed: {
    icon: X,
    bg: "bg-warning/15 text-warning",
  },
  promotion: {
    icon: Trophy,
    bg: "bg-info/15 text-info",
  },
} as const;

// ---------------------------------------------------------------------------
// Per-miner activity table
// ---------------------------------------------------------------------------

function PerMinerTable({ rows }: { rows: LearningSummary["per_miner_28d"] }) {
  const totalEmitted = rows.reduce((a, r) => a + r.emitted, 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Per miner · 28 days</CardTitle>
        <CardDescription>
          Voice + negative + paid + AEO + signal. Wider window than KPIs so noisy miners surface.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {totalEmitted === 0 && (
          <p className="text-sm text-muted-foreground">
            No miner activity in the last 28 days yet — the nightly runner will pick up once you have
            enough decision data (7d of approvals for voice, edits for negative).
          </p>
        )}
        {totalEmitted > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[11px] uppercase tracking-wider text-muted-foreground border-b">
                  <th className="text-left py-2 pr-3">Miner</th>
                  <th className="text-right py-2 pr-3">Runs</th>
                  <th className="text-right py-2 pr-3">Emitted</th>
                  <th className="text-right py-2 pr-3">Accepted</th>
                  <th className="text-right py-2 pr-3">Dismissed</th>
                  <th className="text-right py-2">Accept-rate</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const decided = r.accepted + r.dismissed;
                  const rate = decided > 0 ? r.accepted / decided : null;
                  return (
                    <tr key={r.miner} className="border-b last:border-b-0">
                      <td className="py-2 pr-3 font-medium">{r.miner}</td>
                      <td className="py-2 pr-3 text-right tabular-nums text-muted-foreground">{r.runs}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{r.emitted}</td>
                      <td className="py-2 pr-3 text-right tabular-nums text-success">{r.accepted}</td>
                      <td className="py-2 pr-3 text-right tabular-nums text-warning">{r.dismissed}</td>
                      <td className="py-2 text-right tabular-nums">
                        {rate != null ? `${(rate * 100).toFixed(0)}%` : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Recent runs — the nightly miner ticks, with errors surfaced. Makes a
// chronically-failing miner look DIFFERENT from one that simply found nothing
// (the per-miner errors are computed server-side but were otherwise invisible).
// ---------------------------------------------------------------------------

function RunsHistory({ runs, loading }: { runs?: SelfCritiqueRun[]; loading: boolean }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <RefreshCw className="h-4 w-4 text-muted-foreground" /> Recent miner runs
        </CardTitle>
        <CardDescription>The last nightly ticks. ⚠ flags a run where a miner errored.</CardDescription>
      </CardHeader>
      <CardContent>
        {loading && <Skeleton className="h-24" />}
        {!loading && (runs?.length ?? 0) === 0 && (
          <p className="text-sm text-muted-foreground">No runs recorded yet.</p>
        )}
        {!loading && (runs?.length ?? 0) > 0 && (
          <ul className="divide-y">
            {runs!.slice(0, 10).map((run) => {
              const errored = Object.entries(run.miners || {})
                .filter(([, m]) => (m.errors?.length ?? 0) > 0)
                .map(([name]) => name);
              return (
                <li key={run.id} className="py-2 flex items-center gap-3 text-sm">
                  <span className="text-muted-foreground tabular-nums w-28 shrink-0">
                    {run.started_at ? relativeTime(run.started_at) : "—"}
                  </span>
                  <span className="flex-1">
                    {run.total_proposals} proposal{run.total_proposals === 1 ? "" : "s"}
                  </span>
                  {errored.length > 0 ? (
                    <Badge variant="destructive" className="text-[10px]"
                      title={`Errors in: ${errored.join(", ")}`}>
                      ⚠ {errored.length} miner{errored.length === 1 ? "" : "s"} errored
                    </Badge>
                  ) : (
                    <Badge variant="muted" className="text-[10px]">{run.status ?? "ok"}</Badge>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
