import { cn } from "@/lib/utils";

interface PageHeaderProps {
  eyebrow?: string;
  title: string;
  description?: string;
  className?: string;
  children?: React.ReactNode;
}

export function PageHeader({ eyebrow, title, description, className, children }: PageHeaderProps) {
  return (
    <header
      className={cn(
        // Static on mobile so it can't sit behind the sticky mobile Topbar
        // (both used top-0 and collided). Sticky only on desktop, where there
        // is no Topbar above it.
        "static lg:sticky lg:top-0 z-10 bg-background/85 backdrop-blur-md border-b",
        "px-5 sm:px-8 py-5 sm:py-6 flex flex-wrap items-end justify-between gap-4",
        className,
      )}
    >
      <div className="min-w-0">
        {eyebrow && (
          <p className="text-[11px] font-medium uppercase tracking-[0.12em] text-primary mb-1.5">
            {eyebrow}
          </p>
        )}
        <h1 className="font-serif text-[28px] font-semibold tracking-tight leading-none">
          {title}
        </h1>
        {description && (
          <p className="text-sm text-muted-foreground mt-2 max-w-2xl">{description}</p>
        )}
      </div>
      {children && <div className="flex items-center gap-2 shrink-0">{children}</div>}
    </header>
  );
}
