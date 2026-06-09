import { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { PencilLine, Play, Sparkles, Loader2, Users } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { Empty } from "@/components/ui/Empty";
import { ErrorState } from "@/components/ui/ErrorState";
import { RubricScores } from "@/components/RubricScores";
import { ChannelPreview } from "@/components/ChannelPreview";
import { PipelineStepper, type PipelineStep } from "@/components/PipelineStepper";
import { ShipReadiness } from "@/components/ShipReadiness";
import { AgentBadge } from "@/components/AgentBadge";
import { useDraft } from "@/lib/api";
import type { EvalScores } from "@/lib/types";
import { CHANNELS, ICP_LABELS } from "@/lib/utils";
import { AGENTS, type AgentId } from "@/lib/agents";
import { humanizeAgentName, humanizeSkillName } from "@/lib/humanize";

/**
 * Routing options on the Drafting page. "pipeline" (default) hands the
 * task to the full Research → Critique → Reviser → … → Finalizer team.
 * Anything else is a single-agent handoff — the request hits that agent's
 * dedicated A2A port directly, bypassing the pipeline. The Agents page
 * lists each one's capabilities + inbox.
 */
const ROUTING_OPTIONS: Array<{ id: string; label: string; description: string }> = [
  { id: "pipeline",              label: "Full drafting team", description: "Research → Content → Critique → Reviser → ImageBrief → Review → Finalizer (recommended for new copy)" },
  { id: "lifecycle_email_agent", label: "Lifecycle Email only", description: "Drafts a 3-5 step nurture sequence; no draft on a content channel" },
  { id: "paid_media_agent",      label: "Paid Media only",      description: "Proposes paused ad variants + stop-loss incidents for the chosen platform" },
  { id: "positioning_agent",     label: "Positioning only",     description: "Proposes new / updated messaging library claims; founder approves before promotion" },
  { id: "customer_voice_agent",  label: "Customer Voice only",  description: "Pulls quotable lines out of a transcript or NPS response (paste it in the Topic field)" },
  { id: "research_agent",        label: "Research only",        description: "Returns research_findings (voice, claims, web facts) without drafting anything" },
  { id: "review_agent",          label: "Review only",          description: "Grades the most recent draft + flags claims that need evidence" },
  { id: "image_brief_agent",     label: "ImageBrief only",      description: "Generates a hero image brief + Imagen render against an existing draft" },
  { id: "ops_qa_agent",          label: "Ops/QA sweep",         description: "Uptime + UTM + form checks; opens ops_incidents for failures" },
  { id: "cmo_planner",           label: "CMO weekly memo",      description: "Composes the weekly memo + self-verifies claims; ends in slack_approval" },
  { id: "analytics_agent",       label: "Analytics snapshot",   description: "Weekly counts from the actions mirror — drafts, channels, by-agent breakdown" },
  { id: "self_critique_agent",   label: "Self-critique sweep",  description: "Looks at recent edits and suggests playbook tweaks where the team keeps fixing the same things" },
];

export default function DraftingPage() {
  const [searchParams] = useSearchParams();
  const initialAgent = searchParams.get("agent") || "pipeline";

  const [icp, setIcp] = useState("seg_merchant_dtc");
  const [channel, setChannel] = useState("linkedin");
  const [topic, setTopic] = useState("");
  const [subtopics, setSubtopics] = useState("");
  const [visualPref, setVisualPref] = useState("auto");
  const [agentId, setAgentId] = useState<string>(initialAgent);
  const draft = useDraft();

  // Pipeline stepper. The agents genuinely run in SEQUENCE (Research →
  // Content → … → Review), so we walk the nodes left-to-right on a
  // time-based ESTIMATE while the call is in flight — honest about order,
  // explicitly labeled as estimated about timing (we don't get real
  // per-step events over A2A). The old behavior flipped all six nodes to
  // "running" at once, implying live parallel progress that wasn't real.
  const baseSteps =
    agentId === "pipeline" ? initialSteps() : singleAgentSteps(agentId);
  const [steps, setSteps] = useState<PipelineStep[]>(baseSteps);

  useEffect(() => {
    // Reset when the routing changes so the trace matches the route.
    setSteps(agentId === "pipeline" ? initialSteps() : singleAgentSteps(agentId));
  }, [agentId]);

  useEffect(() => {
    if (!draft.isPending) return;
    setSteps((s) => s.map((step, i) => ({ ...step, state: i === 0 ? "running" : "pending" })));
    let cur = 0;
    const id = setInterval(() => {
      cur += 1;
      setSteps((s) =>
        cur >= s.length
          ? s // hold the last node "running" until the response actually lands
          : s.map((step, i) => ({
              ...step,
              state: i < cur ? "done" : i === cur ? "running" : "pending",
            })),
      );
    }, 1600);
    return () => clearInterval(id);
  }, [draft.isPending, agentId]);

  useEffect(() => {
    if (draft.isSuccess) {
      setSteps((s) => s.map((step) => ({ ...step, state: "done" })));
    }
    if (draft.isError) {
      setSteps((s) => s.map((step) => ({ ...step, state: "pending" })));
    }
  }, [draft.isSuccess, draft.isError]);

  // The result shape varies by which agent ran. The backend stamps
  // ``shape`` on synthetic responses; live pipeline responses use the
  // Finalizer envelope (no ``shape`` field, but ``draft`` + ``image`` +
  // ``review`` present). Anything else is per-agent.
  const result = draft.data as
    | (
        & { synthetic?: boolean; shape?: string }
        // Pipeline / Content
        & { draft?: string; review?: { recommendation?: string; flags?: Array<{ phrase: string; issue: string }> };
            eval_scores?: EvalScores;
            research_findings?: { customer_voice?: string[]; approved_claims?: string[] };
            image?: { url: string | null; alt_text: string; aspect_ratio?: string; mode?: "api" | "stub" };
            images?: Array<{ url: string | null; alt_text: string; aspect_ratio?: string; mode?: "api" | "stub"; kind?: string }> }
        // Lifecycle Email
        & { email_sequence?: { _id: string; sequence_name: string; icp_segment: string;
                               steps: Array<{ step_num: number; subject: string; body: string;
                                              cta: string; delay_days: number }> } }
        // Paid Media
        & { paid_media_action?: { variants_proposed: Array<{ platform: string; headline: string;
                                                              body: string; cta: string;
                                                              test_axis?: string; rationale?: string }>;
                                   stop_loss_incidents: Array<{ campaign_id?: string; severity: string;
                                                                rationale: string }>;
                                   campaigns_reviewed: number; confidence?: string } }
        // Positioning
        & { positioning_proposals?: Array<{ _id: string; kind: string; claim_text: string;
                                            applies_to_icp: string[]; rationale: string;
                                            confidence?: string }> }
        // Customer Voice / Ops / CMO / Research
        & { inserted?: number; skipped_low_value?: number; summary?: string }
        & { checked?: number; incidents_opened?: number }
        & { memo_markdown?: string; proposed_experiments?: unknown[]; approval_id?: string | null }
        & { agent_id?: string; note?: string }
      )
    | undefined;

  const submit = () =>
    draft.mutate({
      icp_segment: icp,
      channel,
      topic_hint: topic,
      // Split on newlines or commas; trim + drop blanks.
      subtopics: subtopics
        .split(/[\n,]+/).map((s) => s.trim()).filter(Boolean),
      visual_pref: visualPref,
      agent_id: agentId === "pipeline" ? undefined : agentId,
    });

  // When a single agent is selected, render its profile chip next to the
  // CTA so the founder sees who they're handing off to.
  const routedAgentProfile =
    agentId !== "pipeline" && AGENTS[agentId as AgentId]
      ? AGENTS[agentId as AgentId]
      : null;

  // Some agents don't take a channel or a topic. Hide those fields when
  // they're irrelevant — keeps the brief form scannable and prevents the
  // founder from setting fields the agent will ignore.
  const needsChannel = !["customer_voice_agent", "positioning_agent",
                          "ops_qa_agent", "cmo_planner", "self_critique_agent"]
                          .includes(agentId);
  const topicLabel: Record<string, string> = {
    customer_voice_agent: "Raw text to ingest (sales-call transcript, NPS, etc.)",
    cmo_planner:          "Focus area for this week's memo (optional)",
    ops_qa_agent:         "Scope hint (optional — defaults to all ops_targets)",
    self_critique_agent:  "Specific skill to critique (optional — defaults to all)",
    positioning_agent:    "Positioning angle / claim to propose",
    paid_media_agent:     "Test angle for new variants (optional)",
    lifecycle_email_agent:"Sequence theme (e.g., 'handoff friction nurture')",
    research_agent:       "Topic for the research pass",
    image_brief_agent:    "Visual theme override (optional)",
    pipeline:             "Topic (optional — drives Content's anchoring)",
    content_agent:        "Topic (optional)",
  };
  const topicPlaceholder = topicLabel[agentId] ?? "Topic (optional)";

  return (
    <div className="flex flex-col">
      <PageHeader
        eyebrow="Live pipeline"
        title="Drafting"
        description="Hand the team an ICP and a channel. Research listens, Content drafts, Review grades. About 12 seconds."
      />

      <div className="p-5 sm:p-8 grid grid-cols-1 lg:grid-cols-5 gap-6 max-w-7xl">
        {/* Left: brief */}
        <div className="lg:col-span-2 space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <PencilLine className="h-4 w-4 text-primary" /> The brief
              </CardTitle>
              <CardDescription>What you'd give a real marketer in Slack.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">For (ICP segment)</label>
                <Select value={icp} onChange={(e) => setIcp(e.target.value)} className="mt-1">
                  {Object.entries(ICP_LABELS).map(([slug, label]) => (
                    <option key={slug} value={slug}>{label}</option>
                  ))}
                </Select>
                <p className="text-[11px] text-muted-foreground mt-1">
                  Decides which customer quotes and which past mistakes the team uses as context.
                </p>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Users className="h-3 w-3" />
                  Route to
                </label>
                <Select
                  value={agentId}
                  onChange={(e) => setAgentId(e.target.value)}
                  className="mt-1"
                >
                  {ROUTING_OPTIONS.map((o) => (
                    <option key={o.id} value={o.id}>{o.label}</option>
                  ))}
                </Select>
                <p className="text-[11px] text-muted-foreground mt-1">
                  {ROUTING_OPTIONS.find((o) => o.id === agentId)?.description}
                </p>
                {routedAgentProfile && (
                  <div className="mt-2 inline-flex items-center gap-2 rounded-md border border-primary/30 bg-primary/5 px-2 py-1.5 text-[11px]">
                    <AgentBadge id={routedAgentProfile.id} size="sm" />
                    <span className="text-muted-foreground">
                      handing off to <b className="text-foreground">{routedAgentProfile.name}</b> — bypassing the full pipeline
                    </span>
                  </div>
                )}
              </div>
              {needsChannel && (
                <div>
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Channel</label>
                  <Select value={channel} onChange={(e) => setChannel(e.target.value)} className="mt-1">
                    <optgroup label="Organic / owned">
                      {CHANNELS.filter((c) => c.group === "organic" || !c.group).map((c) => (
                        <option key={c.id} value={c.id}>{c.label}</option>
                      ))}
                    </optgroup>
                    <optgroup label="Lifecycle">
                      {CHANNELS.filter((c) => c.group === "lifecycle").map((c) => (
                        <option key={c.id} value={c.id}>{c.label}</option>
                      ))}
                    </optgroup>
                    <optgroup label="Paid">
                      {CHANNELS.filter((c) => c.group === "paid").map((c) => (
                        <option key={c.id} value={c.id}>{c.label}</option>
                      ))}
                    </optgroup>
                  </Select>
                  <p className="text-[11px] text-muted-foreground mt-1">
                    {CHANNELS.find((c) => c.id === channel)?.description}
                  </p>
                </div>
              )}
              {needsChannel && (
                <div>
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Visual style</label>
                  <Select value={visualPref} onChange={(e) => setVisualPref(e.target.value)} className="mt-1">
                    <option value="auto">Auto — let the team choose per topic</option>
                    <option value="infographic">Infographic — clean diagram</option>
                    <option value="excalidraw">Excalidraw — hand-drawn diagram</option>
                    <option value="contextual">Contextual image</option>
                  </Select>
                  <p className="text-[11px] text-muted-foreground mt-1">
                    Diagrams (infographic/excalidraw) have legible labels; contextual is an image-model scene. The team may produce 1–3 visuals.
                  </p>
                </div>
              )}
              <div>
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  {topicPlaceholder}
                </label>
                {agentId === "customer_voice_agent" ? (
                  <textarea
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
                    placeholder="Paste a sales-call transcript, support ticket thread, NPS responses…"
                    className="mt-1 flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm h-32 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  />
                ) : (
                  <Input
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
                    placeholder="handoff friction, time-to-value…"
                    className="mt-1"
                  />
                )}
              </div>
              {(agentId === "pipeline" || agentId === "content_agent") && (
                <div>
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                    Sub-topics (optional)
                  </label>
                  <Input
                    value={subtopics}
                    onChange={(e) => setSubtopics(e.target.value)}
                    placeholder="ACP auth handshake, product-feed schema, fallback UX…"
                    className="mt-1"
                  />
                  <p className="text-[11px] text-muted-foreground mt-1">
                    Secondary angles to also cover (comma- or newline-separated). The primary topic still drives the headline.
                  </p>
                </div>
              )}
              <Button
                onClick={submit}
                disabled={draft.isPending}
                className="w-full"
                size="lg"
              >
                {draft.isPending ? (
                  <><Loader2 className="h-4 w-4 animate-spin" /> Running…</>
                ) : routedAgentProfile ? (
                  <><Play className="h-4 w-4" /> Hand off to {routedAgentProfile.shortName}</>
                ) : (
                  <><Play className="h-4 w-4" /> Hand it to the team</>
                )}
              </Button>
              {draft.isError && (
                <p className="text-xs text-destructive">
                  {draft.error?.message ?? "Pipeline call failed"}
                </p>
              )}
            </CardContent>
          </Card>

          {/* Pipeline stepper — appears during the run and persists after */}
          {(draft.isPending || draft.isSuccess) && (
            <Card>
              <CardHeader>
                <CardTitle>Pipeline trace</CardTitle>
                <CardDescription>Each agent's contribution, live.</CardDescription>
              </CardHeader>
              <CardContent>
                <PipelineStepper steps={steps} />
              </CardContent>
            </Card>
          )}
        </div>

        {/* Right: outcome */}
        <div className="lg:col-span-3 space-y-4">
          {!result && !draft.isPending && !draft.isError && (
            <Empty
              icon={Sparkles}
              title="Nothing drafted yet."
              description="When you hand the team a brief, you'll see Research pull voice + claims + recent rejections, Content write, and Review score — estimated progress in the trace on the left."
            />
          )}

          {draft.isPending && (
            <Card>
              <CardContent className="p-8 flex flex-col items-center gap-3 text-sm text-muted-foreground">
                <Loader2 className="h-6 w-6 animate-spin text-primary" />
                <p>The team is drafting… progress estimate on the left.</p>
              </CardContent>
            </Card>
          )}

          {draft.isError && !draft.isPending && (
            <ErrorState
              what="the draft"
              onRetry={() => submit()}
            />
          )}

          {/* Don't keep showing the previous successful draft after a failed
              run — that read as a valid result next to an error. */}
          {result && !draft.isPending && !draft.isError && (
            <AgentResult result={result} channel={channel} routedAgent={routedAgentProfile?.id ?? null} />
          )}
        </div>
      </div>
    </div>
  );
}

// Default pipeline steps mirror the real Sequential graph in
// agents/pipeline.py:
//   Research → Content → Critique → Reviser → ImageBrief → Review → Finalizer
// The Critique+Reviser pair is wrapped in a LoopAgent (max_iterations=2).
function initialSteps(): PipelineStep[] {
  return [
    { agent: "research_agent",     state: "pending", summary: "" },
    { agent: "content_agent",      state: "pending", summary: "" },
    { agent: "review_agent",       state: "pending", summary: "Critique pass" },
    { agent: "content_agent",      state: "pending", summary: "Reviser pass — overwrites draft" },
    { agent: "image_brief_agent",  state: "pending", summary: "" },
    { agent: "review_agent",       state: "pending", summary: "Final review + claim validation" },
  ];
}

// Single-agent stepper — just the one agent for handoff runs. The
// agentId is constrained by ROUTING_OPTIONS to known AgentIds.
function singleAgentSteps(agentId: string): PipelineStep[] {
  return [{ agent: agentId as AgentId, state: "pending", summary: "" }];
}

// ---------------------------------------------------------------------------
// Per-shape result renderer
// ---------------------------------------------------------------------------

type AnyResult = NonNullable<ReturnType<typeof useDraft>["data"]> & {
  synthetic?: boolean; shape?: string;
};

function AgentResult({
  result, channel, routedAgent,
}: {
  result: AnyResult;
  channel: string;
  routedAgent: AgentId | null;
}) {
  const shape = (result as { shape?: string }).shape ?? "pipeline";
  const isSynthetic = !!result.synthetic;

  // IMPORTANT: dispatch by explicit shape FIRST. The fallback to
  // ``draft`` would steal review/lifecycle/paid responses (which also
  // carry a ``draft`` for context) and route them to the pipeline view.
  // Each per-shape branch returns explicitly; only fall through to the
  // pipeline view when the shape is unset OR explicitly pipeline/research.

  // Pipeline / Content — the original full-output view.
  if (shape === "pipeline" || shape === "research" ||
      (!shape && (result as { draft?: string }).draft)) {
    const r = result as {
      draft?: string;
      review?: { recommendation?: string; flags?: Array<{ phrase: string; issue: string }> };
      eval_scores?: EvalScores | null;
      research_findings?: { customer_voice?: string[]; approved_claims?: string[] };
      image?: { url: string | null; alt_text: string; aspect_ratio?: string; mode?: "api" | "stub" };
      images?: Array<{ url: string | null; alt_text: string; aspect_ratio?: string; mode?: "api" | "stub"; kind?: string }>;
    };
    return (
      <>
        <Card>
          <CardHeader className="pb-4">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div className="flex items-center gap-3">
                <AgentBadge id={routedAgent ?? "content_agent"} size="md" showName />
                <span className="text-xs text-muted-foreground">drafted this</span>
              </div>
              <div className="flex items-center gap-2">
                {isSynthetic && <Badge variant="muted">sample preview</Badge>}
                {r.eval_scores && <ShipReadiness scores={r.eval_scores} />}
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <ChannelPreview channel={channel} text={r.draft ?? ""} image={r.image ?? null} images={r.images ?? null} />
          </CardContent>
        </Card>

        {r.eval_scores ? (
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Review scores</CardTitle>
                <AgentBadge id="review_agent" size="sm" showName />
              </div>
              <CardDescription>Quality scores across all 6 rubrics, anchored against recent rejected drafts.</CardDescription>
            </CardHeader>
            <CardContent>
              <RubricScores scores={r.eval_scores} />
            </CardContent>
          </Card>
        ) : isSynthetic ? (
          <Card className="border-dashed">
            <CardContent className="p-4 text-[12px] text-muted-foreground">
              Quality scores aren't shown for the sample preview. Run a real
              draft (or deploy with quality scoring enabled) to see the
              full breakdown.
            </CardContent>
          </Card>
        ) : null}

        {r.research_findings && (
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>What Research surfaced</CardTitle>
                <AgentBadge id="research_agent" size="sm" showName />
              </div>
              <CardDescription>Passed forward into Content's drafting context via session state.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4 text-sm">
              {r.research_findings.customer_voice && r.research_findings.customer_voice.length > 0 && (
                <div>
                  <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium mb-2">
                    Customer voice ({r.research_findings.customer_voice.length})
                  </p>
                  <ul className="space-y-1.5">
                    {r.research_findings.customer_voice.map((v, i) => (
                      <li key={i} className="text-muted-foreground italic border-l-2 border-agent-research/40 pl-3">"{v}"</li>
                    ))}
                  </ul>
                </div>
              )}
              {r.research_findings.approved_claims && r.research_findings.approved_claims.length > 0 && (
                <div>
                  <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium mb-2">
                    Approved claims ({r.research_findings.approved_claims.length})
                  </p>
                  <ul className="space-y-1.5">
                    {r.research_findings.approved_claims.map((c, i) => (
                      <li key={i} className="text-foreground">— {c}</li>
                    ))}
                  </ul>
                </div>
              )}
            </CardContent>
          </Card>
        )}
      </>
    );
  }

  // Lifecycle Email — sequence with steps.
  if (shape === "lifecycle_email") {
    const seq = (result as { email_sequence?: {
      sequence_name: string; icp_segment: string;
      steps: Array<{ step_num: number; subject: string; body: string; cta: string; delay_days: number }>;
    } }).email_sequence;
    if (!seq) return <SyntheticNote />;
    return (
      <Card>
        <CardHeader className="pb-4">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-3">
              <AgentBadge id="lifecycle_email_agent" size="md" showName />
              <div>
                <p className="text-sm font-medium">{seq.sequence_name}</p>
                <p className="text-[11px] text-muted-foreground">{seq.steps.length} steps · {seq.icp_segment}</p>
              </div>
            </div>
            {isSynthetic && <Badge variant="muted">sample preview</Badge>}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {seq.steps.map((step) => (
            <div key={step.step_num} className="rounded-md border bg-card p-3">
              <div className="flex items-center justify-between gap-2 mb-1.5">
                <p className="text-[11px] font-mono text-muted-foreground">
                  step {step.step_num} {step.delay_days > 0 ? `· +${step.delay_days}d` : "· immediate"}
                </p>
                <Badge variant="outline" className="text-[10px]">
                  subject {step.subject.length} chars
                </Badge>
              </div>
              <p className="text-sm font-medium leading-snug">{step.subject}</p>
              <p className="text-sm text-muted-foreground mt-1.5 whitespace-pre-wrap">{step.body}</p>
              <p className="text-[11px] text-primary mt-2 truncate">CTA → {step.cta}</p>
            </div>
          ))}
        </CardContent>
      </Card>
    );
  }

  // Paid Media — variant grid + stop-loss incidents.
  if (shape === "paid_media") {
    const a = (result as { paid_media_action?: {
      variants_proposed: Array<{ platform: string; headline: string; body: string; cta: string;
                                 test_axis?: string; rationale?: string }>;
      stop_loss_incidents: Array<{ campaign_id?: string; severity: string; rationale: string }>;
      campaigns_reviewed: number; confidence?: string;
    } }).paid_media_action;
    if (!a) return <SyntheticNote />;
    return (
      <>
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div className="flex items-center gap-3">
                <AgentBadge id="paid_media_agent" size="md" showName />
                <p className="text-xs text-muted-foreground">
                  {a.variants_proposed.length} variant{a.variants_proposed.length === 1 ? "" : "s"} ·{" "}
                  {a.stop_loss_incidents.length} incident{a.stop_loss_incidents.length === 1 ? "" : "s"} ·{" "}
                  {a.campaigns_reviewed} campaign{a.campaigns_reviewed === 1 ? "" : "s"} reviewed
                </p>
              </div>
              <div className="flex items-center gap-2">
                {a.confidence && <Badge variant="outline" className="text-[10px]">confidence {a.confidence}</Badge>}
                {isSynthetic && <Badge variant="muted">sample preview</Badge>}
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium mb-3">
              Proposed variants — paused on insert
            </p>
            <div className="space-y-3">
              {a.variants_proposed.map((v, i) => (
                <div key={i} className="rounded-md border bg-card p-3">
                  <div className="flex items-center gap-2 mb-1">
                    <Badge variant="muted" className="font-mono text-[10px]">{v.platform}</Badge>
                    {v.test_axis && <Badge variant="outline" className="text-[10px]">tests {v.test_axis}</Badge>}
                  </div>
                  <p className="text-sm font-semibold">{v.headline}</p>
                  <p className="text-sm text-muted-foreground mt-1">{v.body}</p>
                  <p className="text-[11px] text-primary mt-1.5">CTA: {v.cta}</p>
                  {v.rationale && <p className="text-[11px] text-muted-foreground italic mt-2">— {v.rationale}</p>}
                </div>
              ))}
            </div>
            {a.stop_loss_incidents.length > 0 && (
              <>
                <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium mt-5 mb-2">
                  Stop-loss incidents
                </p>
                <div className="space-y-2">
                  {a.stop_loss_incidents.map((i, idx) => (
                    <div key={idx} className="rounded-md border border-warning/40 bg-warning/5 p-3 text-sm">
                      <div className="flex items-center gap-2 mb-1">
                        <Badge variant="warning" className="text-[10px]">severity {i.severity}</Badge>
                        {i.campaign_id && (
                          <span className="text-[10px] text-muted-foreground" title={i.campaign_id}>
                            Campaign #{i.campaign_id.slice(-6)}
                          </span>
                        )}
                      </div>
                      <p className="text-muted-foreground">{i.rationale}</p>
                    </div>
                  ))}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </>
    );
  }

  // Positioning — proposals list.
  if (shape === "positioning") {
    const ps = (result as { positioning_proposals?: Array<{
      _id: string; kind: string; claim_text: string; applies_to_icp: string[];
      rationale: string; confidence?: string;
    }> }).positioning_proposals ?? [];
    return (
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-3">
              <AgentBadge id="positioning_agent" size="md" showName />
              <p className="text-xs text-muted-foreground">
                {ps.length} proposal{ps.length === 1 ? "" : "s"} drafted · awaiting your decision
              </p>
            </div>
            {isSynthetic && <Badge variant="muted">sample preview</Badge>}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {ps.map((p) => (
            <div key={p._id} className="rounded-md border bg-card p-3">
              <div className="flex items-center gap-2 mb-1">
                <Badge variant="muted" className="text-[10px]">{p.kind}</Badge>
                {p.confidence && <Badge variant="outline" className="text-[10px]">confidence {p.confidence}</Badge>}
              </div>
              <p className="text-sm font-medium">"{p.claim_text}"</p>
              <p className="text-[11px] text-muted-foreground mt-1">applies to {p.applies_to_icp.join(", ")}</p>
              <p className="text-[12px] text-muted-foreground italic mt-2">— {p.rationale}</p>
            </div>
          ))}
        </CardContent>
      </Card>
    );
  }

  // Customer Voice ingest — counts only (no fake quotes).
  if (shape === "customer_voice") {
    const r = result as { inserted?: number; skipped_low_value?: number; summary?: string };
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <AgentBadge id="customer_voice_agent" size="md" showName />
            </div>
            {isSynthetic && <Badge variant="muted">sample preview</Badge>}
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4 mb-3">
            <div>
              <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Inserted</p>
              <p className="text-2xl font-semibold tabular-nums">{r.inserted ?? 0}</p>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Skipped</p>
              <p className="text-2xl font-semibold tabular-nums text-muted-foreground">{r.skipped_low_value ?? 0}</p>
            </div>
          </div>
          {r.summary && <p className="text-sm text-muted-foreground">{r.summary}</p>}
        </CardContent>
      </Card>
    );
  }

  // Ops/QA sweep.
  if (shape === "ops_qa") {
    const r = result as { checked?: number; incidents_opened?: number; summary?: string };
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <AgentBadge id="ops_qa_agent" size="md" showName />
            {isSynthetic && <Badge variant="muted">sample preview</Badge>}
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4 mb-3">
            <div>
              <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Targets checked</p>
              <p className="text-2xl font-semibold tabular-nums">{r.checked ?? 0}</p>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Incidents opened</p>
              <p className={
                "text-2xl font-semibold tabular-nums " +
                ((r.incidents_opened ?? 0) > 0 ? "text-warning" : "text-muted-foreground")
              }>
                {r.incidents_opened ?? 0}
              </p>
            </div>
          </div>
          {r.summary && <p className="text-sm text-muted-foreground">{r.summary}</p>}
        </CardContent>
      </Card>
    );
  }

  // CMO memo.
  if (shape === "cmo_memo") {
    const r = result as { memo_markdown?: string; approval_id?: string | null };
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <AgentBadge id="cmo_planner" size="md" showName />
            <div className="flex items-center gap-2">
              {r.approval_id && (
                <Badge variant="muted" className="text-[10px]" title={r.approval_id}>
                  Awaiting approval
                </Badge>
              )}
              {isSynthetic && <Badge variant="muted">sample preview</Badge>}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <pre className="text-sm bg-subtle/40 rounded-md p-4 whitespace-pre-wrap font-sans leading-relaxed border">
            {r.memo_markdown ?? "(no memo body)"}
          </pre>
        </CardContent>
      </Card>
    );
  }

  // Generic / unimplemented agent preview.
  // Review — flags + recommendation against the most recent draft.
  if (shape === "review") {
    const r = result as {
      draft?: string;
      channel?: string;
      review?: {
        flags: Array<{ phrase: string; issue: string; evidence_source?: string | null }>;
        qualitative_notes?: string;
        recommendation?: string;
        confidence?: string;
      };
    };
    const review = r.review ?? { flags: [] };
    return (
      <>
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <AgentBadge id="review_agent" size="md" showName />
              <div className="flex items-center gap-2">
                {review.recommendation && (
                  <Badge variant={
                    review.recommendation === "pass" ? "success" :
                    review.recommendation === "edit" ? "warning" : "destructive"
                  }>
                    {review.recommendation}
                  </Badge>
                )}
                {review.confidence && (
                  <Badge variant="outline" className="text-[10px]">confidence {review.confidence}</Badge>
                )}
                {isSynthetic && <Badge variant="muted">sample preview</Badge>}
              </div>
            </div>
            <CardDescription className="mt-1">
              {review.flags.length} flag{review.flags.length === 1 ? "" : "s"} on the most recent draft.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {review.flags.length === 0 ? (
              <p className="text-sm text-muted-foreground italic">No flags — draft would ship cleanly.</p>
            ) : (
              <div className="space-y-2">
                {review.flags.map((f, i) => (
                  <div key={i} className="rounded-md border border-warning/40 bg-warning/5 p-3 text-sm">
                    <div className="flex items-center gap-2 mb-1">
                      <Badge variant="warning" className="text-[10px]">{f.issue}</Badge>
                      {f.evidence_source && (
                        <Badge variant="outline" className="text-[10px]">source: {f.evidence_source}</Badge>
                      )}
                    </div>
                    <p className="font-mono text-[12px]">{f.phrase}</p>
                  </div>
                ))}
              </div>
            )}
            {review.qualitative_notes && (
              <p className="text-[12px] text-muted-foreground italic">{review.qualitative_notes}</p>
            )}
          </CardContent>
        </Card>

        {r.draft && (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">The draft being reviewed</CardTitle>
            </CardHeader>
            <CardContent>
              <ChannelPreview channel={r.channel ?? channel} text={r.draft} />
            </CardContent>
          </Card>
        )}
      </>
    );
  }

  // ImageBrief — real generated image (Imagen 4 fast in LOCAL_DEV).
  if (shape === "image_brief") {
    const img = (result as { image?: {
      mode: "api" | "stub"; url: string | null; alt_text: string; prompt: string;
      aspect_ratio?: string; reason?: string;
    } }).image;
    if (!img) return <SyntheticNote agentId="image_brief_agent" />;
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <AgentBadge id="image_brief_agent" size="md" showName />
            <div className="flex items-center gap-2">
              <Badge variant={img.mode === "api" ? "success" : "warning"}>
                {img.mode === "api" ? "generated" : "stub"}
              </Badge>
              {isSynthetic && <Badge variant="muted">sample preview</Badge>}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {img.mode === "api" && img.url ? (
            <img
              src={img.url}
              alt={img.alt_text}
              className="w-full rounded-md object-cover"
              style={{ aspectRatio: img.aspect_ratio?.replace(":", "/") || "16/9" }}
              loading="lazy"
            />
          ) : (
            <div className="rounded-md border-2 border-dashed border-warning/40 bg-warning/5 p-6 text-center text-sm text-muted-foreground italic">
              Image generation failed — {img.reason ?? "stub returned"}
            </div>
          )}
          <div className="space-y-1.5 text-[12px]">
            <p><span className="text-muted-foreground">Alt:</span> {img.alt_text}</p>
            <p className="text-muted-foreground">
              <span className="text-foreground">Prompt:</span> {img.prompt}
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  // Analytics — Mongo-derived weekly snapshot.
  if (shape === "analytics") {
    const s = (result as { analytics_snapshot?: {
      window: string; total_actions: number; drafts: number;
      channels_touched: string[]; by_agent: Array<{ agent: string; count: number }>;
      note: string;
    } }).analytics_snapshot;
    if (!s) return <SyntheticNote agentId="analytics_agent" />;
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <AgentBadge id="analytics_agent" size="md" showName />
            <div className="flex items-center gap-2">
              <Badge variant="muted">{s.window} window</Badge>
              {isSynthetic && <Badge variant="muted">sample preview</Badge>}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
            <div>
              <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Total actions</p>
              <p className="text-2xl font-semibold tabular-nums mt-1">{s.total_actions}</p>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Drafts</p>
              <p className="text-2xl font-semibold tabular-nums mt-1">{s.drafts}</p>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Channels</p>
              <p className="text-2xl font-semibold tabular-nums mt-1">{s.channels_touched.length}</p>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wider text-muted-foreground">Agents</p>
              <p className="text-2xl font-semibold tabular-nums mt-1">{s.by_agent.length}</p>
            </div>
          </div>
          <div className="space-y-1.5 mb-3">
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium">By agent</p>
            <div className="space-y-1">
              {s.by_agent.map((a) => (
                <div key={a.agent} className="flex items-center justify-between text-[12px]">
                  <span>{humanizeAgentName(a.agent)}</span>
                  <span className="tabular-nums">{a.count}</span>
                </div>
              ))}
            </div>
          </div>
          <p className="text-[12px] text-muted-foreground italic">{s.note}</p>
        </CardContent>
      </Card>
    );
  }

  // Self-Critique — list of awaiting-review proposals + sweep count.
  if (shape === "self_critique") {
    const s = (result as { self_critique?: {
      awaiting_review: Array<{ skill_id: string; issue?: string; proposed_change?: string;
                                confidence?: string; evidence_count?: number }>;
      sweeps_7d: number; note: string;
    } }).self_critique;
    if (!s) return <SyntheticNote agentId="self_critique_agent" />;
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <AgentBadge id="self_critique_agent" size="md" showName />
            <div className="flex items-center gap-2">
              <Badge variant="muted">{s.sweeps_7d} sweep{s.sweeps_7d === 1 ? "" : "s"} this week</Badge>
              {isSynthetic && <Badge variant="muted">sample preview</Badge>}
            </div>
          </div>
          <CardDescription className="mt-1">
            {s.awaiting_review.length} skill{s.awaiting_review.length === 1 ? "" : "s"} flagged for review.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {s.awaiting_review.length === 0 ? (
            <p className="text-sm text-muted-foreground italic">
              No patterns flagged this week. The agent ran but didn't find a strong-enough signal.
            </p>
          ) : (
            s.awaiting_review.map((p) => (
              <div key={p.skill_id} className="rounded-md border bg-card p-3">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-sm font-medium">{humanizeSkillName(p.skill_id)}</span>
                  {p.confidence && (
                    <Badge variant="outline" className="text-[10px]">{p.confidence} confidence</Badge>
                  )}
                  {typeof p.evidence_count === "number" && (
                    <span className="text-[11px] text-muted-foreground">{p.evidence_count} evidence rows</span>
                  )}
                </div>
                {p.issue && <p className="text-sm italic">"{p.issue}"</p>}
                {p.proposed_change && (
                  <pre className="text-[12px] bg-subtle/60 rounded p-2 mt-2 whitespace-pre-wrap font-sans border">
                    {p.proposed_change}
                  </pre>
                )}
              </div>
            ))
          )}
          <p className="text-[12px] text-muted-foreground italic">{s.note}</p>
        </CardContent>
      </Card>
    );
  }

  return <SyntheticNote note={(result as { note?: string }).note} agentId={(result as { agent_id?: string }).agent_id} />;
}

function SyntheticNote({ note, agentId }: { note?: string; agentId?: string } = {}) {
  return (
    <Card className="border-dashed">
      <CardContent className="p-5 text-sm text-muted-foreground space-y-2">
        <p className="font-medium text-foreground">
          {agentId ? `${agentId} preview` : "Synthetic preview"}
        </p>
        <p>
          {note ??
            "This agent's synthetic-fallback view isn't implemented. Start the A2A process for this agent (see agents/a2a_server.py) and the live output will render here."}
        </p>
      </CardContent>
    </Card>
  );
}
