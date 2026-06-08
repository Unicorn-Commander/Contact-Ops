/**
 * ConfidenceMeterV2 — calm confidence treatment for dense data surfaces.
 *
 * Previews the Review-Queue redesign's confidence read. Deliberately quiet: a
 * 4-segment meter that fills by tier + a tabular percentage + a tier word. No
 * bloom, no blur, no animation on the data surface — it must stay scannable in
 * a long list. Reuses the canonical tier logic (getConfidenceTier /
 * confidenceTiers / formatConfidence) so v2 never drifts from the token system.
 *
 * Color carries meaning but never alone: the percentage + label + segment count
 * all encode the tier, so it survives color-blindness and the AA bar.
 */
import type { CSSProperties } from "react";
import { cn } from "@/lib/utils";
import {
  confidenceTiers,
  formatConfidence,
  getConfidenceTier,
  type ConfidenceTier
} from "@/design-system/tokens";

export interface ConfidenceMeterV2Props {
  /** Confidence 0–1, or null/undefined for an unscored proposal. */
  value: number | null | undefined;
  /** Show the tier word ("High" / "Good" / …). */
  showLabel?: boolean;
  className?: string;
}

/** How many of the 4 segments light up per tier. */
const filledSegments: Record<ConfidenceTier, number> = {
  high: 4,
  good: 3,
  review: 2,
  low: 1
};

export function ConfidenceMeterV2({ value, showLabel = true, className }: ConfidenceMeterV2Props) {
  // Unscored proposals read as a calm neutral chip, NOT an alarming red
  // "0% · Low". In this system an absent score serializes to 0 (raw contact
  // imports the agent never graded), so 0 / null / undefined all mean
  // "no score yet" — honest, and it stops the whole queue looking like junk.
  // Any real, positive score (even a low 0.2) still renders its true tier.
  if (value == null || Number.isNaN(value) || value <= 0) {
    return (
      <span
        className={cn(
          "inline-flex shrink-0 items-center gap-2 rounded-full border border-border bg-muted/50 px-2.5 py-1 text-[11px] font-medium leading-none text-muted-foreground",
          className
        )}
        role="img"
        aria-label="Unscored — no confidence yet"
        title="Unscored — no confidence score yet"
        data-confidence-tier="unscored"
      >
        <span className="flex items-end gap-[2px]" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => (
            <span
              key={i}
              className="w-[3px] rounded-[1px] bg-muted-foreground/25"
              style={{ height: `${5 + i * 2}px` }}
            />
          ))}
        </span>
        {showLabel ? <span>Unscored</span> : <span aria-hidden>—</span>}
      </span>
    );
  }

  const tier = getConfidenceTier(value);
  const config = confidenceTiers[tier];
  const lit = filledSegments[tier];
  const detail = `${config.label} confidence, ${formatConfidence(value)}`;

  const style = { "--confidence-color": config.cssVar } as CSSProperties;

  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-2 rounded-full border px-2.5 py-1 text-[11px] font-medium leading-none",
        className
      )}
      style={{
        ...style,
        backgroundColor: "oklch(var(--confidence-color) / 0.1)",
        borderColor: "oklch(var(--confidence-color) / 0.4)",
        color: "oklch(var(--foreground))"
      }}
      role="img"
      aria-label={detail}
      title={detail}
      data-confidence-tier={tier}
    >
      <span className="flex items-end gap-[2px]" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <span
            key={i}
            className="w-[3px] rounded-[1px]"
            style={{
              height: `${5 + i * 2}px`,
              backgroundColor:
                i < lit ? "oklch(var(--confidence-color))" : "oklch(var(--confidence-color) / 0.22)"
            }}
          />
        ))}
      </span>
      <span className="co-mono-numeric font-mono tabular-nums">{formatConfidence(value)}</span>
      {showLabel ? <span className="text-muted-foreground">{config.label}</span> : null}
    </span>
  );
}
