import { AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";

interface ErrorStateProps {
  /** What failed, in plain language (e.g. "the approval queue"). */
  what?: string;
  /** Refetch callback from the failing react-query hook. */
  onRetry?: () => void;
  className?: string;
}

/**
 * Honest failure state for a data fetch. Distinct from the Empty state so a
 * backend outage never masquerades as "nothing here / all clear" — the
 * single biggest trust gap in the original UI.
 */
export function ErrorState({ what = "this data", onRetry, className }: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center text-center py-12 px-6",
        "border border-dashed border-destructive/30 rounded-xl bg-destructive/5",
        className,
      )}
    >
      <div className="rounded-full bg-destructive/10 ring-1 ring-destructive/20 p-3 mb-4">
        <AlertTriangle className="h-6 w-6 text-destructive" aria-hidden />
      </div>
      <h3 className="font-serif text-lg font-semibold mb-1.5 tracking-tight">
        Couldn't load {what}
      </h3>
      <p className="text-sm text-muted-foreground max-w-md leading-relaxed">
        The server didn't respond as expected. This is a connection problem,
        not an empty inbox — your data is safe.
      </p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-5 inline-flex items-center justify-center h-9 px-4 rounded-md border border-border bg-card text-sm font-medium hover:bg-muted transition-colors"
        >
          Try again
        </button>
      )}
    </div>
  );
}
