import { useMemo } from "react";
import { cn } from "@/lib/utils";
import { diffWords, summarizeDiff } from "@/lib/diff";

interface DiffViewProps {
  before: string;
  after: string;
  className?: string;
}

/**
 * Inline word-level diff. Red strikethrough for removed, green underline for
 * added, normal text for unchanged. Mirrors the markup conventions in
 * Substack and most modern editors.
 */
export function DiffView({ before, after, className }: DiffViewProps) {
  const ops = useMemo(() => diffWords(before, after), [before, after]);
  const summary = useMemo(() => summarizeDiff(ops), [ops]);

  if (!summary.changed) {
    return (
      <div className={cn("rounded-md border bg-subtle/50 px-4 py-6 text-center text-sm text-muted-foreground", className)}>
        No changes yet.
      </div>
    );
  }

  return (
    <div className={cn("rounded-lg border bg-card", className)}>
      <div className="flex items-center justify-between px-4 py-2 border-b bg-subtle/40 text-[11px]">
        <span className="uppercase tracking-wider font-medium text-muted-foreground">
          Your edit · captured to the learning loop
        </span>
        <span className="font-mono">
          <span className="text-success">+{summary.added}</span>
          <span className="text-muted-foreground mx-1">·</span>
          <span className="text-destructive">−{summary.removed}</span>
        </span>
      </div>
      <p className="text-[14px] leading-[1.55] p-4 whitespace-pre-wrap font-sans">
        {ops.map((op, i) => {
          if (op.type === "equal") return <span key={i}>{op.text}</span>;
          if (op.type === "add")
            return (
              <ins key={i} className="bg-success/15 text-success no-underline rounded-sm px-0.5">
                {op.text}
              </ins>
            );
          return (
            <del key={i} className="bg-destructive/10 text-destructive line-through decoration-destructive/60 rounded-sm px-0.5">
              {op.text}
            </del>
          );
        })}
      </p>
    </div>
  );
}
