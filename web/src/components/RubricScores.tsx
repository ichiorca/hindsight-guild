import { cn } from "@/lib/utils";
import type { EvalScores } from "@/lib/types";
import { pct } from "@/lib/utils";

const ORDER: Array<keyof EvalScores> = [
  "brand_voice", "claim_support", "claim_risk",
  "icp_relevance", "originality", "conversion_intent",
  "answer_extractability",
];

const LABELS: Record<keyof EvalScores, string> = {
  brand_voice: "Voice",
  claim_support: "Support",
  claim_risk: "Risk",
  icp_relevance: "ICP",
  originality: "Originality",
  conversion_intent: "Intent",
  answer_extractability: "AI-citable",
};

// Returns the theme-token fill class + a NON-color word for the tier, so the
// judgment (good / borderline / low) is conveyed by text too, not color alone.
function tier(s: number): { cls: string; word: string } {
  if (s >= 0.8) return { cls: "bg-success", word: "good" };
  if (s >= 0.65) return { cls: "bg-warning", word: "borderline" };
  return { cls: "bg-destructive", word: "low" };
}

export function RubricScores({ scores, compact = false }: {
  scores: EvalScores;
  compact?: boolean;
}) {
  return (
    <div className={cn(
      "grid gap-2",
      compact ? "grid-cols-3" : "grid-cols-2 sm:grid-cols-4 md:grid-cols-7",
    )}>
      {ORDER.map((k) => {
        const v = scores[k];
        if (v == null) return null;
        const t = tier(v);
        return (
          <div key={k} className="flex flex-col gap-1">
            <div className="flex items-center justify-between text-[11px]">
              <span className="text-muted-foreground">{LABELS[k]}</span>
              <span className="font-mono font-medium">{pct(v)}</span>
            </div>
            <div
              className="h-1.5 w-full rounded-full bg-muted overflow-hidden"
              role="img"
              aria-label={`${LABELS[k]}: ${pct(v)} — ${t.word}`}
              title={`${LABELS[k]}: ${pct(v)} (${t.word})`}
            >
              <div
                className={cn("h-full rounded-full transition-all", t.cls)}
                style={{ width: `${Math.max(2, v * 100)}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
