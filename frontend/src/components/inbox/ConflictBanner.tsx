/**
 * ConflictBanner - side-by-side rationales when two agents conflict on
 * the same (entity, field). Three load-bearing principles enforced here:
 *
 * 1. Never auto-resolve. Display both. Human picks.
 * 2. Equal real estate per rationale. Tailwind grid columns are 1fr 1fr.
 * 3. "Keep both as facts with source tags" is FIRST-CLASS, same visual
 *    prominence as Keep A / Keep B. The design intent (Rector might have
 *    been founder, then CEO, then ex-CEO) is that two assertions tagged
 *    with provenance are usually more accurate than one resolved value.
 */
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Proposal } from "@/lib/types";
import { AgentBadge, ConfidenceIndicator } from "@/design-system";

export type ConflictRationale = {
  proposal: Proposal;
  /** The differing field value */
  proposedValue: unknown;
  /** Short citation, e.g. "SEC EDGAR record, 2026-03-12" */
  source: string;
};

export type ConflictBannerProps = {
  fieldName: string;
  a: ConflictRationale;
  b: ConflictRationale;
  /** Disable the action buttons while a mutation is in-flight. */
  busy?: boolean;
  onKeepA?: () => void;
  onKeepB?: () => void;
  onKeepBoth?: () => void;
  onCustomValue?: () => void;
  onEscalate?: () => void;
};

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "-";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

function Side({ rationale }: { rationale: ConflictRationale }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <AgentBadge
          slug={rationale.proposal.agent_id}
          label={`${rationale.proposal.agent_id} ${rationale.proposal.agent_version}`}
          size="sm"
        />
        <ConfidenceIndicator
          value={rationale.proposal.confidence}
          mode="numeric"
          showLabel={false}
          size="sm"
        />
      </div>
      <p className="text-13">
        <span className="text-muted-foreground">proposed:</span>{" "}
        <span className="font-medium">{formatValue(rationale.proposedValue)}</span>
      </p>
      <p className="text-12 text-muted-foreground">
        <span className="font-medium">source:</span> {rationale.source}
      </p>
      <p className="text-12 italic text-muted-foreground">{rationale.proposal.rationale}</p>
    </div>
  );
}

export function ConflictBanner({
  fieldName,
  a,
  b,
  busy = false,
  onKeepA,
  onKeepB,
  onKeepBoth,
  onCustomValue,
  onEscalate,
}: ConflictBannerProps) {
  return (
    <section
      className="rounded-md border border-warning/45 bg-warning/10"
      aria-label={`Conflict on field ${fieldName}`}
    >
      <header className="flex items-center gap-co-8 border-b border-warning/35 px-co-12 py-co-8 text-warning">
        <AlertTriangle className="h-4 w-4" aria-hidden="true" />
        <span className="text-13 font-semibold">
          Conflict: 2 agents propose different changes
        </span>
        <span className="ml-auto font-mono text-11 uppercase text-warning">
          field: {fieldName}
        </span>
      </header>
      <div className="grid grid-cols-1 gap-co-16 p-co-12 md:grid-cols-2">
        <Side rationale={a} />
        <Side rationale={b} />
      </div>
      <footer className="flex flex-wrap items-center gap-co-6 border-t border-warning/35 bg-card/65 px-co-12 py-co-8">
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={onKeepA}
          className="h-8 border-warning/40"
        >
          Keep
          <AgentBadge slug={a.proposal.agent_id} size="xs" />
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={onKeepB}
          className="h-8 border-warning/40"
        >
          Keep
          <AgentBadge slug={b.proposal.agent_id} size="xs" />
        </Button>
        <Button
          size="sm"
          disabled={busy}
          onClick={onKeepBoth}
          className="h-8 bg-primary text-primary-foreground hover:bg-primary/90"
        >
          Keep both as facts with source tags
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={onCustomValue}>
          Custom value
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          onClick={onEscalate}
          className="text-warning hover:bg-warning/10"
        >
          Escalate for legal review
        </Button>
      </footer>
    </section>
  );
}
