/**
 * Pipeline stepper — the multi-agent demo moment.
 *
 * When the founder runs the Research → Content → Review pipeline, this
 * surfaces each agent's contribution in real time. It's the difference
 * between "a spinner ran" and "I watched three agents collaborate."
 */

import { CheckCircle2, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { AGENTS, type AgentId } from "@/lib/agents";

export interface PipelineStep {
  agent: AgentId;
  state: "pending" | "running" | "done";
  durationMs?: number;
  summary?: string;
}

interface PipelineStepperProps {
  steps: PipelineStep[];
  className?: string;
}

export function PipelineStepper({ steps, className }: PipelineStepperProps) {
  return (
    <ol className={cn("space-y-3", className)}>
      {steps.map((step, idx) => {
        const agent = AGENTS[step.agent];
        const Icon = agent.icon;
        const isLast = idx === steps.length - 1;

        return (
          <li key={agent.id} className="relative flex items-start gap-4">
            {/* Connector line */}
            {!isLast && (
              <span
                className={cn(
                  "absolute left-[18px] top-10 bottom-[-12px] w-px",
                  step.state === "done" ? "bg-success/40" : "bg-border",
                )}
                aria-hidden
              />
            )}

            {/* Agent avatar */}
            <span
              className={cn(
                "relative inline-flex items-center justify-center h-9 w-9 rounded-full ring-1 shrink-0 transition-colors",
                step.state === "running"
                  ? cn(agent.colorClass, agent.textClass, agent.ringClass, "animate-pulse-soft")
                  : step.state === "done"
                    ? "bg-success/15 text-success ring-success/30"
                    : "bg-muted text-muted-foreground ring-border",
              )}
            >
              {step.state === "done" ? (
                <CheckCircle2 className="h-4 w-4" />
              ) : step.state === "running" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Icon className="h-4 w-4" />
              )}
            </span>

            {/* Row content */}
            <div className="flex-1 min-w-0 pb-1 pt-1">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-sm font-medium">{agent.name} Agent</span>
                  <span className="text-xs text-muted-foreground italic truncate">
                    {agent.role}
                  </span>
                </div>
                {step.durationMs != null && step.state === "done" && (
                  <span className="font-mono text-[11px] text-muted-foreground shrink-0">
                    {(step.durationMs / 1000).toFixed(1)}s
                  </span>
                )}
              </div>
              {step.summary && (
                <p className={cn(
                  "text-[13px] mt-1",
                  step.state === "running" ? "text-foreground" : "text-muted-foreground",
                )}>
                  {step.summary}
                </p>
              )}
              {step.state === "pending" && (
                <p className="text-[12px] text-muted-foreground/60 mt-0.5">Waiting…</p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
