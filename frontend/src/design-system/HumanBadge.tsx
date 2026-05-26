import { UserRound } from "lucide-react";
import { cn, initials } from "@/lib/utils";
import type { BadgeSize } from "@/design-system/AgentBadge";

export interface HumanBadgeProps {
  name: string;
  label?: string;
  size?: BadgeSize;
  showLabel?: boolean;
  disabled?: boolean;
  className?: string;
}

const sizeClasses: Record<BadgeSize, { root: string; sigil: string; icon: string; text: string }> = {
  xs: {
    root: "h-5 gap-1 px-1.5 text-[10px]",
    sigil: "h-4 w-4 text-[8px]",
    icon: "h-2.5 w-2.5",
    text: "max-w-20"
  },
  sm: {
    root: "h-6 gap-1.5 px-2 text-[11px]",
    sigil: "h-4 w-4 text-[9px]",
    icon: "h-3 w-3",
    text: "max-w-28"
  },
  md: {
    root: "h-7 gap-2 px-2.5 text-xs",
    sigil: "h-5 w-5 text-[10px]",
    icon: "h-3.5 w-3.5",
    text: "max-w-36"
  }
};

export function HumanBadge({
  name,
  label = name,
  size = "sm",
  showLabel = true,
  disabled = false,
  className
}: HumanBadgeProps) {
  const classes = sizeClasses[size];
  const fallback = initials(name);

  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full border border-border bg-card font-medium leading-none text-foreground shadow-[var(--shadow-1)]",
        classes.root,
        disabled && "opacity-45",
        className
      )}
      title={`Human ${label}`}
      aria-label={`Human ${label}`}
      data-human-name={name}
    >
      <span className={cn("co-human-sigil", classes.sigil)} aria-hidden="true">
        {fallback ? fallback : <UserRound className={classes.icon} strokeWidth={1.6} />}
      </span>
      {showLabel ? <span className={cn("truncate", classes.text)}>{label}</span> : null}
    </span>
  );
}
