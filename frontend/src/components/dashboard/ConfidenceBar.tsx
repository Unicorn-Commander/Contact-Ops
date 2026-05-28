/**
 * Confidence as a small colored bar + percent pill, tier-mapped to the
 * canonical confidence ramp (emerald / sky / amber / rose via
 * design-system/tokens `confidenceTiers`). The track fills proportionally and
 * the percent reads in mono tabular figures so rows don't jitter.
 */
import { confidenceTiers, formatConfidence, getConfidenceTier } from "@/design-system/tokens";
import { cn } from "@/lib/utils";

export interface ConfidenceBarProps {
  /** 0..1 confidence. */
  value: number;
  className?: string;
}

export function ConfidenceBar({ value, className }: ConfidenceBarProps) {
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
