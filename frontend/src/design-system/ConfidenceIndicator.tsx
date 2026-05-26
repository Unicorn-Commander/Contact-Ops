import type { CSSProperties } from "react";
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  confidenceTiers,
  formatConfidence,
  getConfidenceTier,
  type ConfidenceTier,
  type CSSVarStyle
} from "@/design-system/tokens";

export interface ConfidenceIndicatorProps {
  value: number;
  mode?: "binary" | "numeric";
  size?: "sm" | "md";
  label?: string;
  showLabel?: boolean;
  disabled?: boolean;
  loading?: boolean;
  error?: boolean;
  className?: string;
}

const sizeClasses = {
  sm: {
    root: "h-6 gap-1.5 px-2 text-[11px]",
    meter: "h-3 w-7",
    icon: "h-3 w-3"
  },
  md: {
    root: "h-7 gap-2 px-2.5 text-xs",
    meter: "h-3.5 w-9",
    icon: "h-3.5 w-3.5"
  }
} as const;

const tierAria: Record<ConfidenceTier, string> = {
  high: "high confidence",
  good: "good confidence",
  review: "review required",
  low: "low confidence"
};

export function ConfidenceIndicator({
  value,
  mode = "binary",
  size = "sm",
  label,
  showLabel = true,
  disabled = false,
  loading = false,
  error = false,
  className
}: ConfidenceIndicatorProps) {
  const tier = error ? "low" : getConfidenceTier(value);
  const config = confidenceTiers[tier];
  const classes = sizeClasses[size];
  const style = {
    "--confidence-color": config.cssVar,
    backgroundColor: "oklch(var(--confidence-color) / 0.1)",
    borderColor: "oklch(var(--confidence-color) / 0.45)"
  } satisfies CSSVarStyle & CSSProperties;
  const meterStyle = {
    backgroundColor: "oklch(var(--confidence-color) / 0.18)",
    borderColor: "oklch(var(--confidence-color) / 0.42)"
  } satisfies CSSProperties;
  const certaintyLabel = label ?? config.label;
  const detail = `${tierAria[tier]} at ${formatConfidence(value)}`;
  const isHatched = mode === "binary" && !config.solid;

  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full border font-medium leading-none text-foreground",
        classes.root,
        disabled && "opacity-45",
        loading && "animate-pulse",
        className
      )}
      style={style}
      title={detail}
      role="img"
      aria-label={detail}
      data-confidence-tier={tier}
      data-confidence-mode={mode}
    >
      {mode === "binary" ? (
        <span
          className={cn(
            "relative inline-flex rounded-full border",
            classes.meter,
            isHatched && "co-confidence-hatched"
          )}
          style={meterStyle}
          aria-hidden="true"
        >
          <span
            className={cn("m-[2px] rounded-full", config.solid ? "w-full" : "w-1/2")}
            style={{ backgroundColor: "oklch(var(--confidence-color))" }}
          />
        </span>
      ) : (
        <span className="co-mono-numeric font-mono text-[0.92em]">{formatConfidence(value)}</span>
      )}
      {error ? (
        <AlertTriangle className={classes.icon} strokeWidth={1.6} aria-hidden="true" />
      ) : (
        <CheckCircle2 className={classes.icon} strokeWidth={1.6} aria-hidden="true" />
      )}
      {showLabel ? <span>{certaintyLabel}</span> : null}
    </span>
  );
}
