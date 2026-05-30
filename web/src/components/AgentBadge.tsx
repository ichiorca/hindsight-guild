import { cn } from "@/lib/utils";
import { AGENTS, getAgent, type AgentId } from "@/lib/agents";

interface AgentBadgeProps {
  id: AgentId | string | null | undefined;
  size?: "sm" | "md" | "lg";
  showName?: boolean;
  showRole?: boolean;
  className?: string;
}

export function AgentBadge({ id, size = "md", showName = true, showRole = false, className }: AgentBadgeProps) {
  const agent = getAgent(id);
  if (!agent) return null;
  const Icon = agent.icon;

  const sizes = {
    sm: { box: "h-6 w-6", icon: "h-3 w-3", text: "text-xs" },
    md: { box: "h-7 w-7", icon: "h-3.5 w-3.5", text: "text-sm" },
    lg: { box: "h-10 w-10", icon: "h-5 w-5", text: "text-base" },
  }[size];

  return (
    <div className={cn("inline-flex items-center gap-2", className)}>
      <span
        className={cn(
          "inline-flex items-center justify-center rounded-full ring-1",
          sizes.box, agent.colorClass, agent.textClass, agent.ringClass,
        )}
        title={`${agent.name} Agent — ${agent.role}`}
      >
        <Icon className={sizes.icon} />
      </span>
      {showName && (
        <span className="flex flex-col leading-tight min-w-0">
          <span className={cn(sizes.text, "font-medium")}>{agent.name}</span>
          {showRole && (
            <span className="text-[11px] text-muted-foreground truncate">{agent.role}</span>
          )}
        </span>
      )}
    </div>
  );
}

export function AgentStack({ ids, max = 4 }: { ids: string[]; max?: number }) {
  const visible = ids.slice(0, max);
  const extra = ids.length - visible.length;
  return (
    <div className="flex items-center -space-x-2">
      {visible.map((id) => {
        const agent = AGENTS[id as AgentId];
        if (!agent) return null;
        const Icon = agent.icon;
        return (
          <span
            key={id}
            className={cn(
              "inline-flex items-center justify-center h-7 w-7 rounded-full ring-2 ring-card",
              agent.colorClass, agent.textClass,
            )}
            title={agent.name}
          >
            <Icon className="h-3.5 w-3.5" />
          </span>
        );
      })}
      {extra > 0 && (
        <span className="inline-flex items-center justify-center h-7 w-7 rounded-full ring-2 ring-card bg-muted text-muted-foreground text-xs font-medium">
          +{extra}
        </span>
      )}
    </div>
  );
}
