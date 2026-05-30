/**
 * Word-level diff via longest-common-subsequence.
 *
 * Good enough for paragraph-sized marketing copy. Operates on whitespace
 * tokens so we preserve the natural reading rhythm in the rendered diff.
 */

export type DiffOp =
  | { type: "equal"; text: string }
  | { type: "add"; text: string }
  | { type: "remove"; text: string };

function tokenize(text: string): string[] {
  // Split keeping whitespace + punctuation as separate tokens so the diff
  // doesn't blow up on a single comma.
  return text.split(/(\s+|[.,!?;:—\-()\[\]"'])/).filter((t) => t.length > 0);
}

export function diffWords(before: string, after: string): DiffOp[] {
  const a = tokenize(before);
  const b = tokenize(after);
  const n = a.length;
  const m = b.length;

  // LCS table
  const dp: number[][] = Array.from({ length: n + 1 }, () =>
    new Array(m + 1).fill(0),
  );
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      dp[i][j] = a[i - 1] === b[j - 1]
        ? dp[i - 1][j - 1] + 1
        : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }

  // Walk back and emit ops; coalesce adjacent ops of the same kind.
  const ops: DiffOp[] = [];
  let i = n, j = m;
  while (i > 0 && j > 0) {
    if (a[i - 1] === b[j - 1]) {
      ops.unshift({ type: "equal", text: a[i - 1] });
      i--; j--;
    } else if (dp[i - 1][j] >= dp[i][j - 1]) {
      ops.unshift({ type: "remove", text: a[i - 1] });
      i--;
    } else {
      ops.unshift({ type: "add", text: b[j - 1] });
      j--;
    }
  }
  while (i > 0) { ops.unshift({ type: "remove", text: a[i - 1] }); i--; }
  while (j > 0) { ops.unshift({ type: "add", text: b[j - 1] }); j--; }

  // Coalesce
  const merged: DiffOp[] = [];
  for (const op of ops) {
    const last = merged[merged.length - 1];
    if (last && last.type === op.type) last.text += op.text;
    else merged.push({ ...op });
  }
  return merged;
}

export interface DiffSummary {
  added: number;
  removed: number;
  changed: boolean;
}

export function summarizeDiff(ops: DiffOp[]): DiffSummary {
  let added = 0, removed = 0;
  for (const op of ops) {
    if (op.type === "add") added += op.text.length;
    if (op.type === "remove") removed += op.text.length;
  }
  return { added, removed, changed: added > 0 || removed > 0 };
}
