/**
 * CountUp — animated stat count-up for the v2 dashboard hero.
 *
 * Animates 0 → `value` on mount and on every value change, with an ease-out
 * curve. Correctness is guaranteed *independent* of the animation: a settle
 * timer always snaps the display to the exact `value` once the duration has
 * elapsed, so even if requestAnimationFrame is throttled — a backgrounded tab,
 * or a busy main thread from the ambient-graph canvas — the number can never
 * freeze on a partial value (the bug that left the hero stuck at ~10% of the
 * real count). Respects prefers-reduced-motion: renders the final value
 * immediately, no animation.
 *
 * Numbers render with tabular-nums (.co-mono-numeric) so the width doesn't
 * jitter as digits tick.
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
  durationMs = 900,
  decimals = 0,
  prefix = "",
  suffix = "",
  className
}: CountUpProps) {
  const reduce = prefersReducedMotion();
  const [display, setDisplay] = useState(() => (reduce ? value : 0));

  useEffect(() => {
    if (reduce || durationMs <= 0) {
      setDisplay(value);
      return;
    }
    // Animate 0 → value. Re-runs whenever value changes (a tile that mounted at
    // 0 ticks up once the real count loads). No scroll-into-view gating — these
    // are above-the-fold hero tiles.
    let raf = 0;
    let start = 0;
    const step = (now: number) => {
      if (!start) start = now;
      const t = Math.min((now - start) / durationMs, 1);
      setDisplay(value * easeOut(t));
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    // Correctness guarantee, decoupled from rAF: settle on the exact value once
    // the duration has elapsed. setTimeout still fires when rAF is paused
    // (hidden/throttled tab), so the count can never get stuck mid-animation.
    const settle = window.setTimeout(() => setDisplay(value), durationMs + 160);

    return () => {
      if (raf) cancelAnimationFrame(raf);
      window.clearTimeout(settle);
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
