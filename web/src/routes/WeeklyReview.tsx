import { useMemo } from "react";
import { Link } from "react-router-dom";
import {
  TrendingUp, Lightbulb, ArrowUpRight,
  AlertTriangle, CheckCircle2, X, CircleCheck, BookOpen,
} from "lucide-react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from "recharts";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Empty } from "@/components/ui/Empty";
import { ErrorState } from "@/components/ui/ErrorState";
import { AgentBadge } from "@/components/AgentBadge";
import { EvidenceDrawer } from "@/components/EvidenceDrawer";
import { MarkdownDiff } from "@/components/MarkdownDiff";
import {
  useWeeklyReview, useDecidePromotion,
  useUnifiedProposals, useApproveProposal, useDismissProposal,
} from "@/lib/api";
import type { UnifiedProposal } from "@/lib/api";
import { toast } from "@/lib/toast";
import { cn, pct, formatDate, channelLabel } from "@/lib/utils";
import { humanizeSkillName } from "@/lib/humanize";
import type { Skill } from "@/lib/types";

// "See more" affordance styled like a ghost button but rendered as a real
// link (no dead-end no-op buttons).
const MORE_LINK = "inline-flex items-center h-8 px-3 rounded-md text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground transition-colors";

const LINE_COLORS = ["#0090db", "#00b8b8", "#5fb8eb"]; // ocean, teal, sky — match agent palette

