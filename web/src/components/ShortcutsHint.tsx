import { useState } from "react";
import { Keyboard } from "lucide-react";
import { cn } from "@/lib/utils";
import { Kbd } from "./Kbd";

interface Shortcut {
  keys: string[];
  label: string;
}

interface ShortcutsHintProps {
  shortcuts: Shortcut[];
  className?: string;
}

/**
 * A compact, dismissible shortcuts hint. Sits inline in the page header so
 * keyboard ergonomics are discoverable without a modal.
 */
export function ShortcutsHint({ shortcuts, className }: ShortcutsHintProps) {
  const [open, setOpen] = useState(false);
  return (
    <div className={cn("relative", className)}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-1 rounded-md hover:bg-muted"
      >
        <Keyboard className="h-3.5 w-3.5" />
        Shortcuts
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-2 z-20 w-72 rounded-lg border bg-card shadow-lg animate-fade-in p-3">
          <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium mb-2">
            Keyboard
          </p>
          <ul className="space-y-1.5">
            {shortcuts.map((s) => (
              <li key={s.label} className="flex items-center justify-between text-sm">
                <span>{s.label}</span>
                <Kbd keys={s.keys} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
