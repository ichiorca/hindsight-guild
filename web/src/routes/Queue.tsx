import { useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2, Pencil, X, Inbox, Linkedin, Mail, FileText,
  Clock, AlertCircle, FlaskConical, Search as SearchIcon,
  Send, MousePointerClick, Megaphone,
} from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Textarea, Select } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { Empty } from "@/components/ui/Empty";
import { ErrorState } from "@/components/ui/ErrorState";
import { RubricScores } from "@/components/RubricScores";
import { ChannelPreview } from "@/components/ChannelPreview";
import { ShipReadiness, computeVerdict } from "@/components/ShipReadiness";
import { AgentBadge } from "@/components/AgentBadge";
import { Kbd } from "@/components/Kbd";
import { ShortcutsHint } from "@/components/ShortcutsHint";
import { DiffView } from "@/components/DiffView";
import { SourceAnnotated } from "@/components/SourceAnnotated";
import { PublishStateBadge } from "@/components/PublishStateBadge";
import {
  useQueue, useSubmitDecision, useIntegrationsStatus, integrationForChannel,
  usePublished,
} from "@/lib/api";
import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts";
import { toast } from "@/lib/toast";
import { timeAgo, cn, icpLabel, channelLabel, CHANNELS } from "@/lib/utils";
import { humanizeReviewIssue } from "@/lib/humanize";
import type { QueueItem } from "@/lib/types";

const REJECTION_REASONS = [
  { value: "Absolute claim — unsupportable", category: "claim_risk" },
  { value: "Tone off-brand", category: "tone" },
  { value: "Echoes competitor copy", category: "originality" },
  { value: "Wrong ICP fit", category: "icp_relevance" },
  { value: "Hard sell / multi-CTA", category: "conversion_intent" },
];

// PRD-02 source-label helper. Centralized so /signals + Queue chip agree.
function signalSourceLabel(source: string | null | undefined): string {
  switch ((source || "").toLowerCase()) {
    case "hn":     return "HN";
    case "reddit": return "Reddit";
    case "rss":    return "RSS";
    case "manual": return "manual";
    default:       return source || "signal";
  }
}

const CHANNEL_ICONS: Record<string, typeof Linkedin> = {
  linkedin: Linkedin,
  email: Mail,
  blog: FileText,
  substack: FileText,
  lifecycle_email: Send,             // multi-step nurture
  google_ads: MousePointerClick,     // RSA / paid search
  meta_ads: Megaphone,               // Meta paid social
  linkedin_ads: Megaphone,           // LinkedIn paid
};

