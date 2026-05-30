import { useMemo } from "react";
import { Activity, Clock, Shield, Cpu, Calendar, AlertCircle } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Empty } from "@/components/ui/Empty";
import { ErrorState } from "@/components/ui/ErrorState";
import { AgentBadge } from "@/components/AgentBadge";
import { useLive } from "@/lib/api";
import { timeAgo, cn, channelLabel, scoreColor } from "@/lib/utils";
import { AGENTS, type AgentId } from "@/lib/agents";
import { humanizeActionType, humanizeSkillName, humanizeScheduledJob } from "@/lib/humanize";

const STALE_THRESHOLD_MS = 30 * 60 * 1000; // 30m

export default function LivePage() {
  const { data, isLoading, isError, refetch } = useLive();

  const heartbeatMap = useMemo(() => {
    const m: Record<string, { last_seen: string; n_24h: number }> = {};
    for (const h of data?.heartbeats ?? []) m[h.agent] = h;
    return m;
  }, [data]);

  const totalPending = data?.outcome_status?.pending ?? 0;
  const totalFilled = data?.outcome_status?.filled ?? 0;
  const totalExpired = data?.outcome_status?.expired ?? 0;

  return (
    <div className="flex flex-col">
      <PageHeader
        eyebrow="What's running right now"
        title="Live Ops"
        description="Realtime activity across all five agents and the worker jobs. Auto-refreshes every 8 seconds."
      >
        {data && (
          <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-success" />
            </span>
            Live · {timeAgo(data.server_time)}
          </span>
        )}
      </PageHeader>

      <div className="p-5 sm:p-8 space-y-6 max-w-6xl">
        {!isLoading && isError && !data && (
          <ErrorState what="the live activity feed" onRetry={() => refetch()} />
        )}
        {/* Heartbeats — agents */}
        <section>
          <div className="flex items-center gap-2 mb-3">
            <Cpu className="h-4 w-4 text-primary" />
            <h2 className="font-serif text-lg font-semibold">Agent heartbeats</h2>
            <span className="text-xs text-muted-foreground ml-1">all {Object.keys(AGENTS).length} agents · last 24h</span>
          </div>
          {isLoading ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
              {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => <Skeleton key={i} className="h-28 rounded-xl" />)}
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
              {(Object.keys(AGENTS) as AgentId[]).map((id) => {
                const hb = heartbeatMap[id];
                const lastMs = hb ? Date.now() - new Date(hb.last_seen).getTime() : Infinity;
                const isStale = lastMs > STALE_THRESHOLD_MS;
                const isIdle = !hb;
                return (
                  <Card key={id} className="hover:shadow-md transition-shadow">
                    <CardContent className="p-4">
                      <div className="flex items-start justify-between gap-2 mb-3">
                        <AgentBadge id={id} size="md" showName showRole />
                        <span
                          className={cn(
                            "h-2 w-2 rounded-full mt-2 shrink-0",
                            isIdle ? "bg-muted-foreground/40"
                              : isStale ? "bg-warning"
                              : "bg-success animate-pulse-soft",
                          )}
                        />
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {isIdle ? "No activity in 24h"
                          : isStale ? `Quiet · last ${timeAgo(hb.last_seen)}`
                          : `Active · last ${timeAgo(hb.last_seen)}`}
                      </p>
                      <p className="text-2xl font-serif font-semibold tabular-nums mt-1">
                        {hb?.n_24h ?? 0}
                        <span className="text-xs text-muted-foreground font-sans font-normal ml-1">actions</span>
                      </p>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          )}
        </section>

        {/* Worker / outcome state */}
        <section className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Calendar className="h-4 w-4 text-primary" /> Scheduled jobs
              </CardTitle>
              <CardDescription>Cron-driven workers behind the scenes.</CardDescription>
            </CardHeader>
            <CardContent>
              {isLoading ? (
                <Skeleton className="h-32" />
              ) : (
                <ul className="divide-y -my-2">
                  {data?.scheduled_jobs.map((job) => (
                    <li key={job.name} className="flex items-center justify-between py-3">
                      <div className="min-w-0">
                        <p className="text-sm font-medium">{humanizeScheduledJob(job.name)}</p>
                        <p className="text-xs text-muted-foreground">{job.category}</p>
                      </div>
                      <Badge variant="muted" className="shrink-0">{job.schedule}</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Shield className="h-4 w-4 text-primary" /> Performance attribution
              </CardTitle>
              <CardDescription>How drafts performed once they shipped (last 24h).</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-2.5">
                <Stat label="Filled"  value={totalFilled} tone="success" />
                <Stat label="Pending" value={totalPending} tone="warning" />
                <Stat label="Expired" value={totalExpired} tone="destructive" />
              </div>
            </CardContent>
          </Card>
        </section>

        {/* Activity stream */}
        <section>
          <div className="flex items-center gap-2 mb-3">
            <Activity className="h-4 w-4 text-primary" />
            <h2 className="font-serif text-lg font-semibold">Recent activity</h2>
            <span className="text-xs text-muted-foreground ml-1">last 24h</span>
          </div>
          {isLoading ? (
            <Skeleton className="h-96 rounded-xl" />
          ) : !data?.recent_actions?.length ? (
            <Empty icon={Activity} title="No activity in the last 24 hours." description="Seed the demo data or trigger a draft from the Drafting workspace." />
          ) : (
            <Card>
              <CardContent className="p-0 divide-y">
                {data.recent_actions.map((a) => {
                  const armorBlocked = a.model_armor?.decision === "block";
                  return (
                    <div key={a.telemetry_id} className="flex items-start gap-4 px-5 py-3 hover:bg-subtle/40 transition-colors">
                      <span className="text-[11px] text-muted-foreground w-16 shrink-0 mt-1">
                        {timeAgo(a.ts)}
                      </span>
                      <AgentBadge id={a.agent} size="sm" showName={false} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-medium">{humanizeActionType(a.action_type)}</span>
                          {a.channel && <Badge variant="muted">{channelLabel(a.channel)}</Badge>}
                          {a.skill_id && <span className="text-xs text-muted-foreground">{humanizeSkillName(a.skill_id)}</span>}
                          {armorBlocked && (
                            <Badge variant="destructive" className="inline-flex items-center gap-1">
                              <AlertCircle className="h-3 w-3" /> Safety filter blocked
                            </Badge>
                          )}
                        </div>
                        {a.eval_scores && Object.keys(a.eval_scores).length > 0 && (
                          <div className="mt-1 flex items-center gap-3 text-[11px] text-muted-foreground">
                            {Object.entries(a.eval_scores).slice(0, 3).map(([k, v]) => (
                              <span key={k}>
                                {k.replace(/_/g, " ")}:{" "}
                                <span className={cn("font-medium", scoreColor(v))}>{(v * 100).toFixed(0)}%</span>
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                      <Clock className="h-3.5 w-3.5 text-muted-foreground/50 mt-1.5" />
                    </div>
                  );
                })}
              </CardContent>
            </Card>
          )}
        </section>
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone: "success" | "warning" | "destructive" }) {
  const toneClass = {
    success: "text-success",
    warning: "text-warning",
    destructive: "text-destructive",
  }[tone];
  const dotClass = {
    success: "bg-success",
    warning: "bg-warning",
    destructive: "bg-destructive",
  }[tone];
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        <span className={cn("h-1.5 w-1.5 rounded-full", dotClass)} />
        <span className="text-sm text-muted-foreground">{label}</span>
      </div>
      <span className={cn("font-serif font-semibold tabular-nums text-xl", toneClass)}>
        {value}
      </span>
    </div>
  );
}
