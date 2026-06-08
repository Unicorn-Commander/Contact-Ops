/**
 * CountUp — animated stat count-up for the v2 dashboard hero.
 *
 * Counts from 0 → `value` with an ease-out curve, animating on mount AND again
 * whenever `value` changes (the dashboard tiles mount at 0 and update once the
 * counts load in). Respects prefers-reduced-motion: renders the final value
 * immediately, no animation.
 *
 * Numbers render with tabular-nums (.co-mono-numeric) so the width doesn't jitter
 * as digits tick.
 */
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

export interface CountUpProps {
  value: number;
  /** Animation duration in ms. */
  durationMs?: number;
  /** Decimal places to render. */
  decimals?: number;
  prefix?: string;
  suffix?: string;
  className?: string;
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);

export function CountUp({
  value,
  durationMs = 1100,
  decimals = 0,
  prefix = "",
  suffix = "",
  className
}: CountUpProps) {
  const reduce = prefersReducedMotion();
  const [display, setDisplay] = useState(() => (reduce ? value : 0));

  useEffect(() => {
    if (reduce) {
      setDisplay(value);
      return;
    }
    // Animate 0 → value. Re-runs whenever value changes, so a tile that mounted
    // at 0 (data not loaded) ticks up once the real count arrives. No
    // scroll-into-view gating — these are above-the-fold hero tiles.
    let raf = 0;
    let startTs = 0;
    const step = (ts: number) => {
      if (!startTs) startTs = ts;
      const t = Math.min((ts - startTs) / durationMs, 1);
      setDisplay(value * easeOut(t));
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);

    return () => {
      if (raf) cancelAnimationFrame(raf);
    };
  }, [value, durationMs, reduce]);

  const formatted = display.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals
  });

  return (
    <span className={cn("co-mono-numeric tabular-nums", className)}>
      {prefix}
      {formatted}
      {suffix}
    </span>
  );
}
