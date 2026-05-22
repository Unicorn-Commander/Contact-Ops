/**
 * AgentBadge — 16x16 svg pictogram + agent name + version.
 *
 * Distinct color + lucide icon per agent slug. Compact mode shows just the
 * icon with a tooltip on hover; full mode shows icon + name + version.
 *
 * Slugs match the 12 Phase 3 agents (see backend agent registry).
 */
import {
  Activity,
  BadgeCheck,
  Boxes,
  Clock,
  GitBranch,
  GitMerge,
  GraduationCap,
  Link2,
  Mic,
  Network,
  Radar,
  Tags as TagsIcon,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

type AgentVisual = {
  /** lucide icon component */
  Icon: LucideIcon;
  /** Tailwind text + bg accent tokens */
  text: string;
  bg: string;
  /** Human label for tooltip */
  label: string;
};

const FALLBACK: AgentVisual = {
  Icon: Boxes,
  text: "text-slate-500",
  bg: "bg-slate-500/10",
  label: "Unknown agent",
};

const AGENT_VISUALS: Record<string, AgentVisual> = {
  dedup: { Icon: GitMerge, text: "text-violet-500", bg: "bg-violet-500/10", label: "Dedup" },
  "voice-match": { Icon: Mic, text: "text-pink-500", bg: "bg-pink-500/10", label: "Voice Match" },
  enrichment: { Icon: BadgeCheck, text: "text-emerald-500", bg: "bg-emerald-500/10", label: "Enrichment" },
  lifecycle: { Icon: Clock, text: "text-amber-500", bg: "bg-amber-500/10", label: "Lifecycle" },
  tag: { Icon: TagsIcon, text: "text-sky-500", bg: "bg-sky-500/10", label: "Tagger" },
  "relationship-inference": { Icon: Network, text: "text-indigo-500", bg: "bg-indigo-500/10", label: "Relationships" },
  "carddav-reconciliation": { Icon: GitBranch, text: "text-cyan-500", bg: "bg-cyan-500/10", label: "CardDAV Sync" },
  "calibration-daemon": { Icon: GraduationCap, text: "text-fuchsia-500", bg: "bg-fuchsia-500/10", label: "Calibration" },
  "data-intel-bridge": { Icon: Radar, text: "text-orange-500", bg: "bg-orange-500/10", label: "Data Intel" },
  "graph-sync": { Icon: Link2, text: "text-teal-500", bg: "bg-teal-500/10", label: "Graph Sync" },
  "communication-signal": { Icon: Activity, text: "text-rose-500", bg: "bg-rose-500/10", label: "Comms Signal" },
  "provenance-promoter": { Icon: BadgeCheck, text: "text-lime-500", bg: "bg-lime-500/10", label: "Provenance" },
};

export function getAgentVisual(slug: string): AgentVisual {
  return AGENT_VISUALS[slug] ?? FALLBACK;
}

export type AgentBadgeProps = {
  slug: string;
  version?: string;
  /** Compact = icon only; default = icon + label. */
  variant?: "compact" | "default" | "with-version";
  className?: string;
};

export function AgentBadge({
  slug,
  version,
  variant = "default",
  className,
}: AgentBadgeProps) {
  const v = getAgentVisual(slug);
  const Icon = v.Icon;

  if (variant === "compact") {
    return (
      <span
        className={cn(
          "inline-flex h-5 w-5 items-center justify-center rounded",
          v.bg,
          className,
        )}
        title={`${v.label}${version ? ` · ${version}` : ""}`}
        aria-label={`Agent ${v.label}${version ? ` version ${version}` : ""}`}
      >
        <Icon className={cn("h-3 w-3", v.text)} aria-hidden="true" />
      </span>
    );
  }

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs font-medium",
        v.bg,
        v.text,
        className,
      )}
      title={`${v.label}${version ? ` · ${version}` : ""}`}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      <span>{v.label}</span>
      {variant === "with-version" && version && (
        <span className="font-mono opacity-70">{version}</span>
      )}
    </span>
  );
}
