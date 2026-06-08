import type { LucideIcon } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * Semantic accent tones for the command-center stat tile. Mirrors the tone map
 * in routes/pages/Dashboard.tsx so directory stat strips read as the same family
 * of KPI tiles — a faint ring + a tinted lucide icon — without re-implementing
 * the Dashboard's local copy. Brand/fuchsia is the primary metric; the others
 * carry the ecosystem's emerald/sky/amber/rose semantics.
 */
export type StatTone = "brand" | "fuchsia" | "sky" | "amber" | "emerald" | "rose";

const toneClasses: Record<StatTone, { ring: string; icon: string }> = {
  brand: { ring: "ring-[oklch(var(--co-brand-500)/0.22)]", icon: "text-primary" },
  fuchsia: { ring: "ring-[oklch(var(--accent-fuchsia)/0.24)]", icon: "text-[oklch(var(--accent-fuchsia))]" },
  sky: { ring: "ring-[oklch(var(--co-sky-500)/0.22)]", icon: "text-[oklch(var(--co-sky-500))]" },
  amber: { ring: "ring-[oklch(var(--co-amber-500)/0.22)]", icon: "text-[oklch(var(--co-amber-500))]" },
  emerald: { ring: "ring-[oklch(var(--co-emerald-500)/0.22)]", icon: "text-[oklch(var(--co-emerald-500))]" },
  rose: { ring: "ring-[oklch(var(--co-rose-500)/0.22)]", icon: "text-[oklch(var(--co-rose-500))]" }
};

/**
 * A single overview metric tile: an uppercase label, a tinted semantic icon, a
 * big tabular number, and an optional hint line. Matches the Dashboard Stat
 * exactly so directory headers feel like the same command center.
 */
export function StatTile({
  label,
  value,
  icon: Icon,
  tone,
  hint
}: {
  label: string;
  value: string | number;
  icon: LucideIcon;
  tone: StatTone;
  hint?: string;
}) {
  const t = toneClasses[tone];
  return (
    <Card className={cn("h-full ring-1", t.ring)}>
      <CardContent className="p-5">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
          <Icon className={cn("h-4 w-4", t.icon)} strokeWidth={1.8} />
        </div>
        <p
          className={cn(
            "mt-2.5 text-3xl font-semibold tabular-nums tracking-tight",
            // A zero reads as a calm "none yet", not a stark broken-looking 0.
            value === 0 || value === "0" ? "text-muted-foreground/50" : "text-foreground"
          )}
        >
          {value}
        </p>
        {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
      </CardContent>
    </Card>
  );
}

/** Loading twin of {@link StatTile} for the stat strip. */
export function StatTileSkeleton() {
  return <Skeleton className="h-[104px] w-full rounded-lg" />;
}

/**
 * The responsive 3-up overview row used above directory lists. Children are
 * {@link StatTile}s; the strip collapses to a single column on mobile.
 */
export function StatStrip({ children }: { children: React.ReactNode }) {
  return <div className="grid gap-4 sm:grid-cols-3">{children}</div>;
}
