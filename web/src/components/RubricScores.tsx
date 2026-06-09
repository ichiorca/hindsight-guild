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
function tier(s: number): { cls: string; word: string; text: string } {
  if (s >= 0.8) return { cls: "bg-success", word: "good", text: "text-success" };
  if (s >= 0.65) return { cls: "bg-warning", word: "borderline", text: "text-warning" };
  return { cls: "bg-destructive", word: "low", text: "text-destructive" };
}

export function RubricScores({ scores, explanations, compact = false }: {
  scores: EvalScores;
  // Per-rubric judge rationale ({rubric_key: text}). When present, a
  // collapsible "Reviewer feedback" panel renders below the bars so the
  // founder can read WHY a rubric scored low — not just the number.
  explanations?: Record<string, string> | null;
  compact?: boolean;
}) {
  // Rubrics that have a written rationale, worst score first (most actionable).
  const feedback = explanations
    ? ORDER
        .filter((k) => explanations[k as string] && scores[k] != null)
        .sort((a, b) => (scores[a] ?? 1) - (scores[b] ?? 1))
        .map((k) => ({ key: k, label: LABELS[k], text: explanations[k as string], score: scores[k]! }))
    : [];

  return (
    <div className="space-y-2">
      <div className={cn(
        "grid gap-2",
        compact ? "grid-cols-3" : "grid-cols-2 sm:grid-cols-4 md:grid-cols-7",
      )}>
        {ORDER.map((k) => {
          const v = scores[k];
          if (v == null) return null;
          const t = tier(v);
          const why = explanations?.[k as string];
          return (
            <div key={k} className="flex flex-col gap-1">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-muted-foreground">{LABELS[k]}</span>
                <span className="font-mono font-medium">{pct(v)}</span>
              </div>
              <div
                className="h-1.5 w-full rounded-full bg-muted overflow-hidden"
                role="img"
                aria-label={`${LABELS[k]}: ${pct(v)} — ${t.word}${why ? `. ${why}` : ""}`}
                title={why ? `${LABELS[k]}: ${pct(v)} (${t.word})\n\n${why}` : `${LABELS[k]}: ${pct(v)} (${t.word})`}
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

      {feedback.length > 0 && (
        <details className="group rounded-md border border-border/60 bg-muted/30 px-2.5 py-1.5 text-xs">
          <summary className="cursor-pointer select-none font-medium text-muted-foreground hover:text-foreground">
            Reviewer feedback ({feedback.length})
          </summary>
          <ul className="mt-2 space-y-2">
            {feedback.map((f) => {
              const t = tier(f.score);
              return (
                <li key={f.key} className="flex gap-2">
                  <span className={cn("shrink-0 font-mono font-medium tabular-nums", t.text)}>
                    {f.label} {pct(f.score)}
                  </span>
                  <span className="text-muted-foreground leading-snug">{f.text}</span>
                </li>
              );
            })}
          </ul>
        </details>
      )}
    </div>
  );
}
