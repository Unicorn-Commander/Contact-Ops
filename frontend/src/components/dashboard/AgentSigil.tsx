/**
 * Compact agent attribution: the canonical gradient sigil (per-agent hue, via
 * `agentColorVars` + the `.co-agent-sigil` token recipe) followed by the agent
 * slug in mono. This is the lighter, inline cousin of design-system/AgentBadge
 * — meant to sit inside dense dashboard rows where a full badge is too heavy.
 */
import type { CSSProperties } from "react";
import { Cpu } from "lucide-react";
import { agentColorVars } from "@/design-system/tokens";
import { cn } from "@/lib/utils";

export interface AgentSigilProps {
  slug: string;
  /** Render just the sigil mark without the slug label. */
  iconOnly?: boolean;
  className?: string;
}

export function AgentSigil({ slug, iconOnly = false, className }: AgentSigilProps) {
  const style = agentColorVars(slug) as CSSProperties;
  return (
    <span
      className={cn("inline-flex min-w-0 items-center gap-1.5", className)}
      style={style}
      data-agent-slug={slug}
      title={`Proposed by ${slug}`}
    >
      <span className="co-agent-sigil h-4 w-4 shrink-0" aria-hidden="true">
        <Cpu className="h-2.5 w-2.5" strokeWidth={1.6} />
      </span>
      {iconOnly ? null : (
        <span className="truncate font-mono text-xs text-[oklch(var(--agent-color))]">{slug}</span>
      )}
    </span>
  );
}
