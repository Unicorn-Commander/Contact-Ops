import { formatDistanceToNowStrict } from "date-fns";
import { cn } from "@/lib/utils";
import { AgentBadge } from "@/design-system/AgentBadge";
import { HumanBadge } from "@/design-system/HumanBadge";

export type AttributionActor =
  | {
      type: "agent";
      slug: string;
      label?: string;
    }
  | {
      type: "human";
      name: string;
      label?: string;
    };

export interface ActionAttributionProps {
  actor: AttributionActor;
  verb: string;
  timestamp: Date | string | number;
  target?: string;
  compact?: boolean;
  disabled?: boolean;
  className?: string;
}

function relativeTime(timestamp: Date | string | number) {
  const date = timestamp instanceof Date ? timestamp : new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "just now";
  return `${formatDistanceToNowStrict(date, { addSuffix: true })}`;
}

export function ActionAttribution({
  actor,
  verb,
  timestamp,
  target,
  compact = false,
  disabled = false,
  className
}: ActionAttributionProps) {
  const badge =
    actor.type === "agent" ? (
      <AgentBadge slug={actor.slug} label={actor.label ?? actor.slug} size="xs" disabled={disabled} />
    ) : (
      <HumanBadge name={actor.name} label={actor.label ?? actor.name} size="xs" disabled={disabled} />
    );

  return (
    <span
      className={cn(
        "inline-flex min-w-0 items-center gap-2 text-xs text-muted-foreground",
        compact && "gap-1.5 text-[11px]",
        disabled && "opacity-45",
        className
      )}
    >
      <span className="shrink-0 text-foreground">{verb}</span>
      <span>by</span>
      {badge}
      {target ? <span className="truncate">at {target}</span> : null}
      <span className="shrink-0">{relativeTime(timestamp)}</span>
    </span>
  );
}
