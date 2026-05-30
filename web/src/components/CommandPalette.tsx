import * as Dialog from "@radix-ui/react-dialog";
import { Command as CmdK } from "cmdk";
import { useNavigate } from "react-router-dom";
import {
  FlaskConical, Layers,
  Sun, Moon, Monitor, Search,
  ArrowRight, Sparkles, type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useTheme, type Theme } from "@/lib/theme";
import { useSkills, useRunningExperiments } from "@/lib/api";
import { NAV_FLAT } from "@/lib/nav";
import { AGENTS } from "@/lib/agents";

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}

interface CmdItem {
  id: string;
  label: string;
  icon: LucideIcon;
  group: string;
  hint?: string;
  onSelect: () => void;
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const navigate = useNavigate();
  const { setTheme } = useTheme();
  const skills = useSkills();
  const running = useRunningExperiments();

  // Build the "Go to" list from the single nav source of truth so the
  // palette can NEVER drift from the sidebar (it previously hardcoded 9
  // routes and silently lacked Signals / Self-Learning / Agents). cmdk
  // handles filtering on the input value.
  const navItems: CmdItem[] = NAV_FLAT.map((n) => ({
    id: `nav-${n.to}`,
    label: n.label,
    icon: n.icon,
    group: "Go to",
    hint: n.description,
    onSelect: () => { navigate(n.to); onOpenChange(false); },
  }));

  const themeItems: CmdItem[] = (["light", "dark", "system"] as Theme[]).map((t) => ({
    id: `theme-${t}`,
    label: `Switch theme: ${t}`,
    icon: t === "light" ? Sun : t === "dark" ? Moon : Monitor,
    group: "Settings",
    onSelect: () => { setTheme(t); onOpenChange(false); },
  }));

  const skillItems: CmdItem[] = (skills.data ?? []).map((s) => ({
    id: `skill-${s._id}`,
    label: `Skill: ${s._id.replace(/_/g, " ")}`,
    icon: Layers,
    group: "Jump to skill",
    hint: s.current_version,
    onSelect: () => { navigate(`/skills?skill=${s._id}`); onOpenChange(false); },
  }));

  const expItems: CmdItem[] = (running.data ?? []).slice(0, 8).map((e) => ({
    id: `exp-${e._id}`,
    label: e.title,
    icon: FlaskConical,
    group: "Running experiments",
    hint: e.channel,
    onSelect: () => { navigate("/experiments"); onOpenChange(false); },
  }));

  const all = [...navItems, ...themeItems, ...skillItems, ...expItems];
  const groups = [...new Set(all.map((i) => i.group))];

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-background/60 backdrop-blur-sm data-[state=open]:animate-fade-in" />
        <Dialog.Content
          className={cn(
            "fixed left-1/2 top-[20%] -translate-x-1/2 z-50 w-[92vw] max-w-xl",
            "rounded-xl border bg-card shadow-2xl overflow-hidden",
            "data-[state=open]:animate-fade-in",
          )}
        >
          <Dialog.Title className="sr-only">Command palette</Dialog.Title>
          <CmdK label="Command palette" className="flex flex-col">
            <div className="flex items-center gap-2 px-4 border-b">
              <Search className="h-4 w-4 text-muted-foreground" />
              <CmdK.Input
                autoFocus
                placeholder="Jump to a page, skill, or action…"
                className="flex-1 h-12 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              />
              <kbd className="kbd">ESC</kbd>
            </div>
            <CmdK.List className="max-h-[400px] overflow-y-auto p-2">
              <CmdK.Empty className="py-8 text-center text-sm text-muted-foreground">
                <Sparkles className="mx-auto h-5 w-5 mb-2 opacity-50" />
                Nothing matches. Try "queue" or "promote".
              </CmdK.Empty>
              {groups.map((group) => (
                <CmdK.Group
                  key={group}
                  heading={
                    <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold px-2 mt-3 mb-1.5 block">
                      {group}
                    </span>
                  }
                >
                  {all
                    .filter((i) => i.group === group)
                    .map((item) => {
                      const Icon = item.icon;
                      return (
                        <CmdK.Item
                          key={item.id}
                          value={`${item.label} ${item.hint ?? ""}`}
                          onSelect={item.onSelect}
                          className={cn(
                            "flex items-center gap-3 px-3 py-2 rounded-md cursor-pointer text-sm",
                            "data-[selected=true]:bg-primary/10 data-[selected=true]:text-foreground",
                          )}
                        >
                          <Icon className="h-4 w-4 text-muted-foreground shrink-0" />
                          <span className="flex-1">{item.label}</span>
                          {item.hint && (
                            <span className="text-xs text-muted-foreground font-mono truncate max-w-[140px]">
                              {item.hint}
                            </span>
                          )}
                          <ArrowRight className="h-3.5 w-3.5 text-muted-foreground opacity-0 group-hover:opacity-100" />
                        </CmdK.Item>
                      );
                    })}
                </CmdK.Group>
              ))}
            </CmdK.List>
            <div className="border-t px-4 py-2 flex items-center justify-between text-[11px] text-muted-foreground">
              <div className="flex items-center gap-3">
                <span className="inline-flex items-center gap-1">
                  <kbd className="kbd">↑</kbd><kbd className="kbd">↓</kbd> navigate
                </span>
                <span className="inline-flex items-center gap-1">
                  <kbd className="kbd">↵</kbd> select
                </span>
              </div>
              <div className="flex -space-x-1.5">
                {Object.values(AGENTS).slice(0, 5).map((a) => {
                  const Icon = a.icon;
                  return (
                    <span
                      key={a.id}
                      className={cn(
                        "inline-flex items-center justify-center h-5 w-5 rounded-full ring-2 ring-card",
                        a.colorClass, a.textClass,
                      )}
                    >
                      <Icon className="h-2.5 w-2.5" />
                    </span>
                  );
                })}
              </div>
            </div>
          </CmdK>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
