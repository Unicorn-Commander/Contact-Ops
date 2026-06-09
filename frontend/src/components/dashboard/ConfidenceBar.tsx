/**
 * Confidence as a small colored bar + percent pill, tier-mapped to the
 * canonical confidence ramp (emerald / sky / amber / rose via
 * design-system/tokens `confidenceTiers`). The track fills proportionally and
 * the percent reads in mono tabular figures so rows don't jitter.
 */
import { confidenceTiers, formatConfidence, getConfidenceTier } from "@/design-system/tokens";
import { cn } from "@/lib/utils";

export interface ConfidenceBarProps {
  /** 0..1 confidence. `null`/`0` renders a neutral "Unscored" chip. */
  value: number | null | undefined;
  className?: string;
}

export function ConfidenceBar({ value, className }: ConfidenceBarProps) {
  // Unscored proposals serialize confidence as 0 (or null). A red "0% · Low"
  // makes a whole agent import read like junk, so render a calm neutral chip in
  // the same footprint — matches ConfidenceMeterV2 / ConfidenceIndicator.
  if (value == null || Number.isNaN(value) || value <= 0) {
    return (
      <span
        className={cn("inline-flex items-center gap-1.5 text-muted-foreground", className)}
        title="Unscored — the agent attached no confidence score"
        aria-label="Confidence unscored"
        data-confidence-tier="unscored"
      >
        <span className="h-1.5 w-10 overflow-hidden rounded-full bg-muted" aria-hidden>
          <span className="block h-full w-1/4 rounded-full bg-muted-foreground/25" />
        </span>
        <span className="co-mono-numeric w-9 text-right text-xs font-medium tabular-nums">—</span>
      </span>
    );
  }

  const clamped = Math.max(0, Math.min(1, value));
  const tier = getConfidenceTier(clamped);
  const { cssVar, label } = confidenceTiers[tier];
  const pct = formatConfidence(clamped);

  return (
    <span
      className={cn("inline-flex items-center gap-1.5", className)}
      title={`${label} confidence · ${pct}`}
      aria-label={`Confidence ${pct}, ${label}`}
    >
      <span className="h-1.5 w-10 overflow-hidden rounded-full bg-muted" aria-hidden>
        <span
          className="block h-full rounded-full"
          style={{ width: `${Math.round(clamped * 100)}%`, background: `oklch(${cssVar})` }}
        />
      </span>
      <span
        className="co-mono-numeric w-9 text-right text-xs font-medium tabular-nums"
        style={{ color: `oklch(${cssVar})` }}
      >
        {pct}
      </span>
    </span>
  );
}
