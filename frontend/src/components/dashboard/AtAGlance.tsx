/**
 * AI "at a glance" card — the dashboard's executive summary (Crisis-Ops lineage).
 *
 * A Sparkles-marked, dismissible hero that distills the registry into a single
 * natural-language line plus a row of clickable highlight chips (people, orgs,
 * likely-duplicates, stale records, pending reviews). It is deliberately
 * restrained: a flat card with a brand-tinted icon, NOT a gradient wash. When
 * there is no data / no AISignal yet it degrades to an honest placeholder so the
 * card is never an error or a void.
 */
import type { LucideIcon } from "lucide-react";
import { Sparkles, X } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export type GlanceTone = "fuchsia" | "sky" | "amber" | "emerald" | "rose" | "neutral";

const toneText: Record<GlanceTone, string> = {
  fuchsia: "text-primary",
  sky: "text-[oklch(var(--co-sky-500))]",
  amber: "text-[oklch(var(--co-amber-500))]",
  emerald: "text-[oklch(var(--co-emerald-500))]",
  rose: "text-[oklch(var(--co-rose-500))]",
  neutral: "text-muted-foreground"
};

export type GlanceHighlight = {
  id: string;
  icon: LucideIcon;
  /** Big figure, e.g. "1,284" or "3". */
  value: string | number;
  /** Short label, e.g. "people" / "likely duplicates". */
  label: string;
  tone: GlanceTone;
  /** When set the chip becomes a button that drills into the surface. */
  onClick?: () => void;
};

export interface AtAGlanceProps {
  /** One-line natural-language summary. Omit to show the placeholder line. */
  summary?: string;
  highlights: GlanceHighlight[];
  /** Subtitle context, e.g. "Updated just now · synthesized from your registry". */
  context?: string;
  /** True → render the graceful "no signal yet" placeholder body. */
  placeholder?: boolean;
  onDismiss?: () => void;
  className?: string;
}

function HighlightChip({ icon: Icon, value, label, tone, onClick }: GlanceHighlight) {
  const inner = (
    <>
      <Icon className={cn("h-4 w-4 shrink-0", toneText[tone])} strokeWidth={1.8} aria-hidden />
      <span className="co-mono-numeric text-sm font-semibold tabular-nums text-foreground">{value}</span>
      <span className="truncate text-xs text-muted-foreground">{label}</span>
    </>
  );
  const base = "flex items-center gap-1.5 rounded-md border bg-background/40 px-2.5 py-1.5";
  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={cn(base, "focus-ring text-left transition-colors hover:border-primary/40 hover:bg-muted")}
        aria-label={`${value} ${label}`}
      >
        {inner}
      </button>
    );
  }
  return (
    <span className={base} aria-label={`${value} ${label}`}>
      {inner}
    </span>
  );
}

export function AtAGlance({ summary, highlights, context, placeholder = false, onDismiss, className }: AtAGlanceProps) {
  return (
    <Card className={cn("relative overflow-hidden", className)}>
      {/* Faint brand wash anchored to the corner — a hint of AI, not a gradient surface. */}
      <span
        aria-hidden
        className="pointer-events-none absolute -right-12 -top-12 h-32 w-32 rounded-full bg-primary/10 blur-2xl"
      />
      <div className="relative p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Sparkles className="h-4 w-4" strokeWidth={1.8} />
            </span>
            <div className="min-w-0">
              <h2 className="text-base font-semibold leading-none">At a glance</h2>
              <p className="mt-1 truncate text-xs text-muted-foreground">
                {context ?? "Synthesized from your registry"}
              </p>
            </div>
          </div>
          {onDismiss ? (
            <button
              type="button"
              onClick={onDismiss}
              className="focus-ring -mr-1 -mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              aria-label="Dismiss at-a-glance summary"
              title="Dismiss"
            >
              <X className="h-4 w-4" strokeWidth={1.8} />
            </button>
          ) : null}
        </div>

        {placeholder ? (
          <p className="mt-3.5 max-w-2xl text-sm leading-relaxed text-muted-foreground">
            Your AI summary appears here once there's enough activity to synthesize. Add or import contacts and let the
            agents survey the registry — highlights like likely duplicates and stale records will surface automatically.
          </p>
        ) : (
          <p className="mt-3.5 max-w-3xl text-sm leading-relaxed text-foreground/90">{summary}</p>
        )}

        {highlights.length ? (
          <div className="mt-4 flex flex-wrap gap-2">
            {highlights.map((h) => (
              <HighlightChip key={h.id} {...h} />
            ))}
          </div>
        ) : null}
      </div>
    </Card>
  );
}