export default function WeeklyReviewPage() {
  const { data, isLoading, isError, refetch } = useWeeklyReview();
  const decidePromo = useDecidePromotion();
  const proposals = useUnifiedProposals();
  const approve = useApproveProposal();
  const dismiss = useDismissProposal();

  // Per-card pending: only the acting card's buttons should disable/spin —
  // not every card in the list (the old shared-flag behavior).
  const promoPendingFor = (id: string) =>
    decidePromo.isPending && (decidePromo.variables as { skill_id?: string } | undefined)?.skill_id === id;
  const proposalPendingFor = (id: string) =>
    (approve.isPending && approve.variables === id) ||
    (dismiss.isPending && dismiss.variables === id);

  const promote = (skill_id: string, decision: "approve" | "reject") =>
    decidePromo.mutate({ skill_id, decision }, {
      onSuccess: () => toast.success(
        decision === "approve" ? "Rolling out the new version" : "Kept the current version",
        decision === "approve"
          ? { description: "Every agent will use it on the next draft." }
          : undefined,
      ),
    });
  const acceptProposal = (id: string) =>
    approve.mutate(id, {
      onSuccess: () => toast.success("Accepted as a candidate", {
        description: "It'll be measured against the current version before any rollout.",
      }),
    });
  const dismissProposal = (id: string) =>
    dismiss.mutate(id, { onSuccess: () => toast.success("Dismissed") });

  const decisionCount = useMemo(() => {
    if (!data) return 0;
    // self-critique proposals are now counted from the unified endpoint
    // (which lists EVERY proposal — skills can have more than one — plus
    // paid + signal-source destinations), not the one-per-skill mirror.
    return data.promotion_requests.length
      + (proposals.data?.length ?? 0)
      + data.drift_investigations.length;
  }, [data, proposals.data]);

  if (isLoading) return <div className="p-8 space-y-4"><Skeleton className="h-48" /><Skeleton className="h-64" /></div>;
  if (isError || !data) {
    return (
      <div className="p-5 sm:p-8 max-w-3xl">
        <ErrorState what="this week's review" onRetry={() => refetch()} />
      </div>
    );
  }

  const trendByChannel = (data.rubric_trend ?? []).reduce((acc, p) => {
    if (!acc[p.channel]) acc[p.channel] = [];
    acc[p.channel].push(p);
    return acc;
  }, {} as Record<string, typeof data.rubric_trend>);

  return (
    <div className="flex flex-col">
      <PageHeader
        eyebrow="Monday ritual · 15 minutes"
        title="Weekly Review"
        description="What changed this week, what's working, what needs your call. Read top-to-bottom."
      >
        <div className="text-xs text-muted-foreground">
          Week of {formatDate(new Date())}
        </div>
      </PageHeader>

      <div className="px-5 sm:px-8 py-6 space-y-8 max-w-5xl">
        {/* ----- HERO: the CMO's narrative ----- */}
        <section>
          <div className="flex items-center gap-2 mb-4">
            <AgentBadge id="cmo_planner" size="md" showName showRole />
            <span className="ml-auto text-[11px] uppercase tracking-wider text-muted-foreground">
              Memo · drafted Monday morning
            </span>
          </div>
          <article className="prose-memo bg-card border rounded-xl px-8 py-7">
            <h2 className="!mt-0">This week at a glance</h2>
            <p className="!mb-5 text-muted-foreground italic">
              {data.summary.drafts} drafts produced.
              {" "}{data.summary.approvals} approved,
              {" "}{data.summary.edits} edited,
              {" "}{data.summary.armor_blocks} Model Armor blocks.
              {decisionCount > 0 && (
                <> <strong className="text-foreground not-italic">You have {decisionCount} decision{decisionCount === 1 ? "" : "s"} below.</strong></>
              )}
            </p>

            {data.decided_experiments.slice(0, 1).map((e) => (
              <p key={e._id}>
                <strong>What worked.</strong> {e.lesson || e.title}
                {e.result?.lift != null && (
                  <> The win was <span className="text-success font-medium not-italic">+{pct(e.result.lift, 1)} on {e.success_metric}</span>.</>
                )}
              </p>
            ))}

            {data.drift_investigations.slice(0, 1).map((e) => (
              <p key={e._id}>
                <strong>What to watch.</strong> {e.title}. The detector opened an investigation —
                {" "}<em>"{e.hypothesis.split(".")[0]}."</em> No autopilot rollback; we want your eyes on it.
              </p>
            ))}

            {data.promotion_requests.slice(0, 1).map((s) => {
              const pr = s.promotion_request!;
              if (pr.kind === "agent_skill") {
                const channelList = (pr.channels_at_risk ?? []).join(", ");
                return (
                  <p key={s._id}>
                    <strong>Ready to update a playbook.</strong>{" "}
                    The <b>{humanizeSkillName(s._id)}</b> playbook is dragging brand voice on{" "}
                    {channelList || "multiple channels"}
                    {typeof pr.project_baseline_brand_voice === "number" && (
                      <>
                        {" "}(baseline {pr.project_baseline_brand_voice.toFixed(3)})
                      </>
                    )}
                    . See the proposed change below.
                  </p>
                );
              }
              return (
                <p key={s._id}>
                  <strong>Ready to update a playbook.</strong> A new version of{" "}
                  <b>{humanizeSkillName(s._id)}</b> beat the current one by{" "}
                  <span className="text-success font-medium not-italic">{pct(pr.lift ?? 0, 1)}</span> on{" "}
                  {(pr.success_metric ?? "the success metric").replace(/_/g, " ")}. Approve below.
                </p>
              );
            })}

            {data.self_critique_proposals.slice(0, 1).map((s) => (
              <p key={s._id}>
                <strong>The system noticed something.</strong> {s.self_critique_proposal!.issue}{" "}
                Proposal awaits your accept/reject.
              </p>
            ))}
          </article>
        </section>

        {/* ----- DECISIONS section: triage, then dispatch ----- */}
        {decisionCount > 0 && (
          <section>
            <div className="flex items-center gap-2 mb-4">
              <span className="inline-flex items-center justify-center h-6 w-6 rounded-full bg-primary text-primary-foreground text-xs font-semibold">
                {decisionCount}
              </span>
              <h2 className="font-serif text-xl font-semibold">Decisions awaiting you</h2>
            </div>

            <div className="space-y-3">
              {data.promotion_requests.map((s) => {
                const pr = s.promotion_request!;
                return pr.kind === "agent_skill" ? (
                  <AgentSkillPromotionCard
                    key={s._id}
                    skill={s}
                    onApprove={() => promote(s._id, "approve")}
                    onReject={() => promote(s._id, "reject")}
                    pending={promoPendingFor(s._id)}
                  />
                ) : (
                  <Card key={s._id} className="card-decision">
                    <CardContent className="p-5">
                      <div className="flex items-start gap-4">
                        <div className="rounded-full bg-success/10 ring-1 ring-success/20 p-2 mt-1">
                          <ArrowUpRight className="h-4 w-4 text-success" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1">
                            <h3 className="font-medium text-sm">Update {humanizeSkillName(s._id)}</h3>
                            {typeof pr.lift === "number" && (
                              <Badge variant="success">+{pct(pr.lift, 1)}</Badge>
                            )}
                          </div>
                          <p className="text-sm text-muted-foreground">
                            {humanizeSkillName(pr.incumbent)} → {humanizeSkillName(pr.candidate)}
                            {" "}· {pr.candidate_n ?? "?"} drafts on the new version vs {pr.incumbent_n ?? "?"} on the current · measured on {(pr.success_metric ?? "the metric").replace(/_/g, " ")}
                          </p>
                          <div className="flex gap-2 mt-3 items-center flex-wrap">
                            <ConfirmButton variant="success" size="sm" confirmLabel="Confirm rollout"
                              disabled={promoPendingFor(s._id)}
                              onConfirm={() => promote(s._id, "approve")}>
                              <CheckCircle2 className="h-3.5 w-3.5" /> Roll out new version
                            </ConfirmButton>
                            <Button variant="outline" size="sm" onClick={() => promote(s._id, "reject")} disabled={promoPendingFor(s._id)}>
                              <X className="h-3.5 w-3.5" /> Keep current
                            </Button>
                            <Link to={`/skills?skill=${s._id}`} className={MORE_LINK}>See proof →</Link>
                          </div>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                );
              })}

              {/* Skill improvement proposals from the unified endpoint. A
                  skill can now carry MORE THAN ONE pending proposal (e.g. two
                  different voice patterns), so we render one card per proposal
                  — addressed by its unified id (skill:{id}::{subid}) — instead
                  of one card per skill. Accept mints a candidate version. */}
              {(proposals.data ?? [])
                .filter((p) => p.target_kind === "skill")
                .map((p) => (
                  <ProposalCard
                    key={p.id}
                    p={p}
                    onApprove={() => acceptProposal(p.id)}
                    onDismiss={() => dismissProposal(p.id)}
                    busy={proposalPendingFor(p.id)}
                  />
                ))}

              {/* PRD-03 — paid + signal-source proposals from the nightly miners. */}
              {(proposals.data ?? [])
                .filter((p) => p.target_kind !== "skill")
                .map((p) => (
                  <ProposalCard
                    key={p.id}
                    p={p}
                    onApprove={() => acceptProposal(p.id)}
                    onDismiss={() => dismissProposal(p.id)}
                    busy={proposalPendingFor(p.id)}
                  />
                ))}

              {data.drift_investigations.map((e) => (
                <Card key={e._id} className="card-decision">
                  <CardContent className="p-5">
                    <div className="flex items-start gap-4">
                      <div className="rounded-full bg-destructive/10 ring-1 ring-destructive/20 p-2 mt-1">
                        <AlertTriangle className="h-4 w-4 text-destructive" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <h3 className="font-medium text-sm">{e.title}</h3>
                          {e.channel && <Badge variant="warning">{e.channel}</Badge>}
                        </div>
                        <p className="text-sm text-muted-foreground italic">"{e.hypothesis}"</p>
                        <div className="flex gap-2 mt-3">
                          <Link to="/telemetry" className={MORE_LINK}>Investigate →</Link>
                        </div>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </section>
        )}

        {decisionCount === 0 && (
          <section className="flex items-center gap-3 px-4 py-3 rounded-lg bg-success/10 ring-1 ring-success/20">
            <CircleCheck className="h-5 w-5 text-success" />
            <div>
              <p className="text-sm font-medium">No decisions waiting.</p>
              <p className="text-xs text-muted-foreground">The team is running clean. Scan the trends below.</p>
            </div>
          </section>
        )}

        {/* ----- THINGS TO KNOW: lighter, scannable, no actions ----- */}
        <section>
          <h2 className="font-serif text-xl font-semibold mb-4">Things to know</h2>

          <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
            <Scorecard label="Drafts"        value={data.summary.drafts} />
            <Scorecard label="Approvals"     value={data.summary.approvals} tone="success" />
            <Scorecard label="Edits"         value={data.summary.edits} tone="warning" />
            <Scorecard label="Armor blocks"  value={data.summary.armor_blocks} tone="muted" />
            <Scorecard label="Total actions" value={data.summary.total_actions} tone="muted" />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mt-4">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>Brand voice trend</CardTitle>
                <CardDescription>28 days, per channel · drift &gt; 0.10 triggers investigation</CardDescription>
              </CardHeader>
              <CardContent>
                {Object.keys(trendByChannel).length === 0 ? (
                  <Empty title="No trend data yet" description="Run demo/seed_demo.py to populate." />
                ) : (
                  <ResponsiveContainer width="100%" height={240}>
                    <LineChart data={mergeByDay(data.rubric_trend)}>
                      <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                      <XAxis dataKey="day" tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} />
                      <YAxis domain={[0.5, 1]} tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} tickFormatter={(v) => pct(Number(v), 0)} />
                      <Tooltip contentStyle={{ borderRadius: 8, fontSize: 12, border: "1px solid hsl(var(--border))" }} formatter={(v: number | string) => pct(Number(v), 1)} />
                      <Legend wrapperStyle={{ fontSize: 12 }} />
                      {Object.keys(trendByChannel).map((ch, i) => (
                        <Line
                          key={ch}
                          type="monotone"
                          dataKey={`bv_${ch}`}
                          name={channelLabel(ch)}
                          stroke={LINE_COLORS[i % LINE_COLORS.length]}
                          strokeWidth={2}
                          dot={false}
                        />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Your edit patterns</CardTitle>
                <CardDescription>What you fix most is what the playbook needs to catch.</CardDescription>
              </CardHeader>
              <CardContent>
                {data.edit_categories.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No edits captured this week.</p>
                ) : (
                  <ul className="space-y-2">
                    {data.edit_categories.map((c) => (
                      <li key={c.category} className="flex items-center justify-between text-sm">
                        <span>{c.category.replace(/_/g, " ")}</span>
                        <Badge variant="muted" className="font-mono">{c.n}</Badge>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </div>
        </section>

        {/* Recent wins */}
        {data.decided_experiments.length > 0 && (
          <section>
            <h2 className="font-serif text-xl font-semibold mb-4">Recent decisions</h2>
            <ul className="space-y-3">
              {data.decided_experiments.map((e) => (
                <li
                  key={e._id}
                  className="flex items-start gap-3 border-l-2 border-success/40 bg-success/[0.03] rounded-r-md px-4 py-3"
                >
                  <TrendingUp className="h-4 w-4 mt-1 text-success shrink-0" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{e.title}</p>
                    {e.lesson && <p className="text-xs text-muted-foreground italic mt-1">"{e.lesson}"</p>}
                  </div>
                  {e.result?.lift != null && (
                    <Badge variant="success" className="shrink-0">+{pct(e.result.lift, 1)}</Badge>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}

function ProposalCard({ p, onApprove, onDismiss, busy }: {
  p: UnifiedProposal;
  onApprove: () => void;
  onDismiss: () => void;
  busy: boolean;
}) {
  // Choose icon + tone by kind/target.
  const isPaid = p.target_kind === "paid_action";
  const isSignal = p.target_kind === "signal_source";
  const isSkill = p.target_kind === "skill";
  return (
    <Card className="card-decision">
      <CardContent className="p-5">
        <div className="flex items-start gap-4">
          <div className={cn(
            "rounded-full p-2 mt-1 ring-1",
            isPaid && "bg-info/10 ring-info/30",
            (isSignal || isSkill) && "bg-warning/10 ring-warning/30",
          )}>
            <Lightbulb className={cn(
              "h-4 w-4",
              isPaid && "text-info",
              (isSignal || isSkill) && "text-warning",
            )} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <h3 className="font-medium text-sm">
                {isPaid && "Paid action: "}
                {isSignal && "Signal source: "}
                {isSkill && "Improvement for "}
                <span className="font-mono text-[12px]">{p.target_id}</span>
              </h3>
              {p.miner && <Badge variant="muted">{p.miner}</Badge>}
              {p.confidence && (
                <Badge variant={p.confidence === "high" ? "success" : "warning"}>
                  {p.confidence}{p.evidence_count ? ` · ${p.evidence_count} evidence` : ""}
                </Badge>
              )}
            </div>
            <p className="text-sm text-muted-foreground italic">"{p.issue}"</p>
            {p.proposed_change && (
              <pre className="text-xs bg-subtle/50 rounded-md p-3 whitespace-pre-wrap font-sans my-3 leading-relaxed border">
                {p.proposed_change}
              </pre>
            )}
            <div className="flex gap-2">
              <Button variant="success" size="sm" disabled={busy} onClick={onApprove}>
                {isPaid ? "Approve & queue apply" : isSkill ? "Accept as candidate" : "Approve"}
              </Button>
              <Button variant="outline" size="sm" disabled={busy} onClick={onDismiss}>
                Dismiss
              </Button>
            </div>
            <EvidenceDrawer
              miner={p.miner}
              evidence={p.evidence ?? {}}
              evidenceCount={p.evidence_count}
            />
          </div>
        </div>
      </CardContent>
    </Card>
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

function Scorecard({ label, value, tone = "default" }: {
  label: string;
  value: number;
  tone?: "default" | "success" | "warning" | "muted";
}) {
  const toneClass = {
    default: "text-foreground",
    success: "text-success",
    warning: "text-warning",
    muted: "text-muted-foreground",
  }[tone];
  return (
    <Card>
      <CardContent className="p-4">
        <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium">{label}</p>
        <p className={cn("text-3xl font-semibold mt-1 tabular-nums font-serif", toneClass)}>{value}</p>
      </CardContent>
    </Card>
  );
}


// ---------------------------------------------------------------------------
// AgentSkillPromotionCard — the Skill-library variant of the decision card.
//
// Playbook promotions show "+X% lift on metric Y" because they came from an
// A/B test. Agent Skill promotions come from a Self-Critique proposal that
// passed the cross-channel guardrail in promotion_gate — there's no lift
// number yet, but we *do* have the cross-channel baseline that triggered
// the proposal. We surface that baseline plus the proposed_diff so the
// founder can see "this Skill is dragging on these channels, here's the
// patch that addresses it."
// ---------------------------------------------------------------------------

interface AgentSkillPromotionCardProps {
  skill: Skill;
  onApprove: () => void;
  onReject: () => void;
  pending: boolean;
}

function AgentSkillPromotionCard({ skill, onApprove, onReject, pending }: AgentSkillPromotionCardProps) {
  const pr = skill.promotion_request!;
  const baselines = pr.per_channel_baseline ?? {};
  const channels = Object.keys(baselines);

  return (
    <Card className="card-decision">
      <CardContent className="p-5">
        <div className="flex items-start gap-4">
          <div className="rounded-full bg-primary/10 ring-1 ring-primary/20 p-2 mt-1">
            <BookOpen className="h-4 w-4 text-primary" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <h3 className="font-medium text-sm">
                Update playbook: {humanizeSkillName(skill._id)}
              </h3>
              <Badge variant="outline">
                {humanizeSkillName(pr.incumbent)} → {humanizeSkillName(pr.candidate)}
              </Badge>
              {pr.confidence && (
                <Badge variant={pr.confidence === "high" ? "success" : "warning"}>
                  {pr.confidence}
                  {typeof pr.evidence_count === "number" && ` · ${pr.evidence_count} signals`}
                </Badge>
              )}
            </div>
            {pr.issue && (
              <p className="text-sm text-muted-foreground italic">"{pr.issue}"</p>
            )}

            {/* Channels at risk + baselines */}
            {(pr.channels_at_risk?.length ?? 0) > 0 && (
              <div className="mt-3 rounded-md border bg-subtle/30 p-3">
                <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium mb-2">
                  Cross-channel signal{" "}
                  <span className="normal-case tracking-normal text-foreground/70">
                    (baseline brand_voice
                    {typeof pr.project_baseline_brand_voice === "number" &&
                      ` ${pr.project_baseline_brand_voice.toFixed(3)}`})
                  </span>
                </p>
                <div className="flex flex-wrap gap-2">
                  {channels.map((ch) => {
                    const score = baselines[ch]?.brand_voice;
                    const atRisk = pr.channels_at_risk?.includes(ch);
                    return (
                      <div
                        key={ch}
                        className={cn(
                          "px-2 py-1 rounded-md text-xs flex items-center gap-1.5",
                          atRisk
                            ? "bg-destructive/10 text-destructive ring-1 ring-destructive/20"
                            : "bg-card text-muted-foreground ring-1 ring-border",
                        )}
                      >
                        <span className="font-medium">{ch}</span>
                        {typeof score === "number" && (
                          <span className="font-mono tabular-nums">{score.toFixed(3)}</span>
                        )}
                        {atRisk && <AlertTriangle className="h-3 w-3" />}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* The actual patch */}
            <div className="mt-3">
              <MarkdownDiff diff={pr.proposed_diff ?? ""} />
            </div>

            <div className="flex gap-2 mt-4 items-center flex-wrap">
              <ConfirmButton variant="success" size="sm" confirmLabel="Confirm rollout"
                disabled={pending} onConfirm={onApprove}>
                <CheckCircle2 className="h-3.5 w-3.5" /> Approve & roll out
              </ConfirmButton>
              <Button variant="outline" size="sm" onClick={onReject} disabled={pending}>
                <X className="h-3.5 w-3.5" /> Reject
              </Button>
              <Link to={`/skills?skill=${skill._id}`} className={MORE_LINK}>See affected drafts →</Link>
            </div>
            <p className="text-[11px] text-muted-foreground mt-2">
              Approval rolls the new playbook out to every agent automatically — they'll use it on the next draft.
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