export default function QueuePage() {
  const [channel, setChannel] = useState<string>("");
  const [focusedIdx, setFocusedIdx] = useState(0);
  // Has the founder actually engaged with the list (clicked a row or used
  // J/K)? Until they have, we do NOT arm the A/E/R per-row shortcuts — so a
  // stray keypress on a fresh page can't publish row 0.
  const [interacted, setInteracted] = useState(false);
  const { data: items, isLoading, isError, refetch } = useQueue(channel || undefined);

  // Sort: needs_work first (most urgent), then polish_needed, then ship_ready
  const sorted = useMemo(() => {
    if (!items) return [];
    const order: Record<string, number> = {
      needs_work: 0, polish_needed: 1, ship_ready: 2, unknown: 3,
    };
    return [...items].sort(
      (a, b) => order[computeVerdict(a.eval_scores)] - order[computeVerdict(b.eval_scores)],
    );
  }, [items]);

  const counts = useMemo(() => {
    const c = { ship_ready: 0, polish_needed: 0, needs_work: 0 };
    for (const item of sorted) {
      const v = computeVerdict(item.eval_scores);
      if (v in c) c[v as keyof typeof c]++;
    }
    return c;
  }, [sorted]);

  // Keyboard nav across rows. Any nav keypress counts as engagement, which
  // arms the per-row A/E/R shortcuts.
  const nav = (fn: (i: number) => number) => {
    setInteracted(true);
    setFocusedIdx(fn);
  };
  useKeyboardShortcuts({
    j: () => nav((i) => Math.min(i + 1, Math.max(0, sorted.length - 1))),
    arrowdown: () => nav((i) => Math.min(i + 1, Math.max(0, sorted.length - 1))),
    k: () => nav((i) => Math.max(i - 1, 0)),
    arrowup: () => nav((i) => Math.max(i - 1, 0)),
  });

  return (
    <div className="flex flex-col">
      <PageHeader
        eyebrow="Daily driver"
        title="Approval Queue"
        description={
          sorted.length > 0
            ? `${sorted.length} draft${sorted.length === 1 ? "" : "s"} waiting on your call. Use J/K to move, A to approve, E to edit, R to reject.`
            : "No decisions to make right now. The team is between sprints — try drafting something."
        }
      >
        <ShortcutsHint shortcuts={[
          { keys: ["A"], label: "Approve" },
          { keys: ["E"], label: "Edit" },
          { keys: ["R"], label: "Reject" },
          { keys: ["J"], label: "Next draft" },
          { keys: ["K"], label: "Previous draft" },
        ]} />
        <Select
          value={channel}
          onChange={(e) => setChannel(e.target.value)}
          className="w-48"
        >
          <option value="">All channels</option>
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
      </PageHeader>

      {/* At-a-glance summary band */}
      {sorted.length > 0 && (
        <div className="px-5 sm:px-8 pt-5 -mb-2">
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">At a glance:</span>
            {counts.ship_ready > 0 && (
              <span className="inline-flex items-center gap-1.5 text-success font-medium">
                <span className="w-1.5 h-1.5 rounded-full bg-success" />
                {counts.ship_ready} ship-ready
              </span>
            )}
            {counts.polish_needed > 0 && (
              <span className="inline-flex items-center gap-1.5 text-foreground font-medium">
                <span className="w-1.5 h-1.5 rounded-full bg-warning" />
                {counts.polish_needed} polish
              </span>
            )}
            {counts.needs_work > 0 && (
              <span className="inline-flex items-center gap-1.5 text-destructive font-medium">
                <span className="w-1.5 h-1.5 rounded-full bg-destructive" />
                {counts.needs_work} need work
              </span>
            )}
            <span className="ml-auto text-xs text-muted-foreground">
              Sorted: most-urgent first
            </span>
          </div>
        </div>
      )}

      <div className="p-5 sm:p-8 space-y-4 max-w-5xl">
        {isLoading && (
          <>
            <Skeleton className="h-72 rounded-xl" />
            <Skeleton className="h-72 rounded-xl" />
          </>
        )}
        {!isLoading && isError && (
          <ErrorState what="the approval queue" onRetry={() => refetch()} />
        )}
        {!isLoading && !isError && sorted.length === 0 && (
          <Empty
            icon={Inbox}
            title="Inbox zero."
            description="Your agents are between sprints. Run a draft from the Drafting workspace, or check back after the next Monday slate."
          />
        )}
        {!isError && sorted.map((item, idx) => (
          <QueueRow
            key={item.telemetry_id}
            item={item}
            focused={idx === focusedIdx}
            armed={idx === focusedIdx && interacted}
            onFocus={() => { setInteracted(true); setFocusedIdx(idx); }}
          />
        ))}
      </div>
    </div>
  );
}

