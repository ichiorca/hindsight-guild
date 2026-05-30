import { useState } from "react";
import { NavLink, Outlet, Link } from "react-router-dom";
import { Command } from "lucide-react";
import { cn } from "@/lib/utils";
import { AGENTS, FRONT_LINE_AGENT_IDS } from "@/lib/agents";
import { NAV_SECTIONS } from "@/lib/nav";
import { useLearningSummary } from "@/lib/api";
import { CommandPalette } from "@/components/CommandPalette";
import { Topbar } from "@/components/Topbar";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Kbd } from "@/components/Kbd";
import { LiveTicker, AGENT_COUNT } from "@/components/LiveTicker";
import { AgentStack } from "@/components/AgentBadge";
import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts";

export function Layout() {
  const [paletteOpen, setPaletteOpen] = useState(false);
  // Nav badges — fanned out here so NAV_SECTIONS stays declarative.
  // Only one badge source today (pending_proposals); add more here when
  // we wire e.g. Queue's draft count or Signals's pending-trigger count.
  const learning = useLearningSummary(7);
  const badgeCounts: Record<string, number | undefined> = {
    pending_proposals: learning.data?.pending_proposals,
  };
  // Full roster, front-line first, so AgentStack shows the daily-driver faces
  // and a "+N" pill for the specialists on call.
  const rosterIds: string[] = [
    ...FRONT_LINE_AGENT_IDS,
    ...Object.keys(AGENTS).filter(
      (id) => !(FRONT_LINE_AGENT_IDS as readonly string[]).includes(id),
    ),
  ];

  // Global ⌘K / Ctrl-K
  useKeyboardShortcuts({
    "mod+k": () => setPaletteOpen((v) => !v),
  });

  return (
    <div className="min-h-screen flex bg-background">
      {/* Keyboard users land here first — jump past the nav to the page. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:z-[70] focus:top-3 focus:left-3 focus:rounded-md focus:bg-primary focus:text-primary-foreground focus:px-3 focus:py-2 focus:text-sm focus:font-medium"
      >
        Skip to content
      </a>
      {/* Desktop sidebar */}
      <aside className="hidden lg:flex w-72 shrink-0 border-r flex-col bg-card/40 backdrop-blur">
        <div className="px-6 py-6 border-b">
          <div className="flex items-center gap-3">
            <div className="relative h-10 w-10">
              <div className="absolute inset-0 rounded-xl bg-gradient-to-br from-ocean-600 via-ocean-700 to-ocean-900 shadow-md shadow-ocean-900/20" />
              <div className="absolute inset-0 rounded-xl ring-1 ring-ocean-900/40" />
              <div className="absolute inset-0 flex items-center justify-center text-white font-serif text-base font-semibold tracking-tight">
                h/g
              </div>
            </div>
            <div className="flex flex-col leading-tight">
              <span className="font-serif text-base font-semibold tracking-tight">
                Hindsight Guild
              </span>
            </div>
          </div>

          <button
            onClick={() => setPaletteOpen(true)}
            className="mt-4 w-full inline-flex items-center gap-2 px-3 py-2 rounded-lg border bg-card hover:bg-muted/60 transition-colors text-sm text-muted-foreground"
          >
            <Command className="h-3.5 w-3.5" />
            <span>Jump anywhere…</span>
            <Kbd keys={["⌘", "K"]} className="ml-auto" />
          </button>
        </div>

        <nav className="flex-1 px-3 py-4 space-y-5 overflow-y-auto">
          {NAV_SECTIONS.map((section) => (
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
                  <NavLink
                    key={item.to}
                    to={item.to}
                    className={({ isActive }) =>
                      cn(
                        "group flex items-start gap-3 rounded-lg px-3 py-2 transition-all",
                        isActive
                          ? "bg-primary/10 text-foreground"
                          : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <item.icon
                          className={cn(
                            "h-4 w-4 mt-0.5 shrink-0",
                            isActive ? "text-primary" : "text-muted-foreground group-hover:text-foreground",
                          )}
                        />
                        <div className="flex flex-col min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium leading-tight">{item.label}</span>
                            {typeof badgeCount === "number" && badgeCount > 0 && (
                              <span
                                className="ml-auto inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1.5 rounded-full bg-primary/15 text-primary text-[10px] font-semibold tabular-nums"
                                title={`${badgeCount} pending`}
                              >
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
                );
              })}
            </div>
          ))}
        </nav>

        <div className="px-4 py-4 border-t space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium">
              On shift
            </p>
            <ThemeToggle />
          </div>
          <Link to="/agents" className="block group">
            {/* Front-line avatars + a "+N specialists" pill, via the shared
                AgentStack (was duplicated inline here). Ordering front-line
                first so the visible faces are the daily drivers. */}
            <AgentStack ids={rosterIds} max={FRONT_LINE_AGENT_IDS.length} />
            <p className="text-[11px] text-muted-foreground leading-tight mt-2 group-hover:text-foreground transition-colors">
              {AGENT_COUNT} agents · {FRONT_LINE_AGENT_IDS.length} on the front line, {AGENT_COUNT - FRONT_LINE_AGENT_IDS.length} specialists on call.
            </p>
          </Link>
        </div>
      </aside>

      {/* Mobile topbar + content. LiveTicker collapses when idle so it
          doesn't take up vertical space — only renders when an agent
          job is in flight or telemetry was emitted in the last 60s. */}
      <main id="main-content" className="flex-1 min-w-0 flex flex-col">
        <Topbar onOpenPalette={() => setPaletteOpen(true)} />
        <LiveTicker />
        <Outlet />
      </main>

      {/* Global command palette */}
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}
