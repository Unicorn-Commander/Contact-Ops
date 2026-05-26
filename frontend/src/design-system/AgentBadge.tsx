import type { CSSProperties } from "react";
import { Cpu, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { agentColorVars } from "@/design-system/tokens";

export type AgentBadgeStatus = "idle" | "thinking" | "waiting" | "executing" | "done" | "error";
export type BadgeSize = "xs" | "sm" | "md";

export interface AgentBadgeProps {
  slug: string;
  label?: string;
  status?: AgentBadgeStatus;
  size?: BadgeSize;
  showLabel?: boolean;
  disabled?: boolean;
  className?: string;
}

const sizeClasses: Record<BadgeSize, { root: string; sigil: string; icon: string; text: string }> = {
  xs: {
    root: "h-5 gap-1 px-1.5 text-[10px]",
    sigil: "h-4 w-4",
    icon: "h-2.5 w-2.5",
    text: "max-w-20"
  },
  sm: {
    root: "h-6 gap-1.5 px-2 text-[11px]",
    sigil: "h-4 w-4",
    icon: "h-3 w-3",
    text: "max-w-28"
  },
  md: {
    root: "h-7 gap-2 px-2.5 text-xs",
    sigil: "h-5 w-5",
    icon: "h-3.5 w-3.5",
    text: "max-w-36"
  }
};

const statusLabel: Record<AgentBadgeStatus, string> = {
  idle: "idle",
  thinking: "thinking",
  waiting: "waiting",
  executing: "executing",
  done: "finished",
  error: "needs attention"
};

export function AgentBadge({
  slug,
  label = slug,
  status = "idle",
  size = "sm",
  showLabel = true,
  disabled = false,
  className
}: AgentBadgeProps) {
  const classes = sizeClasses[size];
  const style = agentColorVars(slug) as CSSProperties;
  const active = status === "thinking" || status === "waiting" || status === "executing";

  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-md border border-border bg-card font-mono font-medium leading-none text-foreground shadow-[var(--shadow-1)]",
        classes.root,
        disabled && "opacity-45",
        className
      )}
      style={style}
      title={`Agent ${label} is ${statusLabel[status]}`}
      aria-label={`Agent ${label}, ${statusLabel[status]}`}
      data-agent-slug={slug}
      data-agent-status={status}
    >
      <span className={cn("co-agent-sigil", classes.sigil)} aria-hidden="true">
        {active ? (
          <Loader2 className={cn(classes.icon, "animate-spin")} strokeWidth={1.6} />
        ) : (
          <Cpu className={classes.icon} strokeWidth={1.6} />
        )}
      </span>
      {showLabel ? <span className={cn("truncate", classes.text)}>{label}</span> : null}
    </span>
  );
}
