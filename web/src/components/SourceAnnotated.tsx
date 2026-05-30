import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";

interface SourceAnnotatedProps {
  text: string;
  sources: string[];
  className?: string;
}

/**
 * Annotate a draft with inline highlights showing which phrases were sourced
 * from customer voice quotes. The match algorithm finds the longest n-gram
 * (n >= 4 words) overlap between the draft and each quote, so we highlight
 * meaningful spans not random words.
 *
 * Hovering a highlight surfaces the source quote. Click-to-pin handles
 * touch devices.
 */
export function SourceAnnotated({ text, sources, className }: SourceAnnotatedProps) {
  const [pinned, setPinned] = useState<number | null>(null);

  const annotations = useMemo(() => buildAnnotations(text, sources), [text, sources]);
  const segments = useMemo(() => sliceText(text, annotations), [text, annotations]);

  if (!annotations.length) {
    // No matches — just render the plain text so the layout is consistent.
    return (
      <p className={cn("text-[14px] leading-[1.55] whitespace-pre-wrap font-sans", className)}>
        {text}
      </p>
    );
  }

  return (
    <div className={cn("relative", className)}>
      <p className="text-[14px] leading-[1.55] whitespace-pre-wrap font-sans">
        {segments.map((seg, i) => {
          if (seg.annotationIdx == null) return <span key={i}>{seg.text}</span>;
          const ann = annotations[seg.annotationIdx];
          const isActive = pinned === seg.annotationIdx;
          return (
            <span key={i} className="relative group">
              <mark
                role="button"
                tabIndex={0}
                aria-expanded={isActive}
                aria-label={`Sourced from customer voice — ${ann.source}`}
                onClick={() => setPinned(isActive ? null : seg.annotationIdx!)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setPinned(isActive ? null : seg.annotationIdx!);
                  }
                }}
                className={cn(
                  "bg-agent-research/25 text-foreground rounded-sm cursor-help",
                  "decoration-agent-research decoration-dotted underline underline-offset-4",
                  "focus:outline-none focus-visible:ring-2 focus-visible:ring-agent-research",
                  isActive && "bg-agent-research/40",
                )}
                title="Show source (click or press Enter)"
              >
                {seg.text}
              </mark>
              <span
                role="tooltip"
                className={cn(
                  "pointer-events-none absolute left-0 -bottom-1 translate-y-full",
                  "z-30 w-72 rounded-lg border bg-card p-3 shadow-lg text-xs",
                  // Reveal on hover OR keyboard focus within the group, or when pinned.
                  "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity",
                  isActive && "opacity-100 pointer-events-auto",
                )}
              >
                <p className="text-[10px] uppercase tracking-wider text-agent-research font-semibold mb-1.5">
                  Customer voice
                </p>
                <p className="text-foreground italic leading-relaxed">"{ann.source}"</p>
              </span>
            </span>
          );
        })}
      </p>
      <p className="text-[11px] text-muted-foreground mt-2">
        <span className="inline-block w-2 h-2 rounded-full bg-agent-research/40 mr-1.5 align-middle" />
        Highlighted phrases came from your customer voice corpus · hover or click to see the source
      </p>
    </div>
  );
}

interface Annotation {
  start: number;
  end: number;
  source: string;
}

function buildAnnotations(text: string, sources: string[]): Annotation[] {
  const annotations: Annotation[] = [];
  for (const source of sources) {
    const match = findLongestNgramMatch(text, source, 4);
    if (match) annotations.push({ ...match, source });
  }
  // Resolve overlaps: longest wins.
  annotations.sort((a, b) => (b.end - b.start) - (a.end - a.start));
  const accepted: Annotation[] = [];
  for (const ann of annotations) {
    const overlaps = accepted.some(
      (a) => !(ann.end <= a.start || ann.start >= a.end),
    );
    if (!overlaps) accepted.push(ann);
  }
  return accepted.sort((a, b) => a.start - b.start);
}

function findLongestNgramMatch(
  text: string,
  source: string,
  minWords: number,
): { start: number; end: number } | null {
  // Normalize whitespace for matching but report positions in original text.
  const norm = (s: string) => s.toLowerCase().replace(/\s+/g, " ").trim();
  const sourceWords = norm(source).split(" ").filter((w) => w.length > 0);
  if (sourceWords.length < minWords) return null;

  const textLower = text.toLowerCase();

  // Try the longest possible n-gram, walk shorter if no hit.
  for (let n = Math.min(sourceWords.length, 14); n >= minWords; n--) {
    for (let i = 0; i + n <= sourceWords.length; i++) {
      const phrase = sourceWords.slice(i, i + n).join(" ");
      const idx = textLower.indexOf(phrase);
      if (idx >= 0) return { start: idx, end: idx + phrase.length };
    }
  }
  return null;
}

interface Segment {
  text: string;
  annotationIdx: number | null;
}

function sliceText(text: string, annotations: Annotation[]): Segment[] {
  if (annotations.length === 0) return [{ text, annotationIdx: null }];
  const segments: Segment[] = [];
  let cursor = 0;
  for (let i = 0; i < annotations.length; i++) {
    const ann = annotations[i];
    if (cursor < ann.start) {
      segments.push({ text: text.slice(cursor, ann.start), annotationIdx: null });
    }
    segments.push({ text: text.slice(ann.start, ann.end), annotationIdx: i });
    cursor = ann.end;
  }
  if (cursor < text.length) {
    segments.push({ text: text.slice(cursor), annotationIdx: null });
  }
  return segments;
}
