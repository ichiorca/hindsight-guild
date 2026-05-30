import * as Dialog from "@radix-ui/react-dialog";
import { NavLink } from "react-router-dom";
import { Menu, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { AGENTS } from "@/lib/agents";
import type { NavSection } from "@/lib/nav";

interface MobileNavProps {
  sections: NavSection[];
  /** Optional badge counts keyed by NavItem.badge values. */
  badgeCounts?: Record<string, number | undefined>;
}

export function MobileNav({ sections, badgeCounts = {} }: MobileNavProps) {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <button
          aria-label="Open menu"
          className="inline-flex lg:hidden items-center justify-center h-9 w-9 rounded-md border bg-card text-foreground hover:bg-muted transition-colors"
        >
          <Menu className="h-4 w-4" />
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-background/60 backdrop-blur-sm data-[state=open]:animate-fade-in" />
        <Dialog.Content
          className={cn(
            "fixed inset-y-0 left-0 z-50 w-72 bg-card border-r flex flex-col",
            "shadow-xl data-[state=open]:animate-fade-in",
          )}
        >
          <Dialog.Title className="sr-only">Navigation</Dialog.Title>
          <div className="flex items-center justify-between px-6 py-5 border-b">
            <div className="flex items-center gap-3">
              <div className="relative h-9 w-9">
                <div className="absolute inset-0 rounded-xl bg-gradient-to-br from-ocean-600 via-ocean-700 to-ocean-900 shadow-md shadow-ocean-900/20" />
                <div className="absolute inset-0 rounded-xl ring-1 ring-ocean-900/40" />
                <div className="absolute inset-0 flex items-center justify-center text-white font-serif text-sm font-semibold">
                  a/m
                </div>
              </div>
              <div className="flex flex-col leading-tight">
                <span className="font-serif text-sm font-semibold">Hindsight Guild</span>
              </div>
            </div>
            <Dialog.Close asChild>
              <button
                aria-label="Close menu"
                className="inline-flex items-center justify-center h-8 w-8 rounded-md hover:bg-muted text-muted-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            </Dialog.Close>
          </div>

          <nav className="flex-1 px-3 py-4 space-y-5 overflow-y-auto">
            {sections.map((section) => (
              <div key={section.title} className="space-y-0.5">
                <div className="px-3 pb-1.5">
                  <p className="text-[10px] uppercase tracking-[0.08em] font-semibold text-muted-foreground/80">
                    {section.title}
                  </p>
                  {section.hint && (
                    <p className="text-[10px] text-muted-foreground/60 leading-tight mt-0.5">
                      {section.hint}
                    </p>
                  )}
                </div>
                {section.items.map((item) => {
                  const badgeCount = item.badge ? badgeCounts[item.badge] : undefined;
                  return (
                    <Dialog.Close asChild key={item.to}>
                      <NavLink
                        to={item.to}
                        className={({ isActive }) =>
                          cn(
                            "group flex items-start gap-3 rounded-lg px-3 py-2.5 transition-all",
                            isActive
                              ? "bg-primary/10 text-foreground"
                              : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                          )
                        }
                      >
                        {({ isActive }) => (
                          <>
                            <item.icon className={cn("h-4 w-4 mt-0.5 shrink-0", isActive && "text-primary")} />
                            <div className="flex flex-col min-w-0 flex-1">
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium leading-tight">{item.label}</span>
                                {typeof badgeCount === "number" && badgeCount > 0 && (
                                  <span className="ml-auto inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1.5 rounded-full bg-primary/15 text-primary text-[10px] font-semibold tabular-nums">
                                    {badgeCount}
                                  </span>
                                )}
                              </div>
                              <span className="text-[11px] leading-tight mt-0.5 opacity-80">
                                {item.description}
                              </span>
                            </div>
                          </>
                        )}
                      </NavLink>
                    </Dialog.Close>
                  );
                })}
              </div>
            ))}
          </nav>

          <div className="px-4 py-4 border-t">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium mb-2">
              On shift
            </p>
            <div className="flex -space-x-1.5">
              {Object.values(AGENTS).map((a) => {
                const Icon = a.icon;
                return (
                  <span
                    key={a.id}
                    className={cn(
                      "inline-flex items-center justify-center h-7 w-7 rounded-full ring-2 ring-card",
                      a.colorClass, a.textClass,
                    )}
                    title={a.name}
                  >
                    <Icon className="h-3.5 w-3.5" />
                  </span>
                );
              })}
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
