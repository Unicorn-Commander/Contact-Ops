/**
 * Living command-center stat tile.
 *
 * Keeps the canonical semantic-accent skin (a tinted `ring-1` + a tinted lucide
 * icon, big tabular number) and layers on "living data": an optional trend
 * delta ("▲ +18 this week") and a tiny inline sparkline tinted with the same
 * accent. Presentational only — drilling is handled by the caller wrapping it
 * in a typed <Link> (route types differ between the live page and the preview).
 */
import type { LucideIcon } from "lucide-react";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { Sparkline } from "./Sparkline";

export type StatTone = "fuchsia" | "sky" | "amber" | "emerald";

export const statToneClasses: Record<StatTone, { tile: string; icon: string; accent: string }> = {
  fuchsia: {
    tile: "ring-[oklch(var(--co-brand-500)/0.22)]",
    icon: "text-primary",
    accent: "text-primary"
  },
  sky: {
    tile: "ring-[oklch(var(--co-sky-500)/0.22)]",
    icon: "text-[oklch(var(--co-sky-500))]",
    accent: "text-[oklch(var(--co-sky-500))]"
  },
  amber: {
    tile: "ring-[oklch(var(--co-amber-500)/0.22)]",
    icon: "text-[oklch(var(--co-amber-500))]",
    accent: "text-[oklch(var(--co-amber-500))]"
  },
  emerald: {
    tile: "ring-[oklch(var(--co-emerald-500)/0.22)]",
    icon: "text-[oklch(var(--co-emerald-500))]",
    accent: "text-[oklch(var(--co-emerald-500))]"
  }
};

export type StatTrend = {
  /** Signed change for the period (e.g. +18). 0 renders a neutral "no change". */
  delta: number;
  /** Period label, e.g. "this week". */
  period: string;
  /** 7-ish point series, oldest → newest, for the sparkline. */
  series?: number[];
  /** When true a positive delta is bad (e.g. duplicates) → flips delta coloring. */
  invert?: boolean;
};

export interface StatTileProps {
  label: string;
  value: string | number;
  icon: LucideIcon;
  tone: StatTone;
  hint?: string;
  trend?: StatTrend;
  /** Shows the drill chevron + interactive lift (caller still owns the Link). */
  interactive?: boolean;
  className?: string;
}

function formatDelta(delta: number) {
  if (delta === 0) return "no change";
  const sign = delta > 0 ? "+" : "−";
  return `${sign}${Math.abs(delta).toLocaleString()}`;
}

export function StatTile({ label, value, icon: Icon, tone, hint, trend, interactive, className }: StatTileProps) {
  const t = statToneClasses[tone];
  const delta = trend?.delta ?? 0;
  // A positive delta is "good" unless the metric is inverted (e.g. duplicates).
  const positive = trend?.invert ? delta < 0 : delta > 0;
  const TrendIcon = delta === 0 ? Minus : delta > 0 ? ArrowUpRight : ArrowDownRight;
  const trendColor =
    delta === 0 ? "text-muted-foreground" : positive ? "text-[oklch(var(--co-emerald-500))]" : "text-[oklch(var(--co-rose-500))]";
  const direction = delta === 0 ? "unchanged" : delta > 0 ? "up" : "down";

  return (
    <Card
      className={cn(
        "h-full ring-1",
        t.tile,
        interactive && "transition-all duration-150 group-hover:-translate-y-0.5 group-hover:border-primary/40 group-hover:shadow-[var(--shadow-2)]",
        className
      )}
    >
      <CardContent className="p-5">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
          <Icon className={cn("h-4 w-4", t.icon)} strokeWidth={1.8} />
        </div>
        <div className="mt-2.5 flex items-end justify-between gap-2">
          <p className="text-3xl font-semibold tabular-nums tracking-tight">{value}</p>
          {trend?.series && trend.series.length > 1 ? (
            <Sparkline data={trend.series} className={cn("mb-1 shrink-0", t.accent)} />
          ) : null}
        </div>
        {trend ? (
          <p className="mt-1.5 flex items-center gap-1 text-xs">
            <span className={cn("inline-flex items-center gap-0.5 font-medium tabular-nums", trendColor)}>
              <TrendIcon className="h-3 w-3" strokeWidth={2} aria-hidden />
              <span aria-hidden>{formatDelta(delta)}</span>
              <span className="sr-only">
                {direction} {formatDelta(delta)}
              </span>
            </span>
            <span className="text-muted-foreground">{trend.period}</span>
          </p>
        ) : hint ? (
          <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}
