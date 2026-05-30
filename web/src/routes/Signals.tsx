import { useState } from "react";
import { Link } from "react-router-dom";
import { Radar, RefreshCw, ExternalLink, Ban, Plus, Power, PowerOff } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { Empty } from "@/components/ui/Empty";
import {
  useSignals, useSignalSources, usePollSignalsNow,
  useSuppressSignal, useManualSignal,
} from "@/lib/api";
import type { Signal } from "@/lib/api";

const SOURCE_LABEL: Record<string, string> = {
  hn:     "HN",
  reddit: "Reddit",
  rss:    "RSS",
  manual: "manual",
};

export default function SignalsPage() {
  const sources = useSignalSources();
  const pending = useSignals({ status: "pending", limit: 30 });
  const suppressed = useSignals({ status: "suppressed", limit: 20 });
  const recent = useSignals({ status: "processed", limit: 20 });
  const poll = usePollSignalsNow();
  const suppress = useSuppressSignal();
  const manual = useManualSignal();

  const [manualUrl, setManualUrl] = useState("");
  const [manualIcp, setManualIcp] = useState("");

  return (
    <div className="flex flex-col">
      <PageHeader
        eyebrow="Inbound autonomy"
        title="Signals"
        description="Public ICP-relevant threads (HN / Reddit / RSS) the watcher polls every 30 min. Above-threshold signals auto-draft into the approval queue."
      >
        <Button
          variant="outline" size="sm"
          onClick={() => poll.mutate()}
          disabled={poll.isPending}
        >
          <RefreshCw className={poll.isPending ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
          Poll all sources now
        </Button>
      </PageHeader>

      <div className="p-5 sm:p-8 space-y-6 max-w-7xl">
        {/* Source health */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Radar className="h-4 w-4 text-primary" /> Source health
            </CardTitle>
            <CardDescription>
              Which inbound sources are on, and how much each produced in the
              last 24 hours.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {sources.isLoading && <Skeleton className="h-24" />}
            {!sources.isLoading && (sources.data ?? []).length === 0 && (
              <Empty
                icon={Radar}
                title="No sources configured."
                description="Run the schema bootstrap to seed three sample sources (hn-revops-handoff, reddit-saas-marketing, rss-google-ai-blog)."
              />
            )}
            {!sources.isLoading && (sources.data ?? []).length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-[13px]">
                  <thead>
                    <tr className="text-[11px] uppercase tracking-wider text-muted-foreground">
                      <th className="text-left py-2 pr-3">Name</th>
                      <th className="text-left py-2 pr-3">Source</th>
                      <th className="text-left py-2 pr-3">ICP</th>
                      <th className="text-left py-2 pr-3">Channel</th>
                      <th className="text-right py-2 pr-3">Score floor</th>
                      <th className="text-right py-2 pr-3">Signals 24h</th>
                      <th className="text-right py-2 pr-3">Drafts 24h</th>
                      <th className="text-left py-2">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(sources.data ?? []).map((s) => (
                      <tr key={s.name} className="border-t">
                        <td className="py-2 pr-3 font-mono">{s.name}</td>
                        <td className="py-2 pr-3">
                          <Badge variant="muted">{SOURCE_LABEL[s.source] ?? s.source}</Badge>
                        </td>
                        <td className="py-2 pr-3 text-muted-foreground">
                          {s.icp_segment ?? "—"}
                        </td>
                        <td className="py-2 pr-3 text-muted-foreground">
                          {s.default_channel ?? "—"}
                        </td>
                        <td className="py-2 pr-3 text-right tabular-nums">
                          {s.score_floor ?? "—"}
                        </td>
                        <td className="py-2 pr-3 text-right tabular-nums">{s.signals_24h}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">{s.drafts_24h}</td>
                        <td className="py-2">
                          {s.enabled ? (
                            <span className="inline-flex items-center gap-1 text-success text-[12px]">
                              <Power className="h-3 w-3" /> enabled
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 text-muted-foreground text-[12px]">
                              <PowerOff className="h-3 w-3" /> disabled
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Manual signal */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Plus className="h-4 w-4 text-primary" /> Add a manual signal
            </CardTitle>
            <CardDescription>
              Spotted a thread the watcher missed? Paste the URL — the router
              picks it up on the next 5-min tick.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form
              className="flex flex-wrap gap-2 items-end"
              onSubmit={(e) => {
                e.preventDefault();
                if (!manualUrl) return;
                manual.mutate({ url: manualUrl, icp_segment: manualIcp || undefined });
                setManualUrl("");
              }}
            >
              <div className="flex-1 min-w-[280px]">
                <label className="text-[11px] uppercase tracking-wider text-muted-foreground block mb-1">
                  URL
                </label>
                <input
                  type="url"
                  required
                  placeholder="https://news.ycombinator.com/item?id=..."
                  value={manualUrl}
                  onChange={(e) => setManualUrl(e.target.value)}
                  className="w-full px-3 py-2 text-sm border rounded-md bg-card"
                />
              </div>
              <div className="w-48">
                <label className="text-[11px] uppercase tracking-wider text-muted-foreground block mb-1">
                  ICP (optional)
                </label>
                <input
                  type="text"
                  placeholder="seg_revops_director"
                  value={manualIcp}
                  onChange={(e) => setManualIcp(e.target.value)}
                  className="w-full px-3 py-2 text-sm border rounded-md bg-card"
                />
              </div>
              <Button type="submit" size="sm" disabled={manual.isPending || !manualUrl}>
                <Plus className="h-4 w-4" /> Add
              </Button>
            </form>
            {manual.data?.status === "exists" && (
              <p className="text-[12px] text-muted-foreground mt-2">
                Already exists — signal_id {manual.data.signal_id}.
              </p>
            )}
            {manual.data?.status === "inserted" && (
              <p className="text-[12px] text-success mt-2">
                Inserted — signal_id {manual.data.signal_id}. Will be picked up on the next router tick.
              </p>
            )}
          </CardContent>
        </Card>

        {/* Pending signals */}
        <SignalListCard
          title="Pending"
          description="Score above floor; router will pick these up on the next 5-min tick."
          rows={pending.data ?? []}
          loading={pending.isLoading}
          onSuppress={(id) => suppress.mutate(id)}
          emptyHint="Nothing pending right now. Either nothing matches, or everything's already been routed."
        />

        {/* Recent processed */}
        <SignalListCard
          title="Recent triggers"
          description="Signals that automatically turned into drafts — find the result in your Approval Queue."
          rows={recent.data ?? []}
          loading={recent.isLoading}
          showTelemetryId
          emptyHint="No auto-drafts have fired yet."
        />

        {/* Suppressed */}
        <SignalListCard
          title="Suppressed"
          description="Dropped by score floor, duplicate URL, founder-suppressed, or 24h ICP quota."
          rows={suppressed.data ?? []}
          loading={suppressed.isLoading}
          showSuppressedReason
          emptyHint="Nothing suppressed in the last 24h."
        />
      </div>
    </div>
  );
}

function SignalListCard({
  title, description, rows, loading,
  onSuppress, showTelemetryId, showSuppressedReason, emptyHint,
}: {
  title: string;
  description: string;
  rows: Signal[];
  loading: boolean;
  onSuppress?: (id: string) => void;
  showTelemetryId?: boolean;
  showSuppressedReason?: boolean;
  emptyHint: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        {loading && <Skeleton className="h-24" />}
        {!loading && rows.length === 0 && (
          <p className="text-[13px] text-muted-foreground">{emptyHint}</p>
        )}
        {!loading && rows.length > 0 && (
          <div className="divide-y border rounded-md">
            {rows.map((s) => (
              <div key={s.id} className="p-3 text-[13px] flex items-start gap-3">
                <div className="flex flex-col items-center gap-1 shrink-0 w-16">
                  <Badge variant="muted">{SOURCE_LABEL[s.source ?? ""] ?? s.source}</Badge>
                  {typeof s.score === "number" && (
                    <span className="text-[11px] font-mono">{s.score.toFixed(2)}</span>
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-muted-foreground italic">"{s.evidence_excerpt}"</p>
                  <div className="flex flex-wrap gap-2 items-center mt-1 text-[11px] text-muted-foreground">
                    {s.icp_segment && <span>{s.icp_segment}</span>}
                    {s.icp_keywords_hit?.length > 0 && (
                      <span>· keywords: {s.icp_keywords_hit.join(", ")}</span>
                    )}
                    {showSuppressedReason && s.suppressed_reason && (
                      <span className="text-warning">· {s.suppressed_reason}</span>
                    )}
                    {showTelemetryId && s.triggered_telemetry_id && (
                      <Link to="/queue" className="text-primary hover:underline">· drafted → see in queue</Link>
                    )}
                  </div>
                </div>
                <div className="flex items-start gap-1 shrink-0">
                  {s.evidence_url && (
                    <a
                      href={s.evidence_url}
                      target="_blank"
                      rel="noreferrer"
                      title="Open source thread"
                      aria-label="Open source thread in a new tab"
                      className="text-muted-foreground hover:text-foreground p-1"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  )}
                  {onSuppress && (
                    <button
                      onClick={() => onSuppress(s.id)}
                      title="Suppress this signal"
                      aria-label="Suppress this signal"
                      className="text-muted-foreground hover:text-destructive p-1"
                    >
                      <Ban className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
