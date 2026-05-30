import { Loader2, CheckCircle2, ExternalLink, AlertCircle, Hand } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PublishState } from "@/lib/types";

interface PublishStateBadgeProps {
  state: PublishState | null | undefined;
  url?: string | null;
  mode?: "api" | "manual_review" | null;
  className?: string;
}

const META: Record<PublishState, {
  label: string;
  icon: typeof Loader2;
  className: string;
  pulse?: boolean;
}> = {
  publishing: {
    label: "Publishing…",
    icon: Loader2,
    className: "bg-primary/10 text-primary ring-primary/20",
    pulse: true,
  },
  published: {
    label: "Published",
    icon: CheckCircle2,
    className: "bg-success/10 text-success ring-success/20",
  },
  manual_review: {
    label: "Needs manual publish",
    icon: Hand,
    className: "bg-warning/15 text-foreground ring-warning/30",
  },
  failed: {
    label: "Publish failed",
    icon: AlertCircle,
    className: "bg-destructive/10 text-destructive ring-destructive/20",
  },
};

export function PublishStateBadge({ state, url, mode, className }: PublishStateBadgeProps) {
  if (!state) return null;
  const meta = META[state];
  if (!meta) return null;
  const Icon = meta.icon;

  const content = (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-2 py-0.5 text-[11px] font-medium rounded-full ring-1",
        meta.className,
        className,
      )}
      title={mode === "manual_review"
        ? "Substack API unavailable — staged for manual publish."
        : undefined}
    >
      <Icon className={cn("h-3 w-3", meta.pulse && "animate-spin")} />
      {meta.label}
      {url && state === "published" && <ExternalLink className="h-2.5 w-2.5 opacity-70" />}
    </span>
  );

  if (url && state === "published") {
    return (
      <a href={url} target="_blank" rel="noreferrer" className="hover:opacity-80 transition-opacity">
        {content}
      </a>
    );
  }
  return content;
}
