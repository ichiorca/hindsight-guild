import { useState } from "react";
import { Play, Loader2, AlertTriangle, Clock, ShieldAlert, ShieldCheck, Server, Webhook } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { useCrons, useRunCron, getAdminToken, setAdminToken, type Cron } from "@/lib/api";
import { cn } from "@/lib/utils";

// Admin · cron control. Lists every scheduled job/endpoint and lets the founder
// trigger any of them on demand (mirrors what Cloud Scheduler does nightly).
export default function AdminPage() {
  const { data, isLoading, isError, refetch } = useCrons();
  const runCron = useRunCron();
  const [token, setTokenState] = useState(getAdminToken());

  const saveToken = () => {
    setAdminToken(token.trim());
    refetch();
  };

  // Jobs first, then in-process endpoints; danger ones sink within their group.
  const crons = [...(data?.crons ?? [])].sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "job" ? -1 : 1;
    return Number(a.danger) - Number(b.danger);
  });

  return (
    <div className="flex flex-col">
      <PageHeader
        eyebrow="Admin"
        title="Cron control"
        description="Trigger any scheduled job or endpoint on demand — the same work Cloud Scheduler runs automatically. Useful for seeding pages, forcing a re-grade, or testing a pipeline without waiting for the cron."
      />

      <div className="p-5 sm:p-8 max-w-5xl space-y-5">
        {/* Security banner + token field */}
        {data && (
          <div className={cn(
            "rounded-lg border p-3 text-sm flex items-start gap-3",
            data.secured ? "border-success/30 bg-success/5" : "border-warning/30 bg-warning/5",
          )}>
            {data.secured
              ? <ShieldCheck className="h-4 w-4 text-success mt-0.5 shrink-0" />
              : <ShieldAlert className="h-4 w-4 text-warning mt-0.5 shrink-0" />}
            <div className="flex-1">
              <p className="font-medium">
                {data.secured ? "Token-protected" : "Unprotected (open)"}
              </p>
              <p className="text-muted-foreground text-[13px] leading-snug">
                {data.secured
                  ? "This panel requires the admin token. Paste it below to enable triggers."
                  : "ADMIN_SEED_TOKEN is not set, so these endpoints are reachable by anyone. Set the secret to lock this panel down (no code change needed)."}
              </p>
              <div className="flex items-center gap-2 mt-2">
                <Input
                  type="password"
                  value={token}
                  onChange={(e) => setTokenState(e.target.value)}
                  placeholder="X-Admin-Token (optional until secured)"
                  className="max-w-xs h-8 text-[13px]"
                />
                <Button size="sm" variant="outline" onClick={saveToken}>Save token</Button>
              </div>
            </div>
          </div>
        )}

        {isLoading && (
          <div className="space-y-2">
            <Skeleton className="h-16" /><Skeleton className="h-16" /><Skeleton className="h-16" />
          </div>
        )}
        {isError && <ErrorState what="the cron list" onRetry={() => refetch()} />}

        {data && (
          <Card>
            <CardHeader>
              <CardTitle>Scheduled work ({crons.length})</CardTitle>
              <CardDescription>Schedules are UTC. {data.region}.</CardDescription>
            </CardHeader>
            <CardContent className="p-0">
              <ul className="divide-y divide-border">
                {crons.map((c) => (
                  <CronRow
                    key={c.name}
                    cron={c}
                    running={runCron.isPending && runCron.variables === c.name}
                    onRun={() => {
                      if (c.danger && !window.confirm(
                        `"${c.name}" can cost money or make irreversible changes.\n\n${c.description}\n\nTrigger it now?`
                      )) return;
                      runCron.mutate(c.name);
                    }}
                  />
                ))}
              </ul>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}

function CronRow({ cron, running, onRun }: { cron: Cron; running: boolean; onRun: () => void }) {
  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <span className="shrink-0 text-muted-foreground" title={cron.kind === "job" ? "Cloud Run job" : "in-process endpoint"}>
        {cron.kind === "job" ? <Server className="h-4 w-4" /> : <Webhook className="h-4 w-4" />}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium text-sm">{cron.name}</span>
          {cron.danger && (
            <Badge variant="destructive" className="text-[10px] gap-1">
              <AlertTriangle className="h-3 w-3" /> costly / irreversible
            </Badge>
          )}
          <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground font-mono">
            <Clock className="h-3 w-3" /> {cron.schedule}
          </span>
        </div>
        <p className="text-[12px] text-muted-foreground leading-snug mt-0.5">{cron.description}</p>
      </div>
      <Button
        size="sm"
        variant={cron.danger ? "outline" : "success"}
        onClick={onRun}
        disabled={running}
        className="shrink-0"
      >
        {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
        {running ? "Running…" : "Run now"}
      </Button>
    </li>
  );
}
