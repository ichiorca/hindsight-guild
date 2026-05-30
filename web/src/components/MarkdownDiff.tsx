/**
 * MarkdownDiff — minimal unified-diff renderer.
 *
 * The Self-Critique Agent emits proposed_diff as a unified-diff-ish patch
 * against the current SKILL.md body:
 *
 *   @@ Section heading @@
 *    unchanged context line
 *   - removed line
 *   + added line
 *
 * We don't need a full git-diff implementation here — just enough to make
 * additive proposals readable at a glance. Line-level coloring, monospace,
 * tight enough to fit in a weekly-review decision card.
 *
 * If we ever ship side-by-side or word-level diffs, swap this for
 * react-diff-viewer-continued and add the dep. For the smallest-shippable
 * version this is enough.
 */
import { cn } from "@/lib/utils";

interface MarkdownDiffProps {
  diff: string;
  className?: string;
}

type LineKind = "context" | "add" | "remove" | "hunk" | "meta";

function classify(line: string): LineKind {
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "remove";
  return "context";
}

const ROW_CLASS: Record<LineKind, string> = {
  add: "bg-success/10 text-success border-l-2 border-success",
  remove: "bg-destructive/10 text-destructive border-l-2 border-destructive line-through",
  hunk: "bg-subtle/60 text-muted-foreground italic",
  meta: "text-muted-foreground/60",
  context: "text-foreground/80",
};

export function MarkdownDiff({ diff, className }: MarkdownDiffProps) {
  if (!diff || !diff.trim()) {
    return (
      <p className={cn("text-xs text-muted-foreground italic", className)}>
        (no diff supplied)
      </p>
    );
  }

  const lines = diff.split("\n");
  // Count additions/removals for a one-glance header summary.
  let adds = 0;
  let removes = 0;
  for (const l of lines) {
    const k = classify(l);
    if (k === "add") adds += 1;
    else if (k === "remove") removes += 1;
  }

  return (
    <div className={cn("rounded-md border bg-card overflow-hidden", className)}>
      <div className="flex items-center justify-between gap-2 px-3 py-1.5 border-b bg-subtle/30 text-[11px] uppercase tracking-wider text-muted-foreground">
        <span>Proposed diff</span>
        <span className="font-mono normal-case tracking-normal">
          <span className="text-success">+{adds}</span>{" "}
          <span className="text-destructive">−{removes}</span>
        </span>
      </div>
      <pre className="text-xs font-mono leading-relaxed overflow-x-auto m-0 py-1">
        {lines.map((line, i) => {
          const kind = classify(line);
          return (
            <div
              key={i}
              className={cn(
                "px-3 py-0.5 whitespace-pre",
                ROW_CLASS[kind],
              )}
            >
              {line || " "}
            </div>
          );
        })}
      </pre>
    </div>
  );
}
