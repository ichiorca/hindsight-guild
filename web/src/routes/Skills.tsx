import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Layers, History, Lightbulb, Trophy, ChevronRight, BookOpen, Wrench,
  FileCode2, TrendingDown, AlertTriangle,
} from "lucide-react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Legend, ReferenceLine,
} from "recharts";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Empty } from "@/components/ui/Empty";
import { Skeleton } from "@/components/ui/Skeleton";
import { Button } from "@/components/ui/Button";
import {
  useSkills, useSkill, useDecidePromotion, useDecideCritique, useSkillSamples,
  useSkillBody, useRubricTrend, useDriftInvestigations,
} from "@/lib/api";
import { formatDate, pct, cn, icpLabel, channelLabel } from "@/lib/utils";
import type { Skill } from "@/lib/types";
import { ChannelPreview } from "@/components/ChannelPreview";
import { ShipReadiness } from "@/components/ShipReadiness";
import { EvidenceDrawer } from "@/components/EvidenceDrawer";

const TREND_COLORS = ["#0090db", "#00b8b8", "#5fb8eb", "#f59e0b", "#ef4444"];

/**
 * Display the same version string consistently everywhere on this page.
 * The Mongo `skills` collection stores two different shapes:
 *   - playbook skills: current_version = "linkedin_post_v3.txt"  → show "v3"
 *   - agent skills:    current_version = "v1"                     → show "v1"
 * Strip the filename prefix + extension so the badge says "v3", not the
 * full filename — which is the inconsistency the user called out.
 */
/**
 * Skill maturity classification. Version count alone is a poor signal — a
 * skill churned 5 times (instability) would score identically to one that
 * steadily improved. So "mature" requires BOTH shipped improvements AND a
 * current version that's actually performing well; raw version count only
 * decides "active" vs "fresh" when no quality data exists yet.
 *
 *   mature : ≥3 versions shipped AND current-version brand_voice is healthy
 *   active : ≥2 versions shipped (at least one improvement landed)
 *   fresh  : only the original version
 */
const HEALTHY_VOICE = 0.75;

function classifyMaturity(skill: Skill): "mature" | "active" | "fresh" {
  const versions = skill.history?.length ?? 1;
  const quality = skill.current_version
    ? skill.track_record?.[skill.current_version]?.mean_brand_voice
    : undefined;
  // Green only when there's both a track record of improvement AND the
  // current version is performing (or we have no quality data to say otherwise).
  if (versions >= 3 && (quality == null || quality >= HEALTHY_VOICE)) return "mature";
  if (versions >= 2) return "active";
  return "fresh";
}

function MaturityBadge({ skill }: { skill: Skill }) {
  const m = classifyMaturity(skill);
  // The emoji is decorative (aria-hidden) so screen readers don't read
  // "green circle"; the word + title carry the meaning. Titles describe what
  // the badge actually measures (shipped version count) without implying
  // "more versions = better".
  if (m === "mature") {
    return (
      <Badge variant="success" className="text-[10px]" title="Multiple versions shipped and the current one is performing well">
        <span aria-hidden>🟢</span> mature
      </Badge>
    );
  }
  if (m === "active") {
    return (
      <Badge variant="warning" className="text-[10px]" title="At least one improved version has shipped">
        <span aria-hidden>🟡</span> active
      </Badge>
    );
  }
  return (
    <Badge variant="muted" className="text-[10px]" title="Only the original version so far">
      <span aria-hidden>⚪</span> fresh
    </Badge>
  );
}

function cleanVersionLabel(skillId: string, raw?: string | null): string {
  if (!raw) return "—";
  let v = raw;
  v = v.replace(/\.(txt|md)$/i, "");
  if (v.startsWith(`${skillId}_`)) v = v.slice(skillId.length + 1);
  return v;
}

