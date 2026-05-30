import { FlaskConical, PlayCircle, CheckCircle2, TrendingDown } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Empty } from "@/components/ui/Empty";
import { Skeleton } from "@/components/ui/Skeleton";
import { useRunningExperiments, useDecidedExperiments, useDriftInvestigations } from "@/lib/api";
import { formatDate, pct } from "@/lib/utils";
import type { Experiment } from "@/lib/types";

export default function ExperimentsPage() {
  const running = useRunningExperiments();
  const decided = useDecidedExperiments(20);
  const drift = useDriftInvestigations();

  return (
    <div className="flex flex-col">
      <PageHeader
        eyebrow="Hypotheses in flight"
        title="Experiments"
        description="Every experiment carries a falsifiable hypothesis and a pre-committed decision rule. No theatre."
      />

      <div className="p-5 sm:p-8 space-y-6 max-w-6xl">
        {/* How this page works — guidance, not a list */}
        <Card className="bg-subtle/30 border-dashed">
          <CardContent className="p-5">
            <p className="text-sm font-medium mb-2">How experiments enter this list</p>
            <ol className="text-sm text-muted-foreground space-y-1.5 list-decimal list-inside">
              <li>
                <strong>The CMO agent</strong> proposes 3–5 new experiments each
                Monday, based on what got edited the prior week and where
                brand voice is slipping.
              </li>
              <li>
                <strong>Drift detection</strong> auto-opens an investigation
                whenever a quality score drops noticeably for several days in
                a row on a channel — they appear under <em>Drift investigations</em>.
              </li>
              <li>
                <strong>Outcome attribution</strong> tracks results for each
                variant. Once an experiment has enough data to call a winner,
                it moves to <em>Recently decided</em>.
              </li>
              <li>
                A decided experiment can roll out its winning variant —
                approve it from the Weekly Review and every agent picks up
                the new playbook on the next draft.
              </li>
            </ol>
            <p className="text-[12px] text-muted-foreground mt-3">
              Experiments are authored by the CMO agent today.
              Founder-authored experiments are on the roadmap.
            </p>
          </CardContent>
        </Card>

        <Section
          title="Running"
          icon={PlayCircle}
          accent="text-primary"
          loading={running.isLoading}
          error={running.isError}
          items={running.data ?? []}
          empty="No running experiments. New ones are proposed each Monday — start one from Drafting, or wait for the weekly run."
          render={(e) => <ExperimentRow exp={e} state="running" />}
        />

        <Section
          title="Drift investigations"
          icon={TrendingDown}
          accent="text-warning"
          loading={drift.isLoading}
          error={drift.isError}
          items={drift.data ?? []}
          empty="No active drift signals. Quality is holding steady."
          render={(e) => <ExperimentRow exp={e} state="drift" />}
        />

        <Section
          title="Recently decided"
          icon={CheckCircle2}
          accent="text-success"
          loading={decided.isLoading}
          error={decided.isError}
          items={decided.data ?? []}
          empty="No decisions yet. Experiments move here once enough results are in to call a winner."
          render={(e) => <ExperimentRow exp={e} state="decided" />}
        />
      </div>
    </div>
  );
}

function Section({
  title, icon: Icon, accent, loading, error, items, empty, render,
}: {
  title: string;
  icon: typeof FlaskConical;
  accent: string;
  loading: boolean;
  error?: boolean;
  items: Experiment[];
  empty: string;
  render: (e: Experiment) => React.ReactNode;
}) {
  return (
    <section>
      <div className="flex items-center gap-2 mb-3">
        <Icon className={`h-5 w-5 ${accent}`} />
        <h2 className="font-serif text-lg font-semibold">{title}</h2>
        <Badge variant="muted" className="ml-1 font-mono">{items.length}</Badge>
      </div>
      <Card>
        <CardContent className="p-4 space-y-3">
          {loading && <><Skeleton className="h-16" /><Skeleton className="h-16" /></>}
          {/* Distinguish a load FAILURE from a genuinely empty list — a
              reassuring "all clear" on a failed fetch is dishonest, doubly
              so for a safety signal like drift. */}
          {!loading && error && (
            <p role="alert" className="text-sm text-destructive py-3 text-center">
              Couldn't load this list — connection problem, not an empty result.
            </p>
          )}
          {!loading && !error && items.length === 0 && <Empty title={empty} />}
          {!error && items.map(render)}
        </CardContent>
      </Card>
    </section>
  );
}

function ExperimentRow({ exp, state }: { exp: Experiment; state: "running" | "decided" | "drift" }) {
  return (
    <div className="rounded-lg p-4 hover:bg-subtle/60 transition-colors">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="min-w-0">
          <h4 className="font-medium text-sm leading-tight">{exp.title}</h4>
          <p className="text-xs text-muted-foreground italic mt-1.5">"{exp.hypothesis}"</p>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          {exp.channel && <Badge variant="muted">{exp.channel}</Badge>}
          {state === "decided" && exp.result?.lift != null && (
            <Badge variant="success">+{pct(exp.result.lift, 1)}</Badge>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground mt-3 pt-3 border-t">
        <span>Goal: <span className="text-foreground">{exp.success_metric.replace(/_/g, " ")}</span></span>
        {exp.mde != null && <span>Sensitivity: <span className="text-foreground">{pct(exp.mde, 0)}</span></span>}
        {exp.variants?.length > 0 && (
          <span>Variants: {exp.variants.map((v) => `${v.id} (${v.allocation_pct}%)`).join(" · ")}</span>
        )}
        {exp.decided_at && <span>Decided {formatDate(exp.decided_at)}</span>}
      </div>

      {state === "decided" && exp.lesson && (
        <div className="mt-3 pt-3 border-t bg-success/5 -mx-4 -mb-4 px-4 py-3 rounded-b-lg">
          <p className="text-[10px] text-success font-semibold uppercase tracking-wider mb-1">Lesson</p>
          <p className="text-sm font-serif">{exp.lesson}</p>
        </div>
      )}
    </div>
  );
}
