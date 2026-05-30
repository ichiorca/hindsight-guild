import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";

interface EmptyProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  className?: string;
  action?: React.ReactNode;
}

/**
 * Empty states are a brand surface. The copy here should sound like the
 * tool, not like a 404 page.
 */
export function Empty({ icon: Icon, title, description, className, action }: EmptyProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center py-16 px-6",
        "border border-dashed rounded-xl bg-subtle/60",
        className,
      )}
    >
      {Icon && (
        <div className="rounded-full bg-primary/10 ring-1 ring-primary/20 p-3 mb-4">
          <Icon className="h-6 w-6 text-primary" />
        </div>
      )}
      <h3 className="font-serif text-lg font-semibold mb-1.5 tracking-tight">{title}</h3>
      {description && (
        <p className="text-sm text-muted-foreground max-w-md leading-relaxed">{description}</p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