export default function SkillsPage() {
  const { data, isLoading } = useSkills();
  // Deep-linkable selection: ?skill=<id> preselects a playbook (e.g. the
  // "See proof →" links from Weekly Review). The detail is fetched fresh via
  // useSkill so a deep-link doesn't depend on the list query being warm.
  const [searchParams, setSearchParams] = useSearchParams();
  const urlSkill = searchParams.get("skill");
  const [selected, setSelected] = useState<string | null>(urlSkill);

  const selectSkill = (id: string) => {
    setSelected(id);
    setSearchParams((prev) => {
      const p = new URLSearchParams(prev);
      p.set("skill", id);
      return p;
    }, { replace: true });
  };

  const detail = useSkill(selected ?? "");
  const selectedSkill = detail.data ?? data?.find((s) => s._id === selected) ?? data?.[0];

  return (
    <div className="flex flex-col">
      <PageHeader
        eyebrow="How the team gets better"
        title="Playbooks"
        description="Each playbook has versions. A new version only replaces the old one when it actually produces better copy — measured, not voted."
      />

      <div className="p-5 sm:p-8 grid grid-cols-1 lg:grid-cols-3 gap-6 max-w-7xl">
        {/* Left: list */}
        <div className="lg:col-span-1 space-y-2">
          {isLoading && <><Skeleton className="h-24" /><Skeleton className="h-24" /></>}
          {data && data.length > 0 && (
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium px-1 pb-1">
              Click a playbook to see its history + how it's performing
            </p>
          )}
          {data?.map((s) => {
            const isSelected = selectedSkill?._id === s._id;
            // skill_kind tells us if this is a playbook (versioned per channel)
            // or an Agent Skill (cross-channel library entry from the Tier-2/3
            // SKILL.md set). They're rendered the same; the icon flags the kind.
            const isAgentSkill = (s as { skill_kind?: string }).skill_kind === "agent_skill";
            const KindIcon = isAgentSkill ? BookOpen : Wrench;
            return (
              <button
                key={s._id}
                onClick={() => selectSkill(s._id)}
                className={cn(
                  "w-full text-left p-4 rounded-xl border transition-all cursor-pointer group",
                  isSelected
                    ? "border-primary bg-primary/5 shadow-sm"
                    : "hover:bg-subtle/60 hover:border-foreground/20",
                )}
              >
                <div className="flex items-start gap-2 mb-1.5">
                  <KindIcon className={cn(
                    "h-4 w-4 mt-0.5 shrink-0",
                    isAgentSkill ? "text-agent-research" : "text-muted-foreground",
                  )} />
                  <h4 className="font-medium text-sm flex-1 leading-tight">{s._id.replace(/_/g, " ")}</h4>
                  <ChevronRight className={cn(
                    "h-4 w-4 shrink-0 transition-transform",
                    isSelected ? "text-primary translate-x-0.5" : "text-muted-foreground/40 group-hover:text-muted-foreground",
                  )} />
                </div>
                <div className="flex items-center gap-1.5 flex-wrap mt-2">
                  <Badge variant="outline" className="font-mono text-[10px]">
                    {cleanVersionLabel(s._id, s.current_version)}
                  </Badge>
                  <MaturityBadge skill={s} />
                  {isAgentSkill && (
                    <Badge variant="muted" className="text-[10px]">team-wide</Badge>
                  )}
                  {s.promotion_request && <Badge variant="warning" className="text-[10px]">ready to update</Badge>}
                  {s.self_critique_proposal && !s.promotion_request && (
                    <Badge variant="default" className="text-[10px]">improvement suggested</Badge>
                  )}
                </div>
                <div className="flex gap-1 mt-2 flex-wrap">
                  {s.applies_to.channels.map((ch) => (
                    <Badge key={ch} variant="muted" className="text-[10px]">{channelLabel(ch)}</Badge>
                  ))}
                </div>
              </button>
            );
          })}
        </div>

        {/* Right: detail */}
        <div className="lg:col-span-2">
          {selectedSkill ? (
            <SkillDetail skill={selectedSkill} />
          ) : (
            <Empty icon={Layers} title="Pick a skill." description="Each playbook has a version history, a candidate or two, and a track record." />
          )}
        </div>
      </div>
    </div>
  );
}

