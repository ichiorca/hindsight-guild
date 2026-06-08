/**
 * Agents — the team roster.
 *
 * One card per agent showing identity, capabilities (skills + tools), and
 * the agent's inbox: items the founder owes a decision on. Click a card to
 * drill into the items.
 *
 * The drafting workflow has a "Route to" selector on the Drafting page that
 * lets the founder hand a one-off task to a specific agent instead of
 * running the full team — this page is where you see what each one owns.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ChevronRight, Wrench, BookOpen, Send, Inbox as InboxIcon,
  History, CheckCircle2, Loader2, ArrowUpRight,
} from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Button } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Input";
import { AgentBadge } from "@/components/AgentBadge";
import { useAgents, useDraft, type AgentRosterEntry } from "@/lib/api";
import { getAgent } from "@/lib/agents";
import { cn, timeAgo, ICP_LABELS, CHANNELS, channelLabel } from "@/lib/utils";
import { humanizeActionType, humanizeSkillName, humanizeToolName, humanizeStatus } from "@/lib/humanize";

export default function AgentsPage() {
  const { data, isLoading } = useAgents(5);
  const [selected, setSelected] = useState<string | null>(null);

  // Sort: agents with non-empty inboxes first (need attention),
  // then everything else alphabetical-ish. Within the "has inbox" group,
  // sort by inbox count descending.
  const sorted = [...(data?.agents ?? [])].sort((a, b) => {
    const aHas = a.inbox.count > 0;
    const bHas = b.inbox.count > 0;
    if (aHas !== bHas) return aHas ? -1 : 1;
    if (aHas) return b.inbox.count - a.inbox.count;
    return a.agent_id.localeCompare(b.agent_id);
  });

  const selectedAgent = sorted.find((a) => a.agent_id === selected) ?? null;

  return (
    <div className="flex flex-col">
      <PageHeader
        eyebrow="The team"
        title="Agents"
        description="Every agent in the system, what it can do, and what's currently sitting in its inbox waiting on you. Click a card for details — or use the 'Route to' selector on Drafting to hand a task to one agent directly."
      />

      <div className="p-5 sm:p-8 max-w-7xl">
        {isLoading && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-44" />
            ))}
          </div>
        )}

        {!isLoading && (
          <>
            {/* At-a-glance band */}
            <div className="flex items-center gap-4 mb-5 text-sm flex-wrap">
              <span className="text-muted-foreground">At a glance:</span>
              <span className="inline-flex items-center gap-1.5">
                <InboxIcon className="h-4 w-4 text-muted-foreground" />
                <b className="tabular-nums">
                  {sorted.filter((a) => a.inbox.count > 0).length}
                </b>{" "}
                <span className="text-muted-foreground">agents with items waiting</span>
              </span>
              <span className="inline-flex items-center gap-1.5">
                <b className="tabular-nums">
                  {sorted.reduce((acc, a) => acc + a.inbox.count, 0)}
                </b>{" "}
                <span className="text-muted-foreground">total inbox items</span>
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {sorted.map((a) => (
                <AgentCard
                  key={a.agent_id}
                  entry={a}
                  active={selected === a.agent_id}
                  onSelect={() => setSelected(
                    selected === a.agent_id ? null : a.agent_id,
                  )}
                />
              ))}
            </div>

            {selectedAgent && (
              <div className="mt-6">
                <AgentDetail entry={selectedAgent} />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

/**
 * Quick handoff form — sends a one-off task to this agent via the existing
 * /api/draft pipeline. Same backend as the Drafting page, so handoff
 * results show up in the Approval Queue (for drafts) or the agent's
 * inbox (for proposals / sequences / variants). The form gates input
 * fields per agent kind, like the main Drafting form does — Customer
 * Voice gets a paste-area, ops_qa hides channel, etc.
 */
function QuickHandoff({ agent }: { agent: AgentRosterEntry }) {
  const [icp, setIcp] = useState("seg_merchant_dtc");
  const [channel, setChannel] = useState(defaultChannelFor(agent.agent_id));
  const [topic, setTopic] = useState("");
  const draft = useDraft();

  const needsChannel = ![
    "customer_voice_agent", "positioning_agent",
    "ops_qa_agent", "cmo_planner", "self_critique_agent",
  ].includes(agent.agent_id);

  const topicLabel: Record<string, string> = {
    customer_voice_agent:  "Paste raw text",
    cmo_planner:           "Focus area (optional)",
    ops_qa_agent:          "Scope hint (optional)",
    self_critique_agent:   "Specific skill (optional)",
    positioning_agent:     "Positioning angle to propose",
    paid_media_agent:      "Test angle (optional)",
    lifecycle_email_agent: "Sequence theme",
    research_agent:        "Topic for research",
    image_brief_agent:     "Visual theme (optional)",
  };

  const submit = () => {
    draft.mutate({
      icp_segment: icp,
      channel,
      topic_hint: topic,
      agent_id: agent.agent_id,
    });
  };

  // Submitted state — show a confirmation + link to Drafting for the
  // detailed result view (which already renders per-shape output).
  if (draft.isSuccess) {
    return (
      <div className="rounded-md border border-success/30 bg-success/5 p-3 text-sm">
        <div className="flex items-start gap-2">
          <CheckCircle2 className="h-4 w-4 text-success shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="font-medium">Task handed off to {getAgent(agent.agent_id)?.name ?? agent.agent_id}.</p>
            <p className="text-[12px] text-muted-foreground mt-0.5">
              {(() => {
                const r = draft.data as { synthetic?: boolean; shape?: string };
                return r?.synthetic
                  ? `A sample preview was returned. Connect the live service for the agent's real output.`
                  : "Live agent ran — see the result on the Drafting page.";
              })()}
            </p>
            <div className="flex gap-2 mt-2">
              <Link to={`/draft?agent=${encodeURIComponent(agent.agent_id)}`}>
                <Button variant="outline" size="sm">
                  <ArrowUpRight className="h-3 w-3" />
                  Open in Drafting
                </Button>
              </Link>
              <Button variant="ghost" size="sm" onClick={() => draft.reset()}>
                Send another
              </Button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-md border bg-card p-3 space-y-2.5">
      <div className="flex items-center gap-2">
        <Send className="h-3.5 w-3.5 text-primary" />
        <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium">
          Quick handoff
        </p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <Select value={icp} onChange={(e) => setIcp(e.target.value)} className="h-9 text-[13px]">
          {Object.entries(ICP_LABELS).map(([slug, label]) => (
            <option key={slug} value={slug}>{label}</option>
          ))}
        </Select>
        {needsChannel ? (
          <Select value={channel} onChange={(e) => setChannel(e.target.value)} className="h-9 text-[13px]">
            {CHANNELS.map((c) => (
              <option key={c.id} value={c.id}>{c.label}</option>
            ))}
          </Select>
        ) : (
          <span className="text-[11px] text-muted-foreground self-center px-3">
            no channel needed
          </span>
        )}
      </div>
      {agent.agent_id === "customer_voice_agent" ? (
        <textarea
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="Paste sales-call transcript, NPS responses, support tickets…"
          className="w-full rounded-md border bg-background px-3 py-2 text-[13px] h-20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      ) : (
        <Input
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder={topicLabel[agent.agent_id] ?? "Topic"}
          className="h-9 text-[13px]"
        />
      )}
      <div className="flex items-center gap-2">
        <Button onClick={submit} disabled={draft.isPending} size="sm" className="flex-1">
          {draft.isPending ? (
            <><Loader2 className="h-3 w-3 animate-spin" /> Running…</>
          ) : (
            <><Send className="h-3 w-3" /> Send to {getAgent(agent.agent_id)?.shortName ?? "agent"}</>
          )}
        </Button>
        <Link to={`/draft?agent=${encodeURIComponent(agent.agent_id)}`}>
          <Button variant="outline" size="sm" title="Open full Drafting form">
            <ArrowUpRight className="h-3 w-3" />
          </Button>
        </Link>
      </div>
      {draft.isError && (
        <p className="text-[11px] text-destructive">
          {(draft.error as Error)?.message ?? "Handoff failed"}
        </p>
      )}
    </div>
  );
}

// Pick the most natural default channel per agent. Lifecycle Email
// defaults to its own channel; paid agents default to LinkedIn Ads;
// drafting defaults to LinkedIn.
function defaultChannelFor(agentId: string): string {
  if (agentId === "lifecycle_email_agent") return "lifecycle_email";
  if (agentId === "paid_media_agent")      return "linkedin_ads";
  return "linkedin";
}

// ---------------------------------------------------------------------------

function AgentCard({
  entry, active, onSelect,
}: {
  entry: AgentRosterEntry;
  active: boolean;
  onSelect: () => void;
}) {
  const profile = getAgent(entry.agent_id);
  const fallback = !profile;
  const hasInbox = entry.inbox.count > 0;

  return (
    <button
      onClick={onSelect}
      className={cn(
        "text-left rounded-xl border p-4 transition-all relative",
        active
          ? "border-primary bg-primary/5 shadow-sm"
          : "hover:bg-subtle/60 hover:border-foreground/20",
      )}
    >
      <div className="flex items-start gap-3">
        {profile ? (
          <AgentBadge id={profile.id} size="md" />
        ) : (
          <div className="h-10 w-10 rounded-full bg-muted flex items-center justify-center text-xs font-semibold">
            {entry.agent_id.slice(0, 2).toUpperCase()}
          </div>
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="font-medium text-sm leading-tight">
              {profile?.name ?? entry.agent_id}
            </h3>
            {fallback && (
              <Badge variant="muted" className="text-[10px]">internal</Badge>
            )}
          </div>
          <p className="text-[12px] text-muted-foreground mt-0.5">
            {profile?.role ?? "Pipeline agent"}
          </p>
        </div>
        <ChevronRight className={cn(
          "h-4 w-4 shrink-0 mt-1 transition-transform text-muted-foreground/60",
          active && "rotate-90 text-primary",
        )} />
      </div>

      <div className="mt-3 flex items-center gap-3 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center gap-1">
          <BookOpen className="h-3 w-3" />
          <b className="tabular-nums">{entry.skills_allowed.length}</b> skill{entry.skills_allowed.length === 1 ? "" : "s"}
        </span>
        <span className="inline-flex items-center gap-1">
          <Wrench className="h-3 w-3" />
          <b className="tabular-nums">{entry.tools.length}</b> tool{entry.tools.length === 1 ? "" : "s"}
        </span>
      </div>

      <div
        className={cn(
          "mt-3 rounded-md border p-2.5",
          hasInbox
            ? "border-warning/40 bg-warning/5"
            : "border-border bg-muted/30",
        )}
      >
        <div className="flex items-center gap-2 text-[11px] mb-1">
          <InboxIcon className={cn(
            "h-3.5 w-3.5",
            hasInbox ? "text-warning" : "text-muted-foreground/60",
          )} />
          <span className="font-medium">
            {hasInbox
              ? `${entry.inbox.count} item${entry.inbox.count === 1 ? "" : "s"} waiting`
              : "Inbox empty"}
          </span>
        </div>
        <p className="text-[11px] text-muted-foreground leading-snug">
          {entry.inbox.label}
        </p>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------

function AgentDetail({ entry }: { entry: AgentRosterEntry }) {
  const profile = getAgent(entry.agent_id);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            {profile && <AgentBadge id={profile.id} size="lg" />}
            <div>
              <CardTitle>{profile?.name ?? entry.agent_id}</CardTitle>
              <CardDescription>
                {profile?.role ?? "Specialist on the team"}
              </CardDescription>
            </div>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* Quick handoff — fire a one-off task to this agent without
            navigating away. Backed by the same /api/draft endpoint as
            the Drafting page; results land in the queue / agent inbox. */}
        <QuickHandoff agent={entry} />
        {/* Capabilities — skills + tools */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium mb-2 flex items-center gap-1.5">
              <BookOpen className="h-3.5 w-3.5" />
              Knows how to ({entry.skills_allowed.length})
            </p>
            <div className="flex flex-wrap gap-1.5">
              {entry.skills_allowed.length === 0 ? (
                <span className="text-[12px] text-muted-foreground italic">no playbooks assigned</span>
              ) : (
                entry.skills_allowed.map((s) => (
                  <Link key={s} to="/capabilities" className="hover:no-underline">
                    <Badge variant="muted" className="text-[10px]">
                      {humanizeSkillName(s)}
                    </Badge>
                  </Link>
                ))
              )}
            </div>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium mb-2 flex items-center gap-1.5">
              <Wrench className="h-3.5 w-3.5" />
              Has access to ({entry.tools.length})
            </p>
            <div className="flex flex-wrap gap-1.5">
              {entry.tools.length === 0 ? (
                <span className="text-[12px] text-muted-foreground italic">no integrations</span>
              ) : (
                entry.tools.map((t) => (
                  <Badge key={t} variant="outline" className="text-[10px]">
                    {humanizeToolName(t)}
                  </Badge>
                ))
              )}
            </div>
          </div>
        </div>

        {/* Inbox detail */}
        <div>
          <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium mb-2 flex items-center gap-1.5">
            <InboxIcon className="h-3.5 w-3.5" />
            Inbox · {entry.inbox.count} item{entry.inbox.count === 1 ? "" : "s"}
          </p>
          <p className="text-[12px] text-muted-foreground mb-3">
            {entry.inbox.label}
          </p>
          {entry.inbox.count === 0 ? (
            <div className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground italic">
              Nothing waiting. The agent isn't currently holding any work for you.
            </div>
          ) : (
            <div className="space-y-2">
              {entry.inbox.sample.map((item, idx) => (
                <InboxItem key={String(item._id ?? idx)} item={item} />
              ))}
              {entry.inbox.count > entry.inbox.sample.length && (
                <p className="text-[11px] text-muted-foreground text-center pt-1">
                  + {entry.inbox.count - entry.inbox.sample.length} more
                </p>
              )}
            </div>
          )}
        </div>

        {/* Recent activity timeline — every telemetry row this agent
            authored in the last 7 days. Pipeline agents that have no
            standing inbox still show meaningful history here. */}
        {entry.recent_actions.length > 0 && (
          <div>
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium mb-2 flex items-center gap-1.5">
              <History className="h-3.5 w-3.5" />
              Recent activity ({entry.recent_actions.length})
            </p>
            <ol className="relative space-y-3">
              <span className="absolute left-1.5 top-1.5 bottom-1.5 w-px bg-border" aria-hidden />
              {entry.recent_actions.map((a, i) => (
                <li key={`${a.telemetry_id ?? "anon"}-${i}`} className="relative flex items-start gap-3 pl-0.5">
                  <span className="relative z-10 inline-flex items-center justify-center h-3 w-3 rounded-full bg-muted ring-2 ring-card mt-1.5" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-[13px] text-foreground font-medium">
                        {humanizeActionType(a.action_type)}
                      </span>
                      {a.channel && (
                        <Badge variant="muted" className="text-[10px]">
                          {channelLabel(a.channel)}
                        </Badge>
                      )}
                      {a.skill_id && (
                        <span className="text-[11px] text-muted-foreground">
                          using {humanizeSkillName(a.skill_id)}
                        </span>
                      )}
                    </div>
                    {a.ts && (
                      <p className="text-[11px] text-muted-foreground mt-0.5">
                        {timeAgo(a.ts)}
                      </p>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Render one inbox row. The backend's ``_summarize_item`` projects two
 * human-readable strings (``_summary``, ``_detail``) onto each sample item
 * so the UI doesn't have to guess at the title field per collection —
 * positioning_proposals look different from email_sequences from ops_incidents.
 *
 * Falls back to status/timestamp metadata for context.
 */
function InboxItem({ item }: { item: Record<string, unknown> }) {
  const summary = (item._summary as string | undefined) || "(task)";
  const detail  = (item._detail  as string | undefined) || "";

  const status = typeof item.status === "string" ? item.status : null;
  const ts = (item.proposed_at as string | undefined) ||
             (item.created_at as string | undefined) ||
             (item.opened_at as string | undefined) ||
             (item.ts as string | undefined);

  return (
    <div className="rounded-md border bg-card p-3 text-sm flex items-start gap-2">
      <div className="flex-1 min-w-0">
        <p className="font-medium leading-snug">{summary}</p>
        {(detail || status) && (
          <div className="flex flex-wrap items-center gap-2 mt-1 text-[11px] text-muted-foreground">
            {detail && <span>{detail}</span>}
            {status && (
              <span className="bg-subtle/60 px-1.5 py-0.5 rounded text-[10px]">
                {humanizeStatus(status)}
              </span>
            )}
          </div>
        )}
      </div>
      {ts && (
        <span className="text-[11px] text-muted-foreground shrink-0 whitespace-nowrap">
          {timeAgo(ts)}
        </span>
      )}
    </div>
  );
}
