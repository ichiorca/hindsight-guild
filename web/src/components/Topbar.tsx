import { Command, Sparkles } from "lucide-react";
import { Kbd } from "@/components/Kbd";
import { MobileNav } from "@/components/MobileNav";
import { ThemeToggle } from "@/components/ThemeToggle";
import { NAV_SECTIONS } from "@/lib/nav";
import { useLearningSummary } from "@/lib/api";

interface TopbarProps {
  onOpenPalette: () => void;
}

export function Topbar({ onOpenPalette }: TopbarProps) {
  const learning = useLearningSummary(7);
  const badgeCounts = {
    pending_proposals: learning.data?.pending_proposals,
  };
  return (
    <div className="lg:hidden sticky top-0 z-30 bg-background/85 backdrop-blur-md border-b">
      <div className="px-4 py-3 flex items-center gap-3">
        <MobileNav sections={NAV_SECTIONS} badgeCounts={badgeCounts} />
        <div className="flex items-center gap-2 min-w-0">
          <div className="relative h-7 w-7 shrink-0">
            <div className="absolute inset-0 rounded-md bg-gradient-to-br from-ocean-600 via-ocean-700 to-ocean-900 shadow-sm shadow-ocean-900/20" />
            <div className="absolute inset-0 rounded-md ring-1 ring-ocean-900/40" />
            <Sparkles className="absolute inset-0 m-auto h-3.5 w-3.5 text-white" />
          </div>
          <span className="font-serif text-sm font-semibold">Hindsight Guild</span>
        </div>
        <button
          onClick={onOpenPalette}
          aria-label="Open command palette"
          className="ml-auto inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-1.5 rounded-md hover:bg-muted"
        >
          <Command className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">Command</span>
          <Kbd keys={["⌘", "K"]} />
        </button>
        <ThemeToggle />
      </div>
    </div>
  );
}

// NAV used to be defined+exported here; it now lives in @/lib/nav as
// NAV_SECTIONS. Existing imports of `NAV` from Topbar should switch to
// `NAV_FLAT` from @/lib/nav (or `NAV_SECTIONS` if they want the grouped form).