function SkillDetail({ skill }: { skill: Skill }) {
  const decidePromo = useDecidePromotion();
  const decideCritique = useDecideCritique();

  return (
    <div className="space-y-4">
      {/* Hero */}
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <CardTitle className="font-serif text-xl">{skill._id.replace(/_/g, " ")}</CardTitle>
            <Badge variant="outline" className="font-mono shrink-0">
              {cleanVersionLabel(skill._id, skill.current_version)}
            </Badge>
          </div>
          <CardDescription>
            {skill.promoted_at && <>updated {formatDate(skill.promoted_at)}</>}
            {skill.promoted_from_experiment && (
              <> · won an experiment</>
            )}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex gap-2 flex-wrap">
            {skill.applies_to.icp_segments.map((s) => (
              <Badge key={s} variant="default">{icpLabel(s)}</Badge>
            ))}
            {skill.applies_to.channels.map((c) => (
              <Badge key={c} variant="muted">{channelLabel(c)}</Badge>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Playbook / SKILL.md viewer — the actual content that this skill
          loads into agents. Collapsed by default so the rest of the page
          isn't pushed down by a long markdown body. */}
      <SkillBodyViewer skillId={skill._id} version={skill.current_version} />

      {/* Per-skill drift viewer — overlays the 28-day brand_voice rubric
          trend filtered to THIS skill's channels, plus any open drift
          investigations the daily drift_detect cron raised. */}
      <SkillDriftPanel skill={skill} />

      {/* Version timeline — story of how this playbook evolved */}
      {skill.history.length > 1 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <History className="h-4 w-4 text-muted-foreground" /> How this playbook evolved
            </CardTitle>
            <CardDescription>How this playbook got to where it is.</CardDescription>
          </CardHeader>
          <CardContent>
            <ol className="relative">
              <span className="absolute left-3 top-3 bottom-3 w-px bg-border" aria-hidden />
              {skill.history.map((v, i) => {
                const isCurrent = v === skill.current_version;
                const tr = skill.track_record?.[v];
                // Lift vs previous version's brand_voice (positive = improved).
                const prev = i > 0 ? skill.history[i - 1] : undefined;
                const prevTr = prev ? skill.track_record?.[prev] : undefined;
                const lift = (tr && prevTr)
                  ? tr.mean_brand_voice - prevTr.mean_brand_voice
                  : null;
                return (
                  <li key={v} className="relative flex items-start gap-4 pl-0.5 pb-5 last:pb-0">
                    <span className={cn(
                      "relative z-10 inline-flex items-center justify-center h-6 w-6 rounded-full text-[10px] font-semibold shrink-0",
                      isCurrent
                        ? "bg-primary text-primary-foreground ring-4 ring-primary/15"
                        : "bg-muted text-muted-foreground ring-2 ring-card",
                    )}>
                      {i + 1}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <code className="font-mono text-sm">{cleanVersionLabel(skill._id, v)}</code>
                        {isCurrent && <Badge variant="success" className="text-[10px]">current</Badge>}
                        {i === 0 && (
                          <Badge variant="muted" className="text-[10px]" title="The seed version — what the skill started as">
                            <span aria-hidden>✍️</span> initial seed
                          </Badge>
                        )}
                        {/* Lift annotation (only when we have both endpoints).
                            "pp" = percentage points vs the previous version —
                            spelled out in the tooltip. */}
                        {lift !== null && Math.abs(lift) >= 0.005 && (
                          <Badge
                            variant={lift > 0 ? "success" : "destructive"}
                            className="text-[10px]"
                            title={`Brand-voice score ${lift > 0 ? "up" : "down"} ${Math.abs(lift * 100).toFixed(1)} percentage points vs ${cleanVersionLabel(skill._id, prev)}`}
                          >
                            {lift > 0 ? "+" : ""}{(lift * 100).toFixed(1)}pp voice
                          </Badge>
                        )}
                      </div>
                      {tr && (
                        <p className="text-[12px] text-muted-foreground mt-0.5 font-mono">
                          {tr.action_count} actions · voice {pct(tr.mean_brand_voice, 1)} · support {pct(tr.mean_claim_support, 1)}
                        </p>
                      )}
                      {/* Provenance for the current version — when + why it landed. */}
                      {isCurrent && (skill.promoted_at || skill.promoted_from_experiment) && (
                        <p className="text-[11px] text-muted-foreground mt-1 leading-tight">
                          {skill.promoted_at && <>promoted {formatDate(skill.promoted_at)}</>}
                          {skill.promoted_from_experiment && (
                            <> · won experiment <code className="font-mono">{skill.promoted_from_experiment}</code></>
                          )}
                        </p>
                      )}
                    </div>
                  </li>
                );
              })}
              {skill.candidates.length > 0 && skill.candidates.map((v) => {
                const tr = skill.track_record?.[v];
                // Provenance for candidates: if the current self_critique_proposal
                // matches this candidate, we can name the miner.
                const proposal = skill.self_critique_proposal;
                const proposedByMiner =
                  proposal && proposal.candidate_id === v ? proposal.miner : undefined;
                return (
                  <li key={v} className="relative flex items-start gap-4 pl-0.5">
                    <span className="relative z-10 inline-flex items-center justify-center h-6 w-6 rounded-full text-[10px] font-semibold bg-warning/15 text-warning ring-2 ring-card">
                      ?
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <code className="font-mono text-sm">{cleanVersionLabel(skill._id, v)}</code>
                        <Badge variant="warning" className="text-[10px]">new version being tested</Badge>
                        {proposedByMiner && (
                          <Badge variant="muted" className="text-[10px]" title="Proposed by a self-critique miner">
                            <span aria-hidden>💡</span> {proposedByMiner} miner
                          </Badge>
                        )}
                      </div>
                      {tr && (
                        <p className="text-[12px] text-muted-foreground mt-0.5 font-mono">
                          {tr.action_count} actions · voice {pct(tr.mean_brand_voice, 1)}
                        </p>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          </CardContent>
        </Card>
      )}

      {/* Promotion request — the high-stakes decision.
          Only the playbook (A/B) kind has lift / stats / counts. The
          agent_skill kind renders fully via AgentSkillPromotionCard on the
          WeeklyReview page; here we show a stub link instead. */}
      {skill.promotion_request && skill.promotion_request.kind !== "agent_skill" &&
       skill.promotion_request.candidate_stats &&
       skill.promotion_request.incumbent_stats &&
       typeof skill.promotion_request.candidate_n === "number" &&
       typeof skill.promotion_request.incumbent_n === "number" && (
        <>
          <Card className="card-decision">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Trophy className="h-5 w-5 text-success" />
                New version is performing better
              </CardTitle>
              <CardDescription>
                The proposed version ({cleanVersionLabel(skill._id, skill.promotion_request.candidate)})
                outperformed the current one ({cleanVersionLabel(skill._id, skill.promotion_request.incumbent)})
                by{" "}<span className="text-success font-semibold">+{pct(skill.promotion_request.lift ?? 0, 1)}</span>{" "}
                on {(skill.promotion_request.success_metric ?? "the success metric").replace(/_/g, " ")}.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <StatComparison
                incumbent={skill.promotion_request.incumbent_stats}
                candidate={skill.promotion_request.candidate_stats}
                incumbentN={skill.promotion_request.incumbent_n}
                candidateN={skill.promotion_request.candidate_n}
              />
              <div className="flex gap-2 mt-4">
                <Button variant="success" size="sm" onClick={() => decidePromo.mutate({ skill_id: skill._id, decision: "approve" })} disabled={decidePromo.isPending}>
                  Promote
                </Button>
                <Button variant="outline" size="sm" onClick={() => decidePromo.mutate({ skill_id: skill._id, decision: "reject" })}>
                  Reject
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* Side-by-side sample drafts — the qualitative proof */}
          <SampleDraftsComparison
            skillId={skill._id}
            incumbent={skill.promotion_request.incumbent}
            candidate={skill.promotion_request.candidate}
          />
        </>
      )}

      {/* Agent-skill promotions are reviewed (with the full cross-channel diff)
          on Weekly Review, not here. Previously the Skills list showed a
          "ready to update" badge that led to NOTHING on this detail — a
          dead-end. Render an explicit hand-off so the badge is actionable. */}
      {skill.promotion_request && skill.promotion_request.kind === "agent_skill" && (
        <Card className="card-decision">
          <CardContent className="p-5 flex items-start gap-4">
            <div className="rounded-full bg-success/10 ring-1 ring-success/20 p-2 mt-0.5">
              <Trophy className="h-4 w-4 text-success" />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="font-medium text-sm mb-1">A new version is ready for your review</h3>
              <p className="text-sm text-muted-foreground">
                {cleanVersionLabel(skill._id, skill.promotion_request.incumbent)} →{" "}
                {cleanVersionLabel(skill._id, skill.promotion_request.candidate)} ·{" "}
                this skill is used across channels, so its change is reviewed with the
                full cross-channel diff on Weekly Review.
              </p>
              <Link
                to="/weekly-review"
                className="inline-flex items-center h-9 px-4 mt-3 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
              >
                Review on Weekly Review →
              </Link>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Self-critique — same slot as the legacy weekly LlmAgent.
          The PRD-03 miner envelope adds ``miner`` + ``evidence`` fields
          which we surface here as a badge + EvidenceDrawer. The legacy
          LlmAgent envelope renders without them. */}
      {skill.self_critique_proposal && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Lightbulb className="h-5 w-5 text-warning" />
              {skill.self_critique_proposal.miner
                ? "Proposed by self-critique"
                : "The system noticed something"}
              {skill.self_critique_proposal.miner && (
                <Badge variant="muted" className="ml-1">
                  {skill.self_critique_proposal.miner} miner
                </Badge>
              )}
            </CardTitle>
            <CardDescription className="italic">"{skill.self_critique_proposal.issue}"</CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="text-xs bg-subtle/60 rounded-md p-4 whitespace-pre-wrap font-sans leading-relaxed border">
              {skill.self_critique_proposal.proposed_change}
            </pre>
            <div className="flex items-center gap-2 mt-3">
              <Badge variant={skill.self_critique_proposal.confidence === "high" ? "success" : "warning"}>
                {skill.self_critique_proposal.confidence} confidence
              </Badge>
              <span className="text-xs text-muted-foreground">
                {skill.self_critique_proposal.evidence_count} pieces of evidence
              </span>
            </div>
            <div className="flex gap-2 mt-4">
              <Button variant="success" size="sm" onClick={() => decideCritique.mutate({ skill_id: skill._id, decision: "accept" })}>
                {skill.self_critique_proposal.miner
                  ? "Approve"
                  : "Accept as candidate"}
              </Button>
              <Button variant="outline" size="sm" onClick={() => decideCritique.mutate({ skill_id: skill._id, decision: "reject" })}>
                {skill.self_critique_proposal.miner ? "Dismiss" : "Reject"}
              </Button>
            </div>
            {skill.self_critique_proposal.evidence && (
              <EvidenceDrawer
                miner={skill.self_critique_proposal.miner ?? null}
                evidence={skill.self_critique_proposal.evidence}
                evidenceCount={skill.self_critique_proposal.evidence_count}
              />
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function SampleDraftsComparison({
  skillId, incumbent, candidate,
}: { skillId: string; incumbent: string; candidate: string }) {
  const inc = useSkillSamples(skillId, incumbent, 3);
  const cand = useSkillSamples(skillId, candidate, 3);

  if (inc.isLoading || cand.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Read the proof, not just the stats</CardTitle>
          <CardDescription>Loading samples…</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Read the proof, not just the stats</CardTitle>
        <CardDescription>Three recent drafts from each version. Read both columns — does the candidate sound right?</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="space-y-3">
            <div className="flex items-center gap-2 pb-2 border-b">
              <span className="h-2 w-2 rounded-full bg-muted-foreground/40" />
              <span className="text-sm font-semibold">Current</span>
              <span className="text-xs text-muted-foreground">{cleanVersionLabel(skillId, incumbent)}</span>
            </div>
            {(inc.data ?? []).length === 0 && (
              <p className="text-sm text-muted-foreground italic">No samples on file for this version.</p>
            )}
            {(inc.data ?? []).map((d) => (
              <SampleCard key={d.telemetry_id} draft={d} />
            ))}
          </div>
          <div className="space-y-3">
            <div className="flex items-center gap-2 pb-2 border-b border-primary/30">
              <span className="h-2 w-2 rounded-full bg-primary animate-pulse-soft" />
              <span className="text-sm font-semibold">New version</span>
              <span className="text-xs text-muted-foreground">{cleanVersionLabel(skillId, candidate)}</span>
            </div>
            {(cand.data ?? []).length === 0 && (
              <p className="text-sm text-muted-foreground italic">New version hasn't drafted any samples yet.</p>
            )}
            {(cand.data ?? []).map((d) => (
              <SampleCard key={d.telemetry_id} draft={d} />
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function SampleCard({ draft }: { draft: { draft_text: string; channel: string | null; eval_scores: Record<string, number>; ts: string } }) {
  return (
    <div className="rounded-lg border bg-card overflow-hidden">
      <div className="px-3 py-2 border-b bg-subtle/40 flex items-center justify-between">
        <span className="text-[11px] text-muted-foreground">
          {new Date(draft.ts).toLocaleDateString()}
        </span>
        <ShipReadiness scores={draft.eval_scores} size="sm" />
      </div>
      <div className="p-3 max-h-48 overflow-y-auto">
        <ChannelPreview channel={draft.channel} text={draft.draft_text} className="shadow-none" />
      </div>
    </div>
  );
}

/**
 * Collapsible viewer for the skill's actual body — SKILL.md for agent
 * skills, prompts/<family>/<version>.txt for playbooks. The user asked
 * "no way to see the content of skill" — this fixes that. Lazy-fetched
 * on first expand to avoid bloating the initial page render.
 */
function SkillBodyViewer({ skillId, version }: { skillId: string; version: string }) {
  const [expanded, setExpanded] = useState(false);
  const body = useSkillBody(expanded ? skillId : undefined, version);

  return (
    <Card>
      <CardHeader>
        <button
          onClick={() => setExpanded((e) => !e)}
          className="w-full flex items-start justify-between gap-3 text-left group"
        >
          <div>
            <CardTitle className="flex items-center gap-2">
              <FileCode2 className="h-4 w-4 text-muted-foreground" />
              Read the playbook
            </CardTitle>
            <CardDescription>
              {expanded
                ? "The actual prompt / instructions agents load for this version."
                : "Click to see the prompt + instructions this skill ships to agents."}
            </CardDescription>
          </div>
          <ChevronRight
            className={cn(
              "h-4 w-4 shrink-0 mt-1 transition-transform",
              expanded && "rotate-90",
            )}
          />
        </button>
      </CardHeader>
      {expanded && (
        <CardContent>
          {body.isLoading && <Skeleton className="h-32" />}
          {body.error && (
            <p className="text-sm text-destructive">
              Couldn't load body: {(body.error as Error).message}
            </p>
          )}
          {body.data && (
            <>
              <div className="flex items-center gap-2 mb-2 text-[11px] text-muted-foreground">
                <code className="font-mono">{body.data.source_path}</code>
                <Badge variant="muted" className="text-[10px]">
                  {body.data.format}
                </Badge>
              </div>
              <pre className="text-xs bg-subtle/60 rounded-md p-4 whitespace-pre-wrap font-mono leading-relaxed border max-h-[480px] overflow-y-auto">
                {body.data.body}
              </pre>
            </>
          )}
        </CardContent>
      )}
    </Card>
  );
}

/**
 * Per-skill drift viewer. Two layered signals:
 *   - **Trend chart**: 28-day brand_voice rubric mean, filtered to channels
 *     this skill applies to. Quality floor reference at 0.7. The same data
 *     drives the Telemetry page's chart, but here it's scoped — drift on
 *     a single skill's channel is more actionable than the global mean.
 *   - **Open investigations**: drift_detect's auto-opened experiments
 *     (tagged "drift", state="running") whose channel intersects this
 *     skill's channels. Each gets a card with the hypothesis + primary
 *     metric so the founder can decide whether to act.
 *
 * Renders an empty state with guidance when no rubric data exists yet
 * (the common LOCAL_DEV case before any drafts have been graded).
 */
function SkillDriftPanel({ skill }: { skill: Skill }) {
  const trend = useRubricTrend(28);
  const drift = useDriftInvestigations();

  const skillChannels = new Set(skill.applies_to.channels);

  // Filter the trend points to channels this skill cares about, then
  // pivot to recharts-friendly rows: { day, bv_<channel>, bv_<channel>… }
  const filteredTrend = useMemo(() => {
    const points = (trend.data ?? []).filter(
      (p) => skillChannels.size === 0 || skillChannels.has(p.channel),
    );
    const byDay = new Map<string, Record<string, number | string>>();
    for (const p of points) {
      if (!byDay.has(p.day)) byDay.set(p.day, { day: p.day });
      byDay.get(p.day)![`bv_${p.channel}`] = p.mean_brand_voice;
    }
    return Array.from(byDay.values()).sort((a, b) =>
      String(a.day).localeCompare(String(b.day)),
    );
    // skillChannels intentionally omitted from deps — it's derived from skill
    // and would force a new Set ref on every render. trend.data + skill cover it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trend.data, skill._id]);

  const trendChannels = Array.from(
    new Set(
      (trend.data ?? [])
        .filter((p) => skillChannels.size === 0 || skillChannels.has(p.channel))
        .map((p) => p.channel),
    ),
  );

  // Open drift investigations that touch this skill's channels.
  const matchedInvestigations = useMemo(() => {
    return (drift.data ?? []).filter((exp) => {
      if (!exp.channel) return false;
      return skillChannels.has(exp.channel);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drift.data, skill._id]);

  const hasTrend = filteredTrend.length > 0;
  const hasInvestigations = matchedInvestigations.length > 0;
  const hasAnything = hasTrend || hasInvestigations;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <TrendingDown className="h-4 w-4 text-muted-foreground" />
          Drift signals
          {hasInvestigations && (
            <Badge variant="warning" className="text-[10px]">
              {matchedInvestigations.length} open
            </Badge>
          )}
        </CardTitle>
        <CardDescription>
          Brand-voice rubric over the last 28 days for this skill's channels.
          {" "}A drop &gt; 0.10 from the trailing baseline opens an investigation
          via the daily drift check.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {(trend.isLoading || drift.isLoading) && <Skeleton className="h-48" />}

        {!trend.isLoading && !drift.isLoading && !hasAnything && (
          <div className="rounded-md border border-info/30 bg-info/5 p-3 text-[12px] text-muted-foreground">
            No rubric data for this skill's channels yet. Once a draft on{" "}
            {skill.applies_to.channels.map((c) => channelLabel(c)).join(" / ")}
            {" "}is graded by the Review Agent + Vertex AI Eval Service, the
            trend line and any drift signals will populate here.
          </div>
        )}

        {!trend.isLoading && hasTrend && (
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={filteredTrend}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="day" tick={{ fontSize: 11 }} />
              <YAxis domain={[0.4, 1]} tick={{ fontSize: 11 }}
                tickFormatter={(v) => pct(Number(v), 0)}
                label={{ value: "Brand-voice score", angle: -90, position: "insideLeft", style: { fontSize: 11, fill: "var(--muted-foreground)" } }} />
              <Tooltip contentStyle={{ borderRadius: 8, fontSize: 12 }}
                formatter={(v: number | string) => pct(Number(v), 1)} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <ReferenceLine
                y={0.7}
                stroke="#ef4444"
                strokeDasharray="4 4"
                label={{ value: "Quality floor", fontSize: 10, fill: "#ef4444" }}
              />
              {trendChannels.map((ch, i) => (
                <Line
                  key={ch}
                  type="monotone"
                  dataKey={`bv_${ch}`}
                  name={channelLabel(ch)}
                  stroke={TREND_COLORS[i % TREND_COLORS.length]}
                  strokeWidth={2}
                  dot={{ r: 2 }}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}

        {hasInvestigations && (
          <div className="mt-4 space-y-2">
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium">
              Open investigations
            </p>
            {matchedInvestigations.map((exp) => (
              <div
                key={exp._id}
                className="rounded-md border border-warning/40 bg-warning/5 p-3 text-sm"
              >
                <div className="flex items-start gap-2">
                  <AlertTriangle className="h-4 w-4 text-warning shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium">{exp.title}</span>
                      {exp.channel && (
                        <Badge variant="muted" className="text-[10px]">
                          {channelLabel(exp.channel)}
                        </Badge>
                      )}
                      {exp.icp_segment && (
                        <Badge variant="muted" className="text-[10px]">
                          {icpLabel(exp.icp_segment)}
                        </Badge>
                      )}
                    </div>
                    <p className="text-[12px] text-muted-foreground mt-1 italic">
                      "{exp.hypothesis}"
                    </p>
                    <p className="text-[11px] text-muted-foreground mt-1 font-mono">
                      tracking {exp.success_metric.replace(/_/g, " ")}
                      {exp.mde != null && (
                        <> · sensitivity {pct(exp.mde, 1)}</>
                      )}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function StatComparison({
  incumbent, candidate, incumbentN, candidateN,
}: {
  incumbent: Record<string, number>;
  candidate: Record<string, number>;
  incumbentN: number;
  candidateN: number;
}) {
  const metrics = Object.keys(candidate);
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-xs text-muted-foreground border-b">
          <th className="py-2 font-medium">Metric</th>
          <th className="py-2 font-medium tabular-nums">Current</th>
          <th className="py-2 font-medium tabular-nums">New version</th>
          <th className="py-2 font-medium tabular-nums">Δ</th>
        </tr>
      </thead>
      <tbody>
        <tr className="border-b">
          <td className="py-2 text-xs">Sample size</td>
          <td className="py-2 tabular-nums">{incumbentN}</td>
          <td className="py-2 tabular-nums">{candidateN}</td>
          <td />
        </tr>
        {metrics.map((m) => {
          const delta = candidate[m] - incumbent[m];
          const positive = delta > 0;
          return (
            <tr key={m} className="border-b last:border-0">
              <td className="py-2 text-xs">{m.replace(/_/g, " ")}</td>
              <td className="py-2 tabular-nums">{pct(incumbent[m], 1)}</td>
              <td className="py-2 tabular-nums">{pct(candidate[m], 1)}</td>
              <td className={cn("py-2 tabular-nums font-medium", positive ? "text-success" : "text-destructive")}>
                {positive ? "+" : ""}{pct(delta, 1)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
