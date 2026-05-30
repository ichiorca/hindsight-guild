import { useMemo, useState } from "react";
import {
  BookOpen, TrendingUp, TrendingDown, Coins, Layers as LayersIcon,
  AlertCircle, Search, Hash,
} from "lucide-react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Legend,
} from "recharts";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Input, Select } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { Empty } from "@/components/ui/Empty";
import { ErrorState } from "@/components/ui/ErrorState";
import { AgentBadge } from "@/components/AgentBadge";
import { useCapabilities } from "@/lib/api";
import { cn, timeAgo } from "@/lib/utils";
import { AGENTS, type AgentId } from "@/lib/agents";
import { humanizeSkillName } from "@/lib/humanize";
import type { SkillUsage, SkillCatalogEntry } from "@/lib/types";

const TIER_2_COLOR = "#0090db"; // ocean-500
const TIER_3_COLOR = "#00b8b8"; // teal-cyan

export default function CapabilitiesPage() {
  const [days, setDays] = useState(7);
  const [search, setSearch] = useState("");
  const { data, isLoading, isError, refetch } = useCapabilities(days);

  if (isLoading) {
    return (
      <div className="p-5 sm:p-8 space-y-4">
        <Skeleton className="h-24" />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="p-5 sm:p-8 max-w-3xl">
        <ErrorState what="agent capabilities" onRetry={() => refetch()} />
      </div>
    );
  }

  // Merge catalog × usage so the table shows every installed skill (including
  // the dead-weight ones with zero loads).
  const usageByName: Record<string, SkillUsage> = Object.fromEntries(
    data.by_skill.map((u) => [u.skill_name, u]),
  );
  const merged = data.catalog
    .map((c) => ({
      catalog: c,
      usage: usageByName[c.name],
    }))
    .filter((row) => {
      if (!search) return true;
      const q = search.toLowerCase();
      return (
        row.catalog.name.toLowerCase().includes(q) ||
        row.catalog.description.toLowerCase().includes(q)
      );
    })
    .sort((a, b) => (b.usage?.loads ?? 0) - (a.usage?.loads ?? 0));

  return (
    <div className="flex flex-col">
      <PageHeader
        eyebrow="The team's knowledge library"
        title="Capabilities"
        description={`${data.summary.installed_skills} playbooks installed. ${data.summary.unique_skills_used} used in the last ${days} days. ${data.summary.dead_weight_count} sitting idle.`}
      >
        <Select value={String(days)} onChange={(e) => setDays(Number(e.target.value))} className="w-32">
          <option value="7">Last 7 days</option>
          <option value="14">Last 14 days</option>
          <option value="28">Last 28 days</option>
        </Select>
      </PageHeader>

      <div className="p-5 sm:p-8 space-y-6 max-w-7xl">
        {/* ----- Scorecards ----- */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Scorecard
            icon={LayersIcon}
            label="Installed"
            value={data.summary.installed_skills}
            sub={`${data.catalog.reduce((s, c) => s + c.reference_count, 0)} references on disk`}
          />
          <Scorecard
            icon={BookOpen}
            label="Loads"
            value={data.summary.loads_period}
            sub={
              data.summary.loads_delta_pct == null
                ? "no prior period to compare"
                : `${data.summary.loads_delta_pct >= 0 ? "+" : ""}${data.summary.loads_delta_pct.toFixed(0)}% vs prior period`
            }
            trend={data.summary.loads_delta_pct ?? undefined}
          />
          <Scorecard
            icon={Coins}
            label="Context tokens"
            value={data.summary.tokens_estimated.toLocaleString()}
            sub="Estimated cost of loaded playbooks"
          />
          <Scorecard
            icon={AlertCircle}
            label="Dead weight"
            value={data.summary.dead_weight_count}
            sub={
              data.summary.dead_weight_count === 0
                ? "every skill used at least once"
                : "candidates to prune"
            }
            tone={data.summary.dead_weight_count > 0 ? "warning" : "muted"}
          />
        </div>

        {/* ----- Timeseries + Recent feed side-by-side on lg ----- */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Playbook usage over time</CardTitle>
              <CardDescription>
                Full-playbook loads vs. just looking up a specific reference within a playbook.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {data.timeseries.length === 0 ? (
                <Empty title="No loads yet." description="Run a draft from the Drafting workspace to populate this." />
              ) : (
                <ResponsiveContainer width="100%" height={240}>
                  <LineChart data={data.timeseries}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                    <XAxis dataKey="day" tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} />
                    <YAxis tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} allowDecimals={false} />
                    <Tooltip contentStyle={{ borderRadius: 8, fontSize: 12, border: "1px solid hsl(var(--border))" }} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Line type="monotone" dataKey="tier_2" name="Full playbook" stroke={TIER_2_COLOR} strokeWidth={2} dot={false} />
                    <Line type="monotone" dataKey="tier_3" name="Reference lookup" stroke={TIER_3_COLOR} strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Recent loads</CardTitle>
              <CardDescription>Most recent 50 skill loads, newest first.</CardDescription>
            </CardHeader>
            <CardContent className="p-0">
              {data.recent.length === 0 ? (
                <div className="p-6"><Empty title="Quiet so far." /></div>
              ) : (
                <div className="max-h-[260px] overflow-y-auto divide-y">
                  {data.recent.map((r, i) => (
                    <div key={i} className="px-4 py-2.5 flex items-start gap-3 text-[12px]">
                      <span className="font-mono text-muted-foreground w-12 shrink-0">
                        {timeAgo(r.ts)}
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          {r.agent_name && (
                            <AgentBadge id={r.agent_name as AgentId} size="sm" showName={false} />
                          )}
                          <span className="font-medium truncate">{r.skill_name.replace(/[-_]/g, " ")}</span>
                          <Badge variant="muted" className="text-[10px]">
                            {r.tier === 2 ? "full playbook" : r.tier === 3 ? "reference" : `tier ${r.tier}`}
                          </Badge>
                        </div>
                        {r.reference_path && (
                          <p className="text-[11px] text-muted-foreground truncate mt-0.5">
                            {r.reference_path.split(/[/\\]/).pop()?.replace(/\.md$/, "")}
                          </p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* ----- Skills × usage table ----- */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between flex-wrap gap-2">
              <div>
                <CardTitle>Playbook catalog</CardTitle>
                <CardDescription>
                  Every playbook the team can use, ranked by how often it's been pulled into work.
                </CardDescription>
              </div>
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Filter by name or description…"
                  className="pl-9 w-72"
                />
              </div>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-xs text-muted-foreground">
                    <th className="text-left py-3 px-5 font-medium">Skill</th>
                    <th className="text-right py-3 px-3 font-medium tabular-nums">Loads</th>
                    <th className="text-right py-3 px-3 font-medium tabular-nums">Δ prev</th>
                    <th className="text-right py-3 px-3 font-medium tabular-nums">Tokens</th>
                    <th className="text-left py-3 px-3 font-medium">Top callers</th>
                    <th className="text-left py-3 px-3 font-medium hidden md:table-cell">Allowed for</th>
                    <th className="text-right py-3 px-5 font-medium">Last used</th>
                  </tr>
                </thead>
                <tbody>
                  {merged.map(({ catalog, usage }) => (
                    <SkillRow
                      key={catalog.name}
                      catalog={catalog}
                      usage={usage}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        {/* ----- Agent × Skill heatmap ----- */}
        <Card>
          <CardHeader>
            <CardTitle>Agent × Skill heatmap</CardTitle>
            <CardDescription>
              Which agents reach for which skills. Empty cells mean the agent has the skill in its allowlist but didn't load it this period.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <AgentSkillHeatmap data={data} />
          </CardContent>
        </Card>

        {/* ----- Dead-weight callout ----- */}
        {data.summary.dead_weight.length > 0 && (
          <Card className="border-warning/40">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <AlertCircle className="h-4 w-4 text-warning" />
                Playbooks sitting idle
              </CardTitle>
              <CardDescription>
                Installed but didn't get used. After 30 days of low usage, consider retiring them — every playbook adds to the agent's prompt size.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-2">
                {data.summary.dead_weight.map((name) => (
                  <Badge key={name} variant="warning" className="font-mono">{name}</Badge>
                ))}
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}

function Scorecard({
  icon: Icon, label, value, sub, trend, tone = "default",
}: {
  icon: typeof Coins;
  label: string;
  value: number | string;
  sub?: string;
  trend?: number;
  tone?: "default" | "warning" | "muted";
}) {
  const toneClass = {
    default: "text-foreground",
    warning: "text-warning",
    muted: "text-muted-foreground",
  }[tone];
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-start justify-between mb-2">
          <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium">
            {label}
          </p>
          <Icon className={cn("h-3.5 w-3.5 text-muted-foreground")} />
        </div>
        <p className={cn("text-2xl font-semibold tabular-nums font-serif", toneClass)}>{value}</p>
        {sub && (
          <p className="text-[11px] text-muted-foreground mt-1 inline-flex items-center gap-1">
            {trend != null && trend > 0 && <TrendingUp className="h-3 w-3 text-success" />}
            {trend != null && trend < 0 && <TrendingDown className="h-3 w-3 text-destructive" />}
            {sub}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function SkillRow({ catalog, usage }: { catalog: SkillCatalogEntry; usage?: SkillUsage }) {
  const loads = usage?.loads ?? 0;
  const prev = usage?.loads_prev ?? 0;
  const delta = loads - prev;
  const isDead = loads === 0;

  return (
    <tr className={cn("border-b last:border-0 hover:bg-subtle/50 transition-colors", isDead && "opacity-60")}>
      <td className="py-3 px-5">
        <div className="flex flex-col">
          <span className="font-mono text-sm font-medium">{catalog.name}</span>
          <span className="text-[11px] text-muted-foreground line-clamp-1 max-w-md">
            {catalog.description.replace(/^"?When the user wants /, "When ").replace(/\.$/, "")}
          </span>
          <div className="flex items-center gap-2 mt-1">
            <Badge variant="muted" className="text-[10px] font-mono">v{catalog.version}</Badge>
            {catalog.reference_count > 0 && (
              <span className="text-[10px] text-muted-foreground inline-flex items-center gap-0.5">
                <Hash className="h-2.5 w-2.5" /> {catalog.reference_count} refs
              </span>
            )}
          </div>
        </div>
      </td>
      <td className="py-3 px-3 text-right tabular-nums font-mono">{loads}</td>
      <td className="py-3 px-3 text-right tabular-nums font-mono">
        {prev === 0 && loads === 0 ? (
          <span className="text-muted-foreground">—</span>
        ) : delta === 0 ? (
          <span className="text-muted-foreground">±0</span>
        ) : (
          <span className={delta > 0 ? "text-success" : "text-destructive"}>
            {delta > 0 ? "+" : ""}{delta}
          </span>
        )}
      </td>
      <td className="py-3 px-3 text-right tabular-nums font-mono text-muted-foreground">
        {usage ? usage.tokens_estimated.toLocaleString() : "—"}
      </td>
      <td className="py-3 px-3">
        {usage?.top_agents.length ? (
          <div className="flex items-center gap-1">
            {usage.top_agents.slice(0, 3).map((a) => (
              <AgentBadge key={a.agent_name} id={a.agent_name as AgentId} size="sm" showName={false} />
            ))}
          </div>
        ) : (
          <span className="text-[11px] text-muted-foreground">—</span>
        )}
      </td>
      <td className="py-3 px-3 hidden md:table-cell">
        <div className="flex items-center -space-x-1.5">
          {catalog.allowed_for.slice(0, 5).map((agent) => (
            <AgentBadge key={agent} id={agent as AgentId} size="sm" showName={false} />
          ))}
          {catalog.allowed_for.length > 5 && (
            <span className="inline-flex items-center justify-center h-6 w-6 rounded-full ring-2 ring-card bg-muted text-muted-foreground text-[10px] font-semibold">
              +{catalog.allowed_for.length - 5}
            </span>
          )}
        </div>
      </td>
      <td className="py-3 px-5 text-right text-[11px] text-muted-foreground">
        {usage ? timeAgo(usage.last_used_at) : "never"}
      </td>
    </tr>
  );
}

function AgentSkillHeatmap({ data }: { data: { catalog: SkillCatalogEntry[]; by_agent: { agent_name: string; skill_loads: { skill_name: string; count: number }[] }[] } }) {
  const skillNames = useMemo(() => data.catalog.map((c) => c.name), [data.catalog]);
  const agentNames = useMemo(
    () => Object.keys(AGENTS),
    [],
  );

  // Build a lookup: count[agent][skill]
  const counts = useMemo(() => {
    const m: Record<string, Record<string, number>> = {};
    for (const a of data.by_agent) {
      m[a.agent_name] = {};
      for (const sl of a.skill_loads) {
        m[a.agent_name][sl.skill_name] = sl.count;
      }
    }
    return m;
  }, [data.by_agent]);

  const maxCount = Math.max(
    1,
    ...data.by_agent.flatMap((a) => a.skill_loads.map((s) => s.count)),
  );

  // If the catalog is empty (no skill_usage rows recorded yet), the
  // matrix would render as an all-grey grid with no signal. Surface a
  // helpful "no data" panel instead — this is the most common state in
  // fresh local-dev environments before any drafts have run.
  const totalLoads = data.by_agent.reduce(
    (acc, a) => acc + a.skill_loads.reduce((s, sl) => s + sl.count, 0),
    0,
  );
  if (totalLoads === 0) {
    return (
      <div className="p-6 max-w-2xl">
        <div className="rounded-md border border-info/30 bg-info/5 p-4 text-sm">
          <p className="font-medium">No playbook usage yet this week.</p>
          <p className="text-muted-foreground mt-1">
            The grid fills in as your team uses playbooks while drafting. Run
            2-3 drafts from the <b>Drafting</b> page across different channels
            and come back — you'll see which agents reach for which playbook
            on every draft.
          </p>
        </div>
      </div>
    );
  }

  function intensity(count: number): string {
    if (count === 0) return "bg-muted/30";
    const r = Math.min(1, count / maxCount);
    if (r >= 0.8) return "bg-primary text-primary-foreground";
    if (r >= 0.5) return "bg-primary/70 text-primary-foreground";
    if (r >= 0.25) return "bg-primary/40";
    return "bg-primary/15";
  }

  return (
    <div className="overflow-x-auto">
      <table className="text-xs border-separate border-spacing-0">
        <caption className="sr-only">
          How often each agent used each playbook this period. Rows are agents,
          columns are playbooks; each cell shows the usage count.
        </caption>
        <thead>
          {/* Header height reserved so the rotated labels don't clip. */}
          <tr className="h-28 align-bottom">
            <th scope="col" className="sticky left-0 z-10 bg-card text-left p-2 font-medium text-muted-foreground border-b min-w-[180px] align-bottom">
              Agent
            </th>
            {skillNames.map((name) => (
              <th key={name} scope="col" className="p-1 font-normal text-muted-foreground border-b align-bottom"
                title={humanizeSkillName(name)}>
                <div className="text-[11px] -rotate-45 origin-bottom-left translate-y-2 whitespace-nowrap inline-block max-w-[8rem] truncate">
                  {humanizeSkillName(name)}
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {agentNames.map((agentId) => {
            const profile = AGENTS[agentId as AgentId];
            return (
              <tr key={agentId} className="hover:bg-subtle/40">
                <th scope="row" className="sticky left-0 bg-card px-2 py-1 border-b text-left font-normal">
                  <AgentBadge id={agentId as AgentId} size="sm" showName />
                </th>
                {skillNames.map((skill) => {
                  const c = counts[agentId]?.[skill] ?? 0;
                  const label = `${profile.name} × ${humanizeSkillName(skill)}: used ${c} time${c === 1 ? "" : "s"}`;
                  return (
                    <td
                      key={skill}
                      className={cn(
                        "border-b text-center font-mono text-[10px] tabular-nums",
                        "w-9 h-9 cursor-help transition-colors",
                        intensity(c),
                      )}
                      title={label}
                      aria-label={label}
                    >
                      {c > 0 ? c : ""}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="px-4 py-2 text-[11px] text-muted-foreground border-t">
        Cell intensity = how often this agent pulled in this playbook, relative to the busiest cell. Empty cells = the agent has access but hasn't reached for it this period.
      </p>
    </div>
  );
}
