/**
 * Keyboard hint pill. Founders are power users — show shortcuts inline.
 */
import { cn } from "@/lib/utils";

interface KbdProps {
  keys: string[];
  className?: string;
}

export function Kbd({ keys, className }: KbdProps) {
  return (
    <span className={cn("inline-flex items-center gap-0.5", className)}>
      {keys.map((k, i) => (
        <kbd key={`${k}-${i}`} className="kbd">{k}</kbd>
      ))}
    </span>
  );
}
