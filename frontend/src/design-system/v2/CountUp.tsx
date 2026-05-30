/**
 * CountUp — animated stat count-up for the v2 dashboard hero.
 *
 * Counts from 0 → `value` once, on first scroll-into-view, with an ease-out
 * curve. Respects prefers-reduced-motion: when set, it renders the final value
 * immediately and never animates. Uses a single rAF loop with a ref-held DOM
 * write target where possible, but keeps a React state value for the formatted
 * output so it stays declarative and SSR-safe.
 *
 * Numbers render with tabular-nums (.co-mono-numeric) so the width doesn't jitter
 * as digits tick.
 */
import { useEffect, useRef, useState } from "react";
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
  const ref = useRef<HTMLSpanElement>(null);
  const started = useRef(false);

  useEffect(() => {
    if (reduce) {
      setDisplay(value);
      return;
    }
    const node = ref.current;
    if (!node) return;

    let raf = 0;
    let startTs = 0;

    const run = () => {
      const step = (ts: number) => {
        if (!startTs) startTs = ts;
        const t = Math.min((ts - startTs) / durationMs, 1);
        setDisplay(value * easeOut(t));
        if (t < 1) raf = requestAnimationFrame(step);
      };
      raf = requestAnimationFrame(step);
    };

    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting) && !started.current) {
          started.current = true;
          run();
        }
      },
      { threshold: 0.4 }
    );
    io.observe(node);

    return () => {
      io.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
  }, [value, durationMs, reduce]);

  const formatted = display.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals
  });

  return (
    <span ref={ref} className={cn("co-mono-numeric tabular-nums", className)}>
      {prefix}
      {formatted}
      {suffix}
    </span>
  );
}
