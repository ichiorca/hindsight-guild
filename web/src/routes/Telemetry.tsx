import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend, ReferenceLine } from "recharts";
import { Activity, Shield, Sparkles, Database, TrendingUp, Quote, ExternalLink } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { useRubricTrend, useWeekSummary, useNegatives, useAeoCitedBy } from "@/lib/api";
import type { AeoCitation } from "@/lib/api";
import { pct, channelLabel } from "@/lib/utils";

const LINE_COLORS = ["#0090db", "#00b8b8", "#5fb8eb"]; // ocean, teal, sky

export default function TelemetryPage() {
  const trend = useRubricTrend(28);
  const summary = useWeekSummary();
  const negatives = useNegatives();
  const citations = useAeoCitedBy(28);

  const channels = Array.from(new Set((trend.data ?? []).map((p) => p.channel)));

  const merged = mergeByDay(trend.data ?? []);

  return (
    <div className="flex flex-col">
      <PageHeader
        title="Quality Signals"
        description="How the team's output is trending: brand-voice scores, safety blocks, and growth of the do-not-repeat library."
      />

      <div className="p-5 sm:p-8 space-y-6 max-w-7xl">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Activity className="h-4 w-4 text-primary" /> Brand voice trend (28d)
            </CardTitle>
            <CardDescription>Per channel. Drop &gt; 0.10 from trailing baseline opens an investigation.</CardDescription>
          </CardHeader>
          <CardContent>
            {trend.isLoading && <Skeleton className="h-64" />}
            {!trend.isLoading && (!trend.data || trend.data.length === 0) && (
              <TelemetryGetStarted />
            )}
            {!trend.isLoading && merged.length > 0 && (
              <ResponsiveContainer width="100%" height={320}>
                <LineChart data={merged}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="day" tick={{ fontSize: 11 }} />
                  <YAxis domain={[0.4, 1]} tick={{ fontSize: 11 }}
                    tickFormatter={(v) => pct(Number(v), 0)}
                    label={{ value: "Brand-voice score", angle: -90, position: "insideLeft", style: { fontSize: 11, fill: "var(--muted-foreground)" } }} />
                  <Tooltip contentStyle={{ borderRadius: 8, fontSize: 12 }}
                    formatter={(v: number | string) => pct(Number(v), 1)} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <ReferenceLine y={0.7} stroke="#ef4444" strokeDasharray="4 4" label={{ value: "Quality floor", fontSize: 10, fill: "#ef4444" }} />
                  {channels.map((ch, i) => (
                    <Line
                      key={ch}
                      type="monotone"
                      dataKey={`bv_${ch}`}
                      name={channelLabel(ch)}
                      stroke={LINE_COLORS[i % LINE_COLORS.length]}
                      strokeWidth={2}
                      dot={{ r: 2 }}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Quote className="h-4 w-4 text-primary" /> Cited by AI engines (28d)
            </CardTitle>
            <CardDescription>
              Times your content was cited by Perplexity, ChatGPT, Google
              AI Overviews, or Claude web search. Tracked manually today;
              automatic detection arrives with the paid integrations.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <AeoCitedByTile citations={citations.data} loading={citations.isLoading} />
          </CardContent>
        </Card>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Shield className="h-4 w-4 text-primary" /> Safety filter activity
              </CardTitle>
              <CardDescription>How often the safety filter blocked unsafe content this week.</CardDescription>
            </CardHeader>
            <CardContent>
              {summary.isLoading && <Skeleton className="h-24" />}
              {summary.data && (
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Total actions</p>
                    <p className="text-3xl font-semibold tabular-nums mt-1">{summary.data.total_actions}</p>
                  </div>
                  <div>
                    <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Blocks</p>
                    <p className="text-3xl font-semibold tabular-nums mt-1 text-destructive">
                      {summary.data.armor_blocks}
                    </p>
                    <p className="text-[11px] text-muted-foreground mt-1">
                      {pct(summary.data.armor_blocks / Math.max(1, summary.data.total_actions), 1)} of all calls
                    </p>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Do-not-repeat library</CardTitle>
              <CardDescription>Past mistakes the team won't make again — feeds review.</CardDescription>
            </CardHeader>
            <CardContent>
              {negatives.isLoading && <Skeleton className="h-24" />}
              {negatives.data && (
                <div className="space-y-2">
                  <p className="text-3xl font-semibold tabular-nums">{negatives.data.length}</p>
                  <p className="text-xs text-muted-foreground">
                    Across categories. The Review Agent's grounding pulls the 3 most recent per (channel, category).
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

const PLATFORM_LABEL: Record<string, string> = {
  perplexity:   "Perplexity",
  chatgpt:      "ChatGPT",
  google_aio:   "Google AI Overviews",
  claude:       "Claude",
};

function AeoCitedByTile({ citations, loading }: {
  citations: AeoCitation[] | undefined;
  loading: boolean;
}) {
  if (loading) return <Skeleton className="h-32" />;
  const rows = citations ?? [];
  if (rows.length === 0) {
    return (
      <div className="space-y-3">
        <p className="text-muted-foreground text-sm">
          No AI-engine citations recorded yet.
        </p>
        <p className="text-[13px] text-muted-foreground leading-relaxed">
          When Perplexity, ChatGPT, Google AI Overviews, or Claude cites one
          of your posts, it'll show up here. Automatic detection turns on with
          the paid search integrations.
        </p>
      </div>
    );
  }
  // Group counts by platform for the headline numbers; full table below.
  const byPlatform = rows.reduce<Record<string, number>>((acc, r) => {
    const k = r.platform ?? "other";
    acc[k] = (acc[k] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {Object.entries(byPlatform).map(([k, n]) => (
          <div key={k} className="rounded-md border p-3">
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
              {PLATFORM_LABEL[k] ?? k}
            </p>
            <p className="text-2xl font-semibold tabular-nums mt-1">{n}</p>
          </div>
        ))}
      </div>
      <div className="border rounded-md divide-y max-h-72 overflow-y-auto">
        {rows.slice(0, 30).map((r) => (
          <div key={r.id} className="p-2.5 text-[13px] flex items-start gap-3">
            <span className="font-mono text-[11px] text-muted-foreground shrink-0 w-28">
              {r.platform ? (PLATFORM_LABEL[r.platform] ?? r.platform) : "—"}
            </span>
            <div className="flex-1 min-w-0">
              {r.query && <p className="text-muted-foreground italic truncate">"{r.query}"</p>}
              {r.cited_url && (
                <a
                  href={r.cited_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary text-[12px] inline-flex items-center gap-1 hover:underline"
                >
                  <ExternalLink className="h-3 w-3" />
                  {new URL(r.cited_url).pathname}
                </a>
              )}
            </div>
            {r.evidence_url && (
              <a
                href={r.evidence_url}
                target="_blank"
                rel="noreferrer"
                title="Source thread"
                className="text-muted-foreground hover:text-foreground"
              >
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function mergeByDay(points: { day: string; channel: string; mean_brand_voice: number }[]) {
  const byDay = new Map<string, Record<string, number | string>>();
  for (const p of points) {
    const key = p.day;
    if (!byDay.has(key)) byDay.set(key, { day: key });
    byDay.get(key)![`bv_${p.channel}`] = p.mean_brand_voice;
  }
  return Array.from(byDay.values()).sort((a, b) => String(a.day).localeCompare(String(b.day)));
}

/**
 * Brand voice trend lives in BigQuery (telemetry.actions joined with the
 * Vertex AI Evaluation Service scores). LOCAL_DEV mode has no BQ, so the
 * endpoint returns []. Rather than just "No trend data yet", explain
 * what would fill it and how to enable scoring.
 */
function TelemetryGetStarted() {
  return (
    <div className="space-y-4 max-w-3xl">
      <div className="flex items-start gap-3 rounded-md border border-info/30 bg-info/5 p-4">
        <Sparkles className="h-5 w-5 text-info shrink-0 mt-0.5" />
        <div className="text-sm">
          <p className="font-medium">No telemetry yet — here's how this page is fed.</p>
          <p className="text-muted-foreground mt-1">
            The brand-voice trend chart plots the per-channel
            <code className="font-mono mx-1 text-[12px] bg-subtle/60 px-1.5 py-0.5 rounded">eval_scores.brand_voice</code>
            metric over the last 28 days. Each draft your agents produce gets graded by Vertex AI's
            Gen AI Evaluation Service (the same scoring used in
            <code className="font-mono mx-1 text-[12px] bg-subtle/60 px-1.5 py-0.5 rounded">agents/_common.py</code>),
            then dual-written to BigQuery + the Mongo
            <code className="font-mono mx-1 text-[12px] bg-subtle/60 px-1.5 py-0.5 rounded">actions</code> mirror.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-[13px]">
        <div className="rounded-md border p-3">
          <div className="flex items-center gap-2 font-medium mb-1">
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-primary/15 text-primary text-[11px] font-semibold">1</span>
            Generate drafts
          </div>
          <p className="text-muted-foreground">
            From the <b>Drafting</b> page, run a few drafts across multiple
            channels (LinkedIn, Substack, Lifecycle Email, Paid). Each fires
            the full Research → Critique → Reviser → Review pipeline.
          </p>
        </div>
        <div className="rounded-md border p-3">
          <div className="flex items-center gap-2 font-medium mb-1">
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-primary/15 text-primary text-[11px] font-semibold">2</span>
            Eval scoring fires
          </div>
          <p className="text-muted-foreground">
            Reviser's <code className="font-mono">after_agent_callback</code> hits
            Vertex AI Eval and writes per-metric scores
            (brand_voice, claim_support, originality, etc.) into
            <code className="font-mono">telemetry.actions</code>.
          </p>
        </div>
        <div className="rounded-md border p-3">
          <div className="flex items-center gap-2 font-medium mb-1">
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-primary/15 text-primary text-[11px] font-semibold">3</span>
            Trend lights up
          </div>
          <p className="text-muted-foreground">
            <code className="font-mono">/api/rubric/trend</code> daily-bucket
            aggregates by channel and the chart plots from there. Drop &gt; 0.10
            from the trailing baseline opens a drift investigation.
          </p>
        </div>
      </div>

      <div className="flex gap-2 items-start rounded-md border border-warning/30 bg-warning/5 p-3 text-[12px]">
        <Database className="h-4 w-4 shrink-0 text-warning mt-0.5" />
        <span className="text-muted-foreground">
          <b>LOCAL_DEV note:</b> the eval scorer needs Vertex AI auth
          (gcloud ADC). In local-dev, drafts are still graded by the Review
          agent qualitatively (see flags on each item in the Queue), but the
          quantitative trend stays empty until you deploy to a project with
          a service account that can hit the Gen AI Evaluation Service.
        </span>
      </div>

      <div className="flex gap-2 items-start rounded-md border p-3 text-[12px]">
        <TrendingUp className="h-4 w-4 shrink-0 text-muted-foreground mt-0.5" />
        <span className="text-muted-foreground">
          Want a quick taste? The <b>Queue</b> page shows the same eval_scores
          per draft via the Ship Readiness badge — even without aggregate
          trend data, you can see the per-asset rubric.
        </span>
      </div>
    </div>
  );
}
