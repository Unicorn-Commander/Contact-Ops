/**
 * ConflictBanner — side-by-side rationales when two agents conflict on
 * the same (entity, field). Three load-bearing principles enforced here:
 *
 * 1. Never auto-resolve. Display both. Human picks.
 * 2. Equal real estate per rationale — Tailwind grid columns are 1fr 1fr.
 * 3. "Keep both as facts with source tags" is FIRST-CLASS, same visual
 *    prominence as Keep A / Keep B. The design intent (Rector might have
 *    been founder, then CEO, then ex-CEO) is that two assertions tagged
 *    with provenance are usually more accurate than one resolved value.
 */
import { AlertTriangle } from "lucide-react";
import { ConfidenceDots } from "@/components/inbox/ConfidenceDots";
import { AgentBadge } from "@/components/inbox/AgentBadge";
import { Button } from "@/components/ui/button";
import type { Proposal } from "@/lib/types";

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
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

function Side({ rationale }: { rationale: ConflictRationale }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <AgentBadge slug={rationale.proposal.agent_id} variant="default" />
        <ConfidenceDots
          value={rationale.proposal.confidence}
          showNumeric
          showLabel={false}
          size="sm"
        />
      </div>
      <p className="text-sm">
        <span className="text-muted-foreground">proposed:</span>{" "}
        <span className="font-medium">{formatValue(rationale.proposedValue)}</span>
      </p>
      <p className="text-xs text-muted-foreground">
        <span className="font-medium">source:</span> {rationale.source}
      </p>
      <p className="text-xs italic text-muted-foreground/80">{rationale.proposal.rationale}</p>
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
      className="rounded-md border border-amber-500/50 bg-amber-50/40 dark:bg-amber-950/20"
      aria-label={`Conflict on field ${fieldName}`}
    >
      <header className="flex items-center gap-2 border-b border-amber-500/40 px-3 py-2 text-amber-900 dark:text-amber-200">
        <AlertTriangle className="h-4 w-4" aria-hidden="true" />
        <span className="text-sm font-semibold">
          Conflict · 2 agents propose different changes
        </span>
        <span className="ml-auto font-mono text-xs uppercase tracking-wider text-amber-700/80 dark:text-amber-300/80">
          field: {fieldName}
        </span>
      </header>
      <div className="grid grid-cols-1 gap-4 p-3 md:grid-cols-2">
        <Side rationale={a} />
        <Side rationale={b} />
      </div>
      <footer className="flex flex-wrap items-center gap-2 border-t border-amber-500/40 bg-amber-50/30 px-3 py-2 dark:bg-amber-950/30">
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={onKeepA}
          className="border-amber-500/40"
        >
          Keep {a.proposal.agent_id}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={onKeepB}
          className="border-amber-500/40"
        >
          Keep {b.proposal.agent_id}
        </Button>
        <Button
          size="sm"
          disabled={busy}
          onClick={onKeepBoth}
          className="bg-violet-500 text-white hover:bg-violet-600"
        >
          Keep both as facts with source tags
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={onCustomValue}>
          Custom value…
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          onClick={onEscalate}
          className="text-amber-700 hover:bg-amber-500/10"
        >
          Escalate · flag for legal review
        </Button>
      </footer>
    </section>
  );
}
