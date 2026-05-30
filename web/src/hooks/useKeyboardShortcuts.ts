import { useEffect } from "react";

type Handler = (e: KeyboardEvent) => void;

/**
 * Single-key shortcuts that fire only when the user is not in a text input.
 * Use this for queue navigation (J/K/A/E/R) etc.
 */
export function useKeyboardShortcuts(map: Record<string, Handler>, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable)
      ) {
        return;
      }
      const key = e.key.toLowerCase();
      const combo = (e.metaKey || e.ctrlKey) ? `mod+${key}` : key;
      const fn = map[combo] || map[key];
      if (fn) {
        e.preventDefault();
        fn(e);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [map, enabled]);
}
