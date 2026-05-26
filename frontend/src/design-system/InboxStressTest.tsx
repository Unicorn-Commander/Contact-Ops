import { memo, useMemo } from "react";
import type { CSSProperties } from "react";
import { AlertCircle, GitMerge, PencilLine, ShieldCheck } from "lucide-react";
import { AgentBadge } from "@/design-system/AgentBadge";
import { ConfidenceIndicator } from "@/design-system/ConfidenceIndicator";
import { KeyboardHint } from "@/design-system/KeyboardHint";
import { MonoNumeric } from "@/design-system/MonoNumeric";
import { useDensity } from "@/design-system/Density";
import { agentColorVars } from "@/design-system/tokens";
import { cn } from "@/lib/utils";

type StressProposal = {
  id: string;
  agent: string;
  action: "merge" | "enrich" | "tag" | "voice-match";
  confidence: number;
  title: string;
  detail: string;
  conflict: boolean;
  age: string;
  sources: number;
};

const agents = [
  "dedupe-agent",
  "voice-match-agent",
  "enrichment-agent",
  "tag-agent",
  "relationship-agent",
  "carddav-reconcile"
];

const names = [
  "Avery Hart",
  "Mina Chen",
  "Rafael Ortiz",
  "Priya Shah",
  "Drew Kaplan",
  "Sofia Nguyen",
  "Jon Bell",
  "Iris Morgan"
];

const orgs = ["Northstar Labs", "Magic Unicorn", "Centerdeep", "Helio Civic", "Rook Health", "Atlas Field"];

function makeProposals(count: number): StressProposal[] {
  return Array.from({ length: count }, (_, index) => {
    const agent = agents[index % agents.length];
    const name = names[index % names.length];
    const org = orgs[(index * 3) % orgs.length];
    const actionIndex = index % 4;
    const action = (["merge", "enrich", "tag", "voice-match"] as const)[actionIndex];
    const confidence = Math.max(0.34, Math.min(0.98, 0.98 - ((index * 17) % 64) / 100));
    const conflict = index % 11 === 0 || confidence < 0.5;
    return {
      id: `P-${String(index + 1).padStart(4, "0")}`,
      agent,
      action,
      confidence,
      title:
        action === "merge"
          ? `Merge duplicate profile for ${name}`
          : action === "voice-match"
            ? `Attach speaker identity to ${name}`
            : action === "tag"
              ? `Apply ${org} relationship tag`
              : `Enrich ${name} with ${org} evidence`,
      detail:
        action === "merge"
          ? "Email, phone, and employer overlap with canonical contact."
          : action === "voice-match"
            ? "Meeting-Ops voice print matched two recent calls."
            : action === "tag"
              ? "Source events indicate active operational relationship."
              : "Data Intel and CardDAV agree on title and organization.",
      conflict,
      age: `${(index % 48) + 1}m`,
      sources: (index % 5) + 1
    };
  });
}

const actionIcon = {
  merge: GitMerge,
  enrich: ShieldCheck,
  tag: PencilLine,
  "voice-match": ShieldCheck
} as const;

const StressRow = memo(function StressRow({ proposal }: { proposal: StressProposal }) {
  const Icon = actionIcon[proposal.action];
  const style = {
    ...agentColorVars(proposal.agent),
    borderLeftColor: "var(--agent-color)"
  } as CSSProperties;

  return (
    <div
      className={cn(
        "grid min-h-12 grid-cols-[minmax(140px,180px)_minmax(0,1fr)_auto_auto] items-center gap-3 border-b border-border/70 border-l-2 bg-card/70 px-3 py-2 text-sm transition-colors hover:bg-muted/45",
        proposal.conflict && "bg-[oklch(var(--co-amber-950)_/_0.16)]"
      )}
      style={style}
    >
      <AgentBadge slug={proposal.agent} size="xs" />
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2">
          <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" strokeWidth={1.6} />
          <p className="truncate font-medium text-foreground">{proposal.title}</p>
          {proposal.conflict ? <AlertCircle className="h-3.5 w-3.5 shrink-0 text-[oklch(var(--co-amber-500))]" /> : null}
        </div>
        <p className="truncate text-xs text-muted-foreground">{proposal.detail}</p>
      </div>
      <div className="hidden items-center gap-2 md:flex">
        <ConfidenceIndicator value={proposal.confidence} mode="binary" showLabel={false} />
        <MonoNumeric tone="muted">{proposal.sources} src</MonoNumeric>
        <MonoNumeric tone="muted">{proposal.age}</MonoNumeric>
      </div>
      <div className="hidden items-center gap-1 lg:flex">
        <KeyboardHint keys="Y" label="Approve" />
        <KeyboardHint keys="E" label="Edit" />
        <KeyboardHint keys="N" label="Reject" />
      </div>
    </div>
  );
});

export function InboxStressTest() {
  const proposals = useMemo(() => makeProposals(200), []);
  const { density } = useDensity();

  return (
    <section className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-background shadow-[var(--shadow-2)]">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-card px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-foreground">InboxStressTest</h2>
          <p className="text-xs text-muted-foreground">
            <MonoNumeric tone="muted">{proposals.length}</MonoNumeric> proposal rows, density {density}
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <KeyboardHint keys="J" label="Next" />
          <KeyboardHint keys="K" label="Previous" />
          <KeyboardHint keys="mod+Y" label="Approve selected" />
        </div>
      </header>
      <div className="co-scrollbar max-h-[680px] overflow-auto [content-visibility:auto]">
        {proposals.map((proposal) => (
          <StressRow key={proposal.id} proposal={proposal} />
        ))}
      </div>
    </section>
  );
}
