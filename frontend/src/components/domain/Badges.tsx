import { AgentBadge, ConfidenceIndicator, HumanBadge } from "@/design-system";
import { cn } from "@/lib/utils";

/**
 * A calm, neutral label chip for curation surfaces (people, orgs, filters).
 * Linear-style: a muted pill with a small brand dot rather than a loud fill.
 */
export function TagChip({ label, className }: { label: string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-muted/70 px-2 py-0.5 text-xs font-medium text-foreground/80",
        className
      )}
    >
      <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary/60" />
      <span className="truncate">{label}</span>
    </span>
  );
}

/**
 * Per-field provenance + confidence (Linear AIG).
 *
 * A numeric confidence is a machine artifact: the field value was proposed,
 * imported, or reconciled by an agent / source bridge, so we render a binary
 * `ConfidenceIndicator` (solid = confident, hatched = uncertain) alongside an
 * `AgentBadge` for the source — the design system treats source bridges and
 * automated reconciliation processes as agents.
 *
 * A field with no confidence score was asserted by a human, so it shows a
 * `HumanBadge` and carries no uncertainty meter (human authority is explicit).
 *
 * NOTE: when the backend exposes a `source_type` discriminator on contact
 * methods, a named human source can be split out from an agent bridge here;
 * today `source_label` + the presence of a confidence score is the only signal.
 */
export function ConfidenceBadge({
  confidence,
  source
}: {
  confidence?: number | null;
  source?: string | null;
}) {
  if (typeof confidence === "number") {
    return (
      <span className="inline-flex shrink-0 items-center gap-1.5">
        <ConfidenceIndicator value={confidence} size="sm" showLabel={false} />
        {source ? <AgentBadge slug={source} label={source} size="xs" /> : null}
      </span>
    );
  }
  return <HumanBadge name={source || "Manual entry"} label={source || "Manual"} size="xs" />;
}
