import { useMemo } from "react";
import { AgentBadge } from "@/design-system/AgentBadge";
import { MonoNumeric } from "@/design-system/MonoNumeric";
import { cn } from "@/lib/utils";

type GraphLabel = {
  id: string;
  x: number;
  y: number;
  kind: "person" | "org" | "agent";
  label: string;
  weight: number;
};

const width = 4000;
const height = 2500;
const cols = 40;
const rows = 25;

function jitter(index: number, span: number) {
  const value = Math.sin(index * 12.9898) * 43758.5453;
  return (value - Math.floor(value) - 0.5) * span;
}

function makeLabels(): GraphLabel[] {
  return Array.from({ length: cols * rows }, (_, index) => {
    const column = index % cols;
    const row = Math.floor(index / cols);
    const kind = index % 9 === 0 ? "agent" : index % 4 === 0 ? "org" : "person";
    const x = 64 + column * ((width - 128) / (cols - 1)) + jitter(index, 18);
    const y = 64 + row * ((height - 128) / (rows - 1)) + jitter(index + 9, 18);
    return {
      id: `N-${String(index + 1).padStart(4, "0")}`,
      x,
      y,
      kind,
      label: kind === "org" ? `Org ${index + 1}` : kind === "agent" ? `agent-${index + 1}` : `Person ${index + 1}`,
      weight: 1 + ((index * 7) % 9)
    };
  });
}

function makeEdges(labels: GraphLabel[]) {
  return labels
    .filter((_, index) => index % 5 === 0)
    .map((label, index) => {
      const target = labels[(index * 19 + 37) % labels.length];
      return { id: `${label.id}-${target.id}`, source: label, target };
    });
}

export function GraphLabelStressTest() {
  const labels = useMemo(() => makeLabels(), []);
  const edges = useMemo(() => makeEdges(labels), [labels]);

  return (
    <section className="overflow-hidden rounded-[var(--radius-lg)] border border-border bg-background shadow-[var(--shadow-2)]">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-card px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-foreground">GraphLabelStressTest</h2>
          <p className="text-xs text-muted-foreground">
            <MonoNumeric tone="muted">{labels.length}</MonoNumeric> HTML labels over a static graph plane
          </p>
        </div>
        <AgentBadge slug="relationship-agent" size="xs" />
      </header>
      <div className="co-scrollbar h-[680px] overflow-auto bg-[radial-gradient(circle_at_50%_40%,oklch(var(--co-sky-950)_/_0.2),transparent_42%),oklch(var(--background))]">
        <div className="relative" style={{ width, height }}>
          <svg className="absolute inset-0 h-full w-full" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
            <defs>
              <radialGradient id="nodeGlow">
                <stop offset="0%" stopColor="oklch(0.68 0.15 226 / 0.55)" />
                <stop offset="100%" stopColor="oklch(0.68 0.15 226 / 0)" />
              </radialGradient>
            </defs>
            {edges.map((edge) => (
              <line
                key={edge.id}
                x1={edge.source.x}
                y1={edge.source.y}
                x2={edge.target.x}
                y2={edge.target.y}
                stroke="oklch(0.75 0.05 226 / 0.14)"
                strokeWidth="1"
              />
            ))}
            {labels
              .filter((label) => label.weight > 7)
              .map((label) => (
                <circle key={label.id} cx={label.x} cy={label.y} r={26 + label.weight * 2} fill="url(#nodeGlow)" />
              ))}
          </svg>
          {labels.map((label) => {
            const important = label.weight > 7;
            return (
              <div
                key={label.id}
                className={cn(
                  "absolute inline-flex max-w-28 -translate-x-1/2 -translate-y-1/2 items-center gap-1 rounded-full border px-2 py-1 font-mono text-[11px] leading-none backdrop-blur-[2px]",
                  label.kind === "person" && "border-[oklch(var(--co-emerald-500)_/_0.24)] bg-[oklch(var(--co-emerald-950)_/_0.18)] text-[oklch(var(--co-emerald-100))]",
                  label.kind === "org" && "border-[oklch(var(--co-sky-500)_/_0.24)] bg-[oklch(var(--co-sky-950)_/_0.2)] text-[oklch(var(--co-sky-100))]",
                  label.kind === "agent" && "border-[oklch(var(--co-brand-500)_/_0.3)] bg-[oklch(var(--co-brand-950)_/_0.24)] text-[oklch(var(--co-brand-100))]",
                  important ? "z-10 opacity-100 shadow-[var(--shadow-1)]" : "z-0 opacity-45"
                )}
                style={{
                  left: label.x,
                  top: label.y,
                  contain: "layout paint style",
                  transform: "translate3d(-50%, -50%, 0)"
                }}
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current" />
                <span className="truncate">{label.label}</span>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
