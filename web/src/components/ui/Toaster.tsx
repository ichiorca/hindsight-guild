import { useSyncExternalStore } from "react";
import { CheckCircle2, AlertTriangle, Info, X } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  subscribeToasts,
  getToasts,
  dismissToast,
  type ToastItem,
} from "@/lib/toast";

const VARIANT: Record<
  ToastItem["variant"],
  { icon: typeof Info; ring: string; iconColor: string }
> = {
  success: { icon: CheckCircle2, ring: "ring-success/30", iconColor: "text-success" },
  error:   { icon: AlertTriangle, ring: "ring-destructive/40", iconColor: "text-destructive" },
  info:    { icon: Info, ring: "ring-info/30", iconColor: "text-info" },
  default: { icon: Info, ring: "ring-border", iconColor: "text-muted-foreground" },
};

/**
 * Renders the global toast stack. Mounted once at the app root.
 *
 * Each toast is its own polite live region so screen readers announce
 * success/failure of an action. Motion is gated behind motion-safe so
 * users with reduced-motion preferences don't get the slide-in.
 */
export function Toaster() {
  const items = useSyncExternalStore(subscribeToasts, getToasts, getToasts);

  return (
    <div
      aria-label="Notifications"
      className="fixed z-[60] bottom-4 right-4 flex flex-col gap-2 w-[min(92vw,22rem)] pointer-events-none"
    >
      {items.map((t) => {
        const meta = VARIANT[t.variant];
        const Icon = meta.icon;
        return (
          <div
            key={t.id}
            role="status"
            aria-live={t.variant === "error" ? "assertive" : "polite"}
            className={cn(
              "pointer-events-auto rounded-lg border bg-card shadow-lg ring-1 px-4 py-3",
              "flex items-start gap-3 motion-safe:animate-in motion-safe:slide-in-from-bottom-2 motion-safe:fade-in",
              meta.ring,
            )}
          >
            <Icon className={cn("h-4 w-4 mt-0.5 shrink-0", meta.iconColor)} aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium leading-snug">{t.title}</p>
              {t.description && (
                <p className="text-[13px] text-muted-foreground leading-snug mt-0.5">
                  {t.description}
                </p>
              )}
              {t.action && (
                <button
                  onClick={() => {
                    t.action!.onClick();
                    dismissToast(t.id);
                  }}
                  className="mt-2 text-[13px] font-medium text-primary hover:underline"
                >
                  {t.action.label}
                </button>
              )}
            </div>
            <button
              onClick={() => dismissToast(t.id)}
              aria-label="Dismiss notification"
              className="shrink-0 -mr-1 -mt-1 p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
