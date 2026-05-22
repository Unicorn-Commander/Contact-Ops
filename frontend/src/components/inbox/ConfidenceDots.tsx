/**
 * ConfidenceDots — 5-dot scale + numeric + verbal tag.
 *
 * Bands (per Aaron's `feedback_confidence_tags_legal_work` + UI prompt
 * 'Confidence display'):
 *   ≥ 0.90  emerald-500  "high" / "verified"
 *   ≥ 0.75  sky-500      "documented"
 *   0.50-.74 amber-500   "medium"
 *   < 0.50  rose-500     "low" / "uncertain"
 *
 * Rose, not red. Rose reads "uncertain"; red reads "broken."
 *
 * Reusable across UC apps — keep the API minimal.
 */
import { cn } from "@/lib/utils";

export type ConfidenceBand = "high" | "documented" | "medium" | "low";

export type ConfidenceVisuals = {
  band: ConfidenceBand;
  /** Tailwind background class, e.g. "bg-emerald-500" */
  fill: string;
  /** Tailwind text class, e.g. "text-emerald-500" */
  text: string;
  /** Human label */
  label: string;
};

export function visualsFor(value: number): ConfidenceVisuals {
  if (value >= 0.9) return { band: "high", fill: "bg-emerald-500", text: "text-emerald-500", label: "verified" };
  if (value >= 0.75) return { band: "documented", fill: "bg-sky-500", text: "text-sky-500", label: "documented" };
  if (value >= 0.5) return { band: "medium", fill: "bg-amber-500", text: "text-amber-500", label: "medium" };
  return { band: "low", fill: "bg-rose-500", text: "text-rose-500", label: "uncertain" };
}

export type ConfidenceDotsProps = {
  value: number;
  /** Show numeric value next to dots. Default true. */
  showNumeric?: boolean;
  /** Show verbal tag (verified/documented/medium/uncertain) next to dots. Default true. */
  showLabel?: boolean;
  /** Dot size — "sm" for 6px, "md" for 8px. Default "md". */
  size?: "sm" | "md";
  className?: string;
};

export function ConfidenceDots({
  value,
  showNumeric = true,
  showLabel = true,
  size = "md",
  className,
}: ConfidenceDotsProps) {
  const visuals = visualsFor(value);
  const filled = Math.max(0, Math.min(5, Math.round(value * 5)));
  const dotSize = size === "sm" ? "h-1.5 w-1.5" : "h-2 w-2";
  const ariaLabel = `Confidence ${value.toFixed(2)}, ${visuals.label}`;

  return (
    <span
      className={cn("inline-flex items-center gap-1.5", className)}
      role="img"
      aria-label={ariaLabel}
    >
      <span className="inline-flex items-center gap-0.5">
        {Array.from({ length: 5 }, (_, i) => (
          <span
            key={i}
            className={cn(
              "rounded-full",
              dotSize,
              i < filled ? visuals.fill : "bg-muted",
            )}
            aria-hidden="true"
          />
        ))}
      </span>
      {showNumeric && (
        <span className="font-mono text-xs tabular-nums text-foreground/80">
          {value.toFixed(2)}
        </span>
      )}
      {showLabel && (
        <span className={cn("text-xs", visuals.text)}>{visuals.label}</span>
      )}
    </span>
  );
}
