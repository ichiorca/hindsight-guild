/**
 * EvidenceDrawer — formatted, per-miner evidence panel for self-critique
 * proposals.
 *
 * Used by:
 *   - WeeklyReview's ProposalCard
 *   - Skills page's "Proposed by self-critique" tab
 *
 * The panel is a <details> element so the founder can scan a queue of
 * proposals quickly and only expand the evidence they want to verify.
 * Each miner emits a different evidence shape (see agents/_miners/*.py);
 * this component knows the contract for all five and falls back to a
 * JSON dump for anything new.
 */
import { useState } from "react";
import { ChevronRight, ChevronDown, Quote } from "lucide-react";
import { cn } from "@/lib/utils";

type Evidence = Record<string, unknown>;

export interface EvidenceDrawerProps {
  miner: string | null;
  evidence: Evidence;
  evidenceCount?: number | null;
  className?: string;
}

export function EvidenceDrawer({
  miner, evidence, evidenceCount, className,
}: EvidenceDrawerProps) {
  const [open, setOpen] = useState(false);
  const empty = !evidence || Object.keys(evidence).length === 0;

  return (
    <div className={cn("border-t mt-3 pt-2", className)}>
      <button
        type="button"
        className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
        onClick={() => setOpen((o) => !o)}
        disabled={empty}
        aria-expanded={open}
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        {empty
          ? "No evidence captured"
          : `Evidence drilldown${typeof evidenceCount === "number" ? ` · ${evidenceCount} ${evidenceCount === 1 ? "row" : "rows"}` : ""}`}
      </button>
      {open && !empty && (
        <div className="mt-2 ml-4 rounded-md border bg-subtle/40 p-3 text-[12px] space-y-1.5">
          {renderEvidence(miner, evidence)}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-miner rendering
// ---------------------------------------------------------------------------

function renderEvidence(miner: string | null, ev: Evidence) {
  const m = (miner ?? "").toLowerCase();

  if (m === "voice")    return <VoiceEvidence ev={ev} />;
  if (m === "negative") return <NegativeEvidence ev={ev} />;
  if (m === "paid")     return <PaidEvidence ev={ev} />;
  if (m === "aeo")      return <AeoEvidence ev={ev} />;
  if (m === "signal")   return <SignalEvidence ev={ev} />;

  // Unknown miner — JSON dump as last resort so we never hide data.
  return <JsonFallback ev={ev} />;
}

// ---------------------------------------------------------------------------
// Miner-specific layouts
// ---------------------------------------------------------------------------

function VoiceEvidence({ ev }: { ev: Evidence }) {
  return (
    <>
      <Row label="N-gram"  value={<Quoted text={asString(ev.ngram)} />} />
      <Row label="Pattern" value={<KindBadge text={asString(ev.pattern_kind)} />} />
      <Row label="Frequency" value={asNumber(ev.frequency)} />
      <Row label="Distinct drafts" value={asNumber(ev.distinct_drafts)} />
    </>
  );
}

function NegativeEvidence({ ev }: { ev: Evidence }) {
  const channels = Array.isArray(ev.channels) ? ev.channels as string[] : [];
  return (
    <>
      <Row label="Category" value={<KindBadge text={asString(ev.category)} />} />
      <Row label="Phrase"   value={<Quoted text={asString(ev.phrase)} />} />
      <Row label="Distinct drafts" value={asNumber(ev.distinct_drafts)} />
      <Row label="Rejections in window" value={asNumber(ev.rejects_in_window)} />
      {channels.length > 0 && (
        <Row label="Channels" value={channels.join(", ")} />
      )}
    </>
  );
}

function PaidEvidence({ ev }: { ev: Evidence }) {
  const reasons = Array.isArray(ev.reasons) ? ev.reasons as string[] : [];
  return (
    <>
      <Row label="Platform" value={<KindBadge text={asString(ev.platform)} />} />
      <Row label="Variant"  value={asString(ev.variant_name) || asString(ev.external_id)} />
      {ev.action_kind === "pause" && (
        <>
          <Row label="Spend (24h)"       value={`$${asNumber(ev.spend_24h)?.toFixed(2) ?? "—"}`} />
          <Row label="Conversions (24h)" value={asNumber(ev.conversions_24h)} />
          <Row label="CTR (%)"           value={ev.ctr_pct != null ? `${Number(ev.ctr_pct).toFixed(2)}%` : "—"} />
          <Row label="Hours running"     value={asNumber(ev.hours_running)} />
        </>
      )}
      {ev.action_kind === "reallocate_budget" && (
        <>
          <Row label="From variant"  value={asString(ev.source_variant_name)} />
          <Row label="To variant"    value={asString(ev.winner_variant_name)} />
          <Row label="Experiment"    value={asString(ev.experiment_id)} />
          <Row label="Winner conv. (24h)" value={asNumber(ev.winner_conversions_24h)} />
        </>
      )}
      {reasons.length > 0 && (
        <div className="pt-1">
          <p className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">Reasons</p>
          <ul className="list-disc pl-4 space-y-0.5">
            {reasons.map((r, i) => (
              <li key={i} className="text-[11px] text-muted-foreground italic">"{r}"</li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

function AeoEvidence({ ev }: { ev: Evidence }) {
  return (
    <>
      <Row label="Rewrite kind"     value={<KindBadge text={asString(ev.rewrite_kind)} />} />
      <Row label="Occurrence count" value={asNumber(ev.occurrence_count)} />
      <Row
        label="Mean score lift"
        value={typeof ev.mean_score_lift === "number"
          ? (ev.mean_score_lift >= 0 ? "+" : "") + ev.mean_score_lift.toFixed(3)
          : "—"}
      />
      <Row label="Window (days)" value={asNumber(ev.audits_window_days)} />
    </>
  );
}

function SignalEvidence({ ev }: { ev: Evidence }) {
  return (
    <>
      <Row label="Source"      value={<KindBadge text={asString(ev.source_name)} />} />
      <Row label="Approves"    value={asNumber(ev.approves)} />
      <Row label="Rejects"     value={asNumber(ev.rejects)} />
      <Row label="Approve rate" value={
        typeof ev.approve_rate === "number"
          ? `${(ev.approve_rate * 100).toFixed(1)}%`
          : "—"
      } />
      {ev.current_score_floor != null && ev.proposed_score_floor != null && (
        <Row
          label="Score floor"
          value={`${asNumber(ev.current_score_floor)} → ${asNumber(ev.proposed_score_floor)}`}
        />
      )}
      {ev.proposed_enabled === false && (
        <Row label="Action" value={<KindBadge text="disable source" />} />
      )}
    </>
  );
}

function JsonFallback({ ev }: { ev: Evidence }) {
  return (
    <pre className="font-mono text-[11px] whitespace-pre-wrap break-all">
      {JSON.stringify(ev, null, 2)}
    </pre>
  );
}

// ---------------------------------------------------------------------------
// Tiny helpers
// ---------------------------------------------------------------------------

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  if (value === undefined || value === null || value === "" || value === "—") {
    return null;
  }
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-[11px] uppercase tracking-wider text-muted-foreground w-32 shrink-0">
        {label}
      </span>
      <span className="text-[12px]">{value}</span>
    </div>
  );
}

function Quoted({ text }: { text: string | null }) {
  if (!text) return <>—</>;
  return (
    <span className="inline-flex items-center gap-1 font-mono text-muted-foreground italic">
      <Quote className="h-3 w-3 shrink-0" /> {text}
    </span>
  );
}

function KindBadge({ text }: { text: string | null }) {
  if (!text) return <>—</>;
  return (
    <span className="inline-block px-1.5 py-0.5 rounded bg-muted text-[11px] font-mono">
      {text}
    </span>
  );
}

function asString(v: unknown): string | null {
  return typeof v === "string" ? v : v == null ? null : String(v);
}

function asNumber(v: unknown): number | null {
  return typeof v === "number" ? v : null;
}
