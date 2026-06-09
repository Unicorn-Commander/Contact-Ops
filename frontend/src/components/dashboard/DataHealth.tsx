/**
 * Data-health widget — at-a-glance record completeness for the right rail.
 *
 * Renders a few coverage bars ("82% have email") plus a compact "needs
 * attention" line ("14 missing phone · 6 no organization"). Coverage values are
 * 0..1. When no metrics are available it degrades to a short placeholder rather
 * than rendering empty bars.
 */
import { ShieldCheck } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export type CoverageTone = "emerald" | "sky" | "amber";

const toneVar: Record<CoverageTone, string> = {
  emerald: "var(--co-emerald-500)",
  sky: "var(--co-sky-500)",
  amber: "var(--co-amber-500)"
};

export type CoverageMetric = {
  label: string;
  /** 0..1 share of records that have this field. */
  value: number;
  tone?: CoverageTone;
};

export type GapMetric = {
  label: string;
  count: number;
};

export interface DataHealthProps {
  coverage: CoverageMetric[];
  gaps?: GapMetric[];
  /** Optional honest footnote, e.g. "Across a sample of 100 of 823 contacts". */
  note?: string;
  /** Render skeleton bars while coverage is being computed. */
  isLoading?: boolean;
  className?: string;
}

function pickTone(value: number): CoverageTone {
  if (value >= 0.8) return "emerald";
  if (value >= 0.5) return "sky";
  return "amber";
}

function CoverageRow({ label, value, tone }: CoverageMetric) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const resolved = tone ?? pickTone(value);
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="co-mono-numeric font-medium tabular-nums" style={{ color: `oklch(${toneVar[resolved]})` }}>
          {pct}%
        </span>
      </div>
      <span className="block h-1.5 w-full overflow-hidden rounded-full bg-muted" aria-hidden>
        <span
          className="block h-full rounded-full transition-[width] duration-300"
          style={{ width: `${pct}%`, background: `oklch(${toneVar[resolved]})` }}
        />
      </span>
    </div>
  );
}

export function DataHealth({ coverage, gaps = [], note, isLoading = false, className }: DataHealthProps) {
  const hasData = coverage.length > 0;
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ShieldCheck className="h-4 w-4 text-[oklch(var(--co-emerald-500))]" strokeWidth={1.8} />
          Data health
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3.5">
        {isLoading ? (
          <div className="space-y-3.5" aria-hidden>
            {Array.from({ length: 2 }).map((_, i) => (
              <div key={i} className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className="h-3 w-24 rounded bg-muted" />
                  <span className="h-3 w-8 rounded bg-muted" />
                </div>
                <span className="block h-1.5 w-full rounded-full bg-muted" />
              </div>
            ))}
          </div>
        ) : hasData ? (
          <>
            {coverage.map((c) => (
              <CoverageRow key={c.label} {...c} />
            ))}
            {gaps.length ? (
              <p className="flex flex-wrap items-center gap-x-1 gap-y-0.5 border-t pt-3 text-xs text-muted-foreground">
                {gaps.map((g, i) => (
                  <span key={g.label} className="inline-flex items-center gap-1">
                    {i > 0 ? <span className="text-border">·</span> : null}
                    <span className="co-mono-numeric font-medium tabular-nums text-foreground">{g.count}</span>
                    <span>{g.label}</span>
                  </span>
                ))}
              </p>
            ) : null}
            {note ? <p className="border-t pt-3 text-[11px] leading-relaxed text-muted-foreground/80">{note}</p> : null}
          </>
        ) : (
          <p className={cn("text-xs leading-relaxed text-muted-foreground")}>
            Coverage metrics — email and organization — appear here once you have contacts. Import or add people to see
            where the registry is thin.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
