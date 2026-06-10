import { useState } from "react";
import {
  Banknote, Clock3, Cpu, Send, TrendingUp, Sparkles,
} from "lucide-react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Legend, ComposedChart, Bar,
} from "recharts";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Empty } from "@/components/ui/Empty";
import { ErrorState } from "@/components/ui/ErrorState";
import { Button } from "@/components/ui/Button";
import { useFounderDashboard, useLearningCurve } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Founder Dashboard — the business-value view.
 *
 * Everything else in the app shows WHAT the guild did; this page shows what
 * it was WORTH: assets shipped, the founder-minutes they cost, what the same
 * work would bill from a freelancer, and what the API spend was. The
 * dollar/minute figures are estimates — the assumptions are returned by the
 * API and rendered in the footnote, never hidden.
 */
export default function FounderDashboardPage() {
  const [days, setDays] = useState(7);
  const { data, isLoading, isError, refetch } = useFounderDashboard(days);
  const curve = useLearningCurve(28);

  return (
    <div className="flex flex-col">
      <PageHeader
        eyebrow="Impact"
        title="Founder Dashboard"
        description="What the guild shipped, what it cost you, and what it saved — with every assumption on the table."
      >
        <div className="flex gap-1">
          {[7, 28].map((d) => (
            <Button
              key={d}
              variant={days === d ? "default" : "outline"}
              size="sm"
              onClick={() => setDays(d)}
            >
              {d}d
            </Button>
          ))}
        </div>
      </PageHeader>

      <div className="p-5 sm:p-8 space-y-6 max-w-7xl">
        {isLoading && !data && (
          <div className="space-y-4">
            <Skeleton className="h-32" />
            <Skeleton className="h-64" />
          </div>
        )}
        {isError && !data && (
          <ErrorState what="the founder dashboard" onRetry={() => refetch()} />
        )}

        {data && (
          <>
            <RoiBand data={data} />
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <ApprovalTrendCard data={data} />
              <QualityCurveCard points={curve.data} loading={curve.isLoading} />
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              <ChannelMixCard data={data} />
              <OutcomesCard data={data} />
              <FunnelCard data={data} />
            </div>
            <p className="text-[11px] text-muted-foreground leading-relaxed max-w-3xl">
              <b>How the estimates work:</b> {String(data.roi.assumptions.note ?? "")}{" "}
              Tune the rates with ROI_* environment variables on the web-api service.
            </p>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ROI band — the headline: assets · your minutes · freelancer-equivalent · API cost
// ---------------------------------------------------------------------------

function RoiBand({ data }: { data: NonNullable<ReturnType<typeof useFounderDashboard>["data"]> }) {
  const r = data.roi;
  const tiles = [
    {
      label: "Assets drafted",
      value: String(data.assets.drafted),
      hint: `${data.assets.published} published externally`,
      icon: Send,
      tone: "default" as const,
    },
    {
      label: "Your time",
      value: `${r.founder_minutes} min`,
      hint: `${data.assets.decided} decisions @ ~2 min each`,
      icon: Clock3,
      tone: "default" as const,
    },
    {
      label: "Freelancer-equivalent",
      value: `~$${r.freelancer_equivalent_usd.toLocaleString()}`,
      hint: "mid-market per-asset rates (est.)",
      icon: Banknote,
      tone: "success" as const,
    },
    {
      label: "API cost",
      value: `~$${r.api_cost_usd.toLocaleString()}`,
      hint: r.leverage ? `${r.leverage}x leverage vs freelancer (est.)` : "Gemini pipeline runs (est.)",
      icon: Cpu,
      tone: "info" as const,
    },
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <TrendingUp className="h-4 w-4 text-primary" /> Last {data.days} days, in founder terms
        </CardTitle>
        <CardDescription>
          {data.assets.drafted} assets · {r.founder_minutes} min of your time ·
          ~${r.freelancer_equivalent_usd.toLocaleString()} freelancer-equivalent ·
          ~${r.api_cost_usd.toLocaleString()} in API cost
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {tiles.map((t) => (
            <div
              key={t.label}
              className={cn(
                "rounded-lg border p-4",
                t.tone === "success" && "bg-success/5 border-success/20",
                t.tone === "info" && "bg-info/5 border-info/20",
              )}
            >
              <div className="flex items-center gap-2 text-muted-foreground mb-1.5">
                <t.icon className="h-3.5 w-3.5" />
                <span className="text-[11px] uppercase tracking-wider font-medium">{t.label}</span>
              </div>
              <p className={cn(
                "text-3xl font-semibold tabular-nums font-serif",
                t.tone === "success" && "text-success",
                t.tone === "info" && "text-info",
              )}>
                {t.value}
              </p>
              <p className="text-[11px] text-muted-foreground mt-1 leading-tight">{t.hint}</p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Approval-rate trend
// ---------------------------------------------------------------------------

function ApprovalTrendCard({ data }: { data: NonNullable<ReturnType<typeof useFounderDashboard>["data"]> }) {
  const trend = data.approval_trend;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Approval rate</CardTitle>
        <CardDescription>
          Share of decided drafts you shipped (approved or shipped-with-edits), per day.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {trend.length === 0 ? (
          <Empty title="No decisions in this window" description="Approve or reject drafts in the Queue to populate this." />
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={trend}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
              <XAxis dataKey="day" tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} />
              <YAxis
                domain={[0, 1]}
                tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                tickFormatter={(v) => `${Math.round(Number(v) * 100)}%`}
              />
              <Tooltip
                contentStyle={{ borderRadius: 8, fontSize: 12, border: "1px solid hsl(var(--border))" }}
                formatter={(v: number | string, name: string) =>
                  name === "rate" ? `${Math.round(Number(v) * 100)}%` : v}
              />
              <Line type="monotone" dataKey="rate" name="ship rate" stroke="#0090db" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Quality curve — daily quality index + rejections fed back into the rubric
// ---------------------------------------------------------------------------

function QualityCurveCard({ points, loading }: {
  points?: { day: string; quality: number | null; n: number; rejections: number }[];
  loading: boolean;
}) {
  const data = (points ?? []).filter((p) => p.quality != null || p.rejections > 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" /> Quality curve · 28 days
        </CardTitle>
        <CardDescription>
          Daily quality index (brand voice + claim support + AEO) with the rejections that taught it.
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
// Channel mix / outcomes / decision funnel
// ---------------------------------------------------------------------------

function ChannelMixCard({ data }: { data: NonNullable<ReturnType<typeof useFounderDashboard>["data"]> }) {
  const entries = Object.entries(data.assets.by_channel).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, n]) => n));
  return (
    <Card>
      <CardHeader>
        <CardTitle>Channel mix</CardTitle>
        <CardDescription>Assets drafted per channel.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {entries.length === 0 && <Empty title="Nothing drafted yet" description="" />}
        {entries.map(([ch, n]) => (
          <div key={ch} className="flex items-center gap-2 text-sm">
            <span className="w-24 shrink-0 text-muted-foreground">{ch}</span>
            <div className="flex-1 h-2 rounded bg-muted overflow-hidden">
              <div className="h-full bg-primary/70" style={{ width: `${(n / max) * 100}%` }} />
            </div>
            <span className="tabular-nums w-8 text-right">{n}</span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function OutcomesCard({ data }: { data: NonNullable<ReturnType<typeof useFounderDashboard>["data"]> }) {
  const o = data.outcomes;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Attributed outcomes</CardTitle>
        <CardDescription>Engagement slots filled by GA4 / HubSpot / Ads attribution.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex items-baseline gap-2">
          <p className="text-3xl font-semibold tabular-nums font-serif">{o.filled}</p>
          <p className="text-sm text-muted-foreground">slots filled</p>
        </div>
        <div className="mt-2 space-y-1 text-sm text-muted-foreground">
          <p>{o.pending} pending (filled within 72h of publish)</p>
          <p>
            total attributed engagement{" "}
            <span className="text-foreground tabular-nums">{o.total_value.toFixed(2)}</span>
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function FunnelCard({ data }: { data: NonNullable<ReturnType<typeof useFounderDashboard>["data"]> }) {
  const a = data.assets;
  const rows = [
    { label: "Drafted", n: a.drafted, tone: "" },
    { label: "Decided", n: a.decided, tone: "" },
    { label: "Approved", n: a.approved, tone: "text-success" },
    { label: "Edited + shipped", n: a.edited, tone: "text-info" },
    { label: "Rejected → lessons", n: a.rejected, tone: "text-warning" },
    { label: "Published", n: a.published, tone: "text-success" },
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Decision funnel</CardTitle>
        <CardDescription>Every rejection becomes a negative example the rubric learns from.</CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="space-y-1.5 text-sm">
          {rows.map((r) => (
            <li key={r.label} className="flex items-center justify-between">
              <span className="text-muted-foreground">{r.label}</span>
              <Badge variant="muted" className={cn("tabular-nums", r.tone)}>{r.n}</Badge>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