function QueueRow({ item, focused, armed, onFocus }: {
  item: QueueItem;
  focused: boolean;
  /** Per-row keyboard actions only fire when armed (focused AND the founder
   * has engaged the list). Prevents publish-on-load via a stray keypress. */
  armed: boolean;
  onFocus: () => void;
}) {
  const [mode, setMode] = useState<"view" | "edit" | "reject" | "confirm_ship">("view");
  const [editedText, setEditedText] = useState(item.draft_text);
  const [reason, setReason] = useState(REJECTION_REASONS[0].value);
  const [justActed, setJustActed] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const submit = useSubmitDecision();
  const integrations = useIntegrationsStatus();
  // Once a draft has a publish state, look up where it actually landed
  // (attribution_map) — covers the case where the approval row didn't
  // capture the external URL but the publisher recorded it.
  const published = usePublished(item.publish_state ? item.telemetry_id : undefined);
  const ChannelIcon = item.channel ? CHANNEL_ICONS[item.channel] ?? FileText : FileText;

  // Look up which integration adapter (if any) will fire on Ship-it
  // for this row's channel. Each channel routes to at most one
  // adapter, configured via CHANNEL_ROUTES on the backend. When no
  // matching adapter is configured the Ship-it button stays as plain
  // "Ship it" — no surprise external posting.
  const route = integrationForChannel(integrations.data, item.channel);
  const shipVerb = route
    ? route.platform === "Google Ads" || route.platform === "Meta Ads"
      ? `Ship + create paused ${route.platform} variant`
      : `Ship + publish to ${route.platform}`
    : "Ship it";
  const willPublishHint = route
    ? route.platform === "Google Ads" || route.platform === "Meta Ads"
      ? `will create a paused ${route.platform} draft`
      : `will post to ${route.platform}`
    : null;

  useEffect(() => {
    if (focused) {
      ref.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [focused]);

  // Toast on success — survives the row's disappearance on refetch, and
  // carries the published URL (which used to live on the now-vanishing row).
  const shipSuccess = (data: unknown) => {
    const pub = (data as { publish?: { url?: string; platform?: string } } | undefined)?.publish;
    if (pub?.url) {
      toast.success(`Shipped to ${pub.platform ?? "the channel"}`, {
        description: "Your post is live.",
        action: { label: "View post →", onClick: () => window.open(pub.url, "_blank", "noopener") },
      });
    } else if (route) {
      toast.success(`Shipped to ${route.platform}`, { description: "Publish requested." });
    } else {
      toast.success("Approved", { description: "Saved to your approvals." });
    }
  };

  const approve = () => {
    setJustActed(true);
    submit.mutate({
      telemetry_id: item.telemetry_id,
      decision: "approve",
      original_draft: item.draft_text,
      approved_text: item.draft_text,
      channel: item.channel,
    }, { onSuccess: shipSuccess });
  };

  // Ship entrypoint: external publishes get a confirm step; internal
  // approvals ship in one tap. This inverts the old friction where Reject
  // (reversible) was confirmed but public Ship (irreversible) was one click.
  const ship = () => {
    if (route) setMode("confirm_ship");
    else approve();
  };

  const saveEdit = () => {
    setJustActed(true);
    submit.mutate(
      {
        telemetry_id: item.telemetry_id,
        decision: "edit",
        original_draft: item.draft_text,
        approved_text: editedText,
        channel: item.channel,
      },
      { onSuccess: (data) => { setMode("view"); shipSuccess(data); } },
    );
  };

  const reject = () => {
    setJustActed(true);
    submit.mutate(
      {
        telemetry_id: item.telemetry_id,
        decision: "reject",
        original_draft: item.draft_text,
        rejection_reason: reason,
        channel: item.channel,
      },
      {
        onSuccess: () => {
          setMode("view");
          toast.success("Rejected", {
            description: "Future drafts will avoid this pattern.",
          });
        },
      },
    );
  };

  // Per-row keyboard — only when armed (focused + founder engaged the list).
  useKeyboardShortcuts(
    {
      a: () => mode === "view" && ship(),
      e: () => mode === "view" && setMode("edit"),
      r: () => mode === "view" && setMode("reject"),
      escape: () => setMode("view"),
    },
    armed,
  );

  const editDiffLength = mode === "edit"
    ? Math.abs(editedText.length - item.draft_text.length)
    : 0;

  return (
    <Card
      ref={ref}
      onClick={onFocus}
      className={cn(
        "card-decision animate-fade-in overflow-hidden transition-all",
        focused
          ? "ring-2 ring-primary/40 shadow-lg"
          : "hover:shadow-md cursor-pointer",
        justActed && "animate-pop-success",
      )}
    >
      {/* Top row: agent identity + trigger context + readiness verdict */}
      <div className="border-b bg-subtle/50 px-6 py-3 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3 min-w-0">
          <AgentBadge id={item.agent} size="sm" showName={false} />
          <ChannelIcon className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm font-medium">{channelLabel(item.channel) || "draft"}</span>
          {item.icp_segment && (
            <Badge variant="muted">{icpLabel(item.icp_segment)}</Badge>
          )}
          {item.experiment_id && (
            <Badge variant="default" className="inline-flex items-center gap-1"
                   title={`Experiment: ${item.experiment_id}`}>
              <FlaskConical className="h-3 w-3" /> Part of an experiment
            </Badge>
          )}
          {/* PRD-02 — signal-triggered drafts carry a back-ref chip. */}
          {item.triggered_by_signal_id && item.triggered_by_evidence_url && (
            <a
              href={item.triggered_by_evidence_url}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              title={`Triggered by a ${signalSourceLabel(item.triggered_by_source)} thread`}
              className="inline-flex items-center gap-1 text-[11px] text-info hover:underline px-2 py-0.5 rounded-full ring-1 ring-info/30 bg-info/10"
            >
              <SearchIcon className="h-3 w-3" />
              Triggered by {signalSourceLabel(item.triggered_by_source)}
              {item.triggered_by_ts && (
                <span className="opacity-70">· {timeAgo(item.triggered_by_ts)}</span>
              )}
            </a>
          )}
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <PublishStateBadge
            state={item.publish_state}
            url={item.publish_url}
            mode={item.publish_mode}
          />
          {published.data?.external_url && !item.publish_url && (
            <a
              href={published.data.external_url}
              target="_blank"
              rel="noreferrer"
              className="text-[11px] text-primary hover:underline"
            >
              View published →
            </a>
          )}
          <ShipReadiness scores={item.eval_scores} size="sm" />
          <span className="text-[11px] text-muted-foreground inline-flex items-center gap-1">
            <Clock className="h-3 w-3" /> {timeAgo(item.ts)}
          </span>
        </div>
      </div>

      <CardContent className="pt-5 space-y-4">
        {/* Channel-native preview replaces the flat text block */}
        {mode !== "edit" && (
          <>
            <ChannelPreview channel={item.channel} text={item.draft_text} subject={item.subject ?? undefined} image={item.image} images={item.images} />
            {item.customer_voice_used?.length > 0 && (
              <div className="rounded-lg border bg-card p-4">
                <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium mb-2">
                  Source attribution
                </p>
                <SourceAnnotated
                  text={item.draft_text}
                  sources={item.customer_voice_used}
                />
              </div>
            )}
          </>
        )}
        {mode === "edit" && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium">
                Editing — your changes train the system
              </p>
              {editDiffLength > 0 && (
                <span className="text-[11px] font-mono text-muted-foreground">
                  Δ {editDiffLength} chars
                </span>
              )}
            </div>
            <Textarea
              value={editedText}
              onChange={(e) => setEditedText(e.target.value)}
              className="min-h-[220px] font-sans text-[14px] leading-relaxed"
              autoFocus
            />
            {/* Live diff preview */}
            <DiffView before={item.draft_text} after={editedText} />
          </div>
        )}

        {Object.keys(item.eval_scores ?? {}).length > 0 && (
          <div className="rounded-lg border bg-card p-4">
            <div className="flex items-center justify-between mb-3">
              <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium">
                Vertex AI Eval · all 6 rubrics
              </p>
              <SearchIcon className="h-3 w-3 text-muted-foreground" />
            </div>
            <RubricScores scores={item.eval_scores} />
          </div>
        )}

        {item.review_flags?.length > 0 && (
          <div className="rounded-lg border-l-2 border-warning bg-warning/5 px-4 py-3">
            <div className="flex items-center gap-2 mb-2">
              <AlertCircle className="h-3.5 w-3.5 text-warning" />
              <span className="text-[11px] font-medium uppercase tracking-wider">
                Review flagged
              </span>
            </div>
            <ul className="space-y-1">
              {item.review_flags.map((f, i) => (
                <li key={i} className="text-sm flex gap-2">
                  <Badge variant="warning" className="shrink-0">{humanizeReviewIssue(f.issue)}</Badge>
                  <span className="text-muted-foreground italic">"{f.phrase}"</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {item.customer_voice_used?.length > 0 && (
          <details className="text-xs group">
            <summary className="cursor-pointer text-muted-foreground hover:text-foreground transition-colors inline-flex items-center gap-1.5">
              <span className="font-medium">{item.customer_voice_used.length} customer voice quote{item.customer_voice_used.length === 1 ? "" : "s"} used</span>
              <span className="opacity-60">— sourced from your sales calls + NPS</span>
            </summary>
            <ul className="mt-2 space-y-1.5 pl-4 border-l-2 border-muted">
              {item.customer_voice_used.map((q, i) => (
                <li key={i} className="text-muted-foreground italic">"{q}"</li>
              ))}
            </ul>
          </details>
        )}
      </CardContent>

      <div className={cn(
        "border-t px-6 py-3 flex items-center gap-2 flex-wrap",
        mode === "reject" && "bg-destructive/5",
        mode === "edit" && "bg-warning/5",
      )}>
        {mode === "view" && (
          <>
            <Button variant="success" size="sm" onClick={ship} disabled={submit.isPending}>
              <CheckCircle2 className="h-4 w-4" />
              {shipVerb}
              {armed && <Kbd keys={["A"]} className="ml-1 opacity-70" />}
            </Button>
            {willPublishHint && (
              <span className="text-[11px] text-muted-foreground">
                {willPublishHint}
              </span>
            )}
            <Button variant="outline" size="sm" onClick={() => setMode("edit")}>
              <Pencil className="h-4 w-4" /> Edit
              {armed && <Kbd keys={["E"]} className="ml-1 opacity-70" />}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setMode("reject")}>
              <X className="h-4 w-4" /> Reject
              {armed && <Kbd keys={["R"]} className="ml-1 opacity-70" />}
            </Button>
            <span className="ml-auto text-[11px] text-muted-foreground hidden md:inline">
              Every decision feeds the learning loop.
            </span>
          </>
        )}
        {mode === "confirm_ship" && (
          <>
            <span className="text-sm text-foreground">
              {route?.platform === "Google Ads" || route?.platform === "Meta Ads"
                ? `Create a paused ${route?.platform} variant?`
                : `Publish to ${route?.platform} now? This posts publicly.`}
            </span>
            <Button variant="success" size="sm" onClick={approve} disabled={submit.isPending}>
              <CheckCircle2 className="h-4 w-4" /> Confirm
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setMode("view")}>
              Cancel
            </Button>
          </>
        )}
        {mode === "edit" && (
          <>
            <Button variant="success" size="sm" onClick={saveEdit} disabled={submit.isPending}>
              <CheckCircle2 className="h-4 w-4" /> Save & ship
            </Button>
            <Button variant="ghost" size="sm" onClick={() => { setMode("view"); setEditedText(item.draft_text); }}>
              Cancel
            </Button>
            <span className="ml-auto text-[11px] text-muted-foreground">
              The diff is the system's single most valuable signal.
            </span>
          </>
        )}
        {mode === "reject" && (
          <>
            <Select value={reason} onChange={(e) => setReason(e.target.value)} className="max-w-xs">
              {REJECTION_REASONS.map((r) => (
                <option key={r.value} value={r.value}>{r.value}</option>
              ))}
            </Select>
            <Button variant="destructive" size="sm" onClick={reject} disabled={submit.isPending}>
              Confirm reject
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setMode("view")}>
              Cancel
            </Button>
            <span className="ml-auto text-[11px] text-muted-foreground">
              Future drafts will avoid this pattern.
            </span>
          </>
        )}
      </div>
    </Card>
  );
}
