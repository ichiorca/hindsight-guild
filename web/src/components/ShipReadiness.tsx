/**
 * Composite "ship-readiness" indicator — a single judgment on top of six
 * rubric bars. Founders can scan the queue and prioritize at a glance.
 */

import { CheckCircle2, AlertCircle, XCircle, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { pct } from "@/lib/utils";
import type { EvalScores } from "@/lib/types";

export type Verdict = "ship_ready" | "polish_needed" | "needs_work" | "unknown";

// AEO (answer_extractability) is intentionally NOT factored into the verdict
// in v1 — per PRD-01 §12 open question 2, it's advisory until the founder has
// 2 weeks of calibration data. The score still surfaces on the rubric grid +
// the AI-citable chip; it just doesn't gate ship-readiness.
const VERDICT_RUBRICS: ReadonlyArray<keyof EvalScores> = [
  "brand_voice", "claim_support", "claim_risk",
  "icp_relevance", "originality", "conversion_intent",
];

export function computeVerdict(scores: EvalScores | undefined | null): Verdict {
  if (!scores) return "unknown";
  const values = VERDICT_RUBRICS
    .map((k) => scores[k])
    .filter((v): v is number => typeof v === "number");
  if (values.length === 0) return "unknown";
  const min = Math.min(...values);
  if (min >= 0.80) return "ship_ready";
  if (min >= 0.65) return "polish_needed";
  return "needs_work";
}

const VERDICT_META = {
  ship_ready: {
    label: "Ship-ready",
    description: "All rubrics clear the 0.80 bar",
    icon: CheckCircle2,
    className: "bg-success/10 text-success ring-1 ring-success/20",
    dotClass: "bg-success",
  },
  polish_needed: {
    label: "Polish needed",
    description: "At least one rubric between 0.65 and 0.80",
    icon: AlertCircle,
    className: "bg-warning/15 text-foreground ring-1 ring-warning/30",
    dotClass: "bg-warning",
  },
  needs_work: {
    label: "Needs work",
    description: "A rubric scored below 0.65",
    icon: XCircle,
    className: "bg-destructive/10 text-destructive ring-1 ring-destructive/20",
    dotClass: "bg-destructive",
  },
  unknown: {
    label: "Not scored",
    description: "Eval scores not yet available",
    icon: Sparkles,
    className: "bg-muted text-muted-foreground ring-1 ring-border",
    dotClass: "bg-muted-foreground",
  },
} as const;

export function ShipReadiness({ scores, size = "md" }: {
  scores: EvalScores | undefined | null;
  size?: "sm" | "md";
}) {
  const verdict = computeVerdict(scores);
  const meta = VERDICT_META[verdict];
  const Icon = meta.icon;

  const sizeClass = size === "sm"
    ? "px-2 py-0.5 text-[11px] gap-1"
    : "px-3 py-1 text-xs gap-1.5";

  return (
    <span
      className={cn("inline-flex items-center font-medium rounded-full", sizeClass, meta.className)}
      title={meta.description}
    >
      <Icon className={size === "sm" ? "h-3 w-3" : "h-3.5 w-3.5"} />
      {meta.label}
      {scores && verdict !== "unknown" && (
        <span className="font-mono opacity-70">· {pct(Math.min(...Object.values(scores).filter((v): v is number => typeof v === "number")))}</span>
      )}
    </span>
  );
}
