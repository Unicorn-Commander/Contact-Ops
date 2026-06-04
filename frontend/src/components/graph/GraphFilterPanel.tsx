import { Box, Monitor, Sparkles, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfidenceIndicator, MonoNumeric } from "@/design-system";
import type { GraphRenderMode } from "@/components/graph/GraphCanvas";
import { colorForNode } from "@/lib/graph/colorScheme";
import { cn } from "@/lib/utils";

/** Plain-language label for a raw node kind ("Organization" → "Organizations"). */
function kindLabel(kind: string) {
  const map: Record<string, string> = {
    Person: "People",
    Organization: "Organizations",
    Agent: "Agents",
    Topic: "Topics",
    Tag: "Tags",
    Email: "Emails",
    Phone: "Phones",
    Entity: "Other"
  };
  return map[kind] ?? kind;
}

/** A small color dot matching the node's render color, for the type toggles. */
function KindDot({ kind }: { kind: string }) {
  return (
    <span
      aria-hidden
      className="h-2 w-2 shrink-0 rounded-full"
      style={{ backgroundColor: colorForNode(kind) }}
    />
  );
}

export function GraphFilterPanel({
  hopLimit,
  confidenceFloor,
  renderMode,
  availableKinds,
  hiddenKinds,
  onHopLimitChange,
  onConfidenceFloorChange,
  onRenderModeChange,
  onToggleKind
}: {
  hopLimit: number;
  confidenceFloor: number;
  renderMode: GraphRenderMode;
  /** Node kinds present in the current graph (e.g. ["Person","Organization"]). */
  availableKinds: string[];
  /** Kinds the user has hidden (filtered out). */
  hiddenKinds: Set<string>;
  onHopLimitChange: (value: number) => void;
  onConfidenceFloorChange: (value: number) => void;
  onRenderModeChange: (value: GraphRenderMode) => void;
  onToggleKind: (kind: string) => void;
}) {
  const modeButtons: Array<{ value: GraphRenderMode; label: string; icon: LucideIcon }> = [
    { value: "auto", label: "Auto", icon: Sparkles },
    { value: "3d", label: "3D", icon: Box },
    { value: "2d", label: "2D", icon: Monitor }
  ];

  return (
    <section className="space-y-4 border-t border-border pt-3">
      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Refine the view</p>

      {/* Reach (hop depth) — plain language instead of "hop limit". */}
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-foreground">How far to reach</p>
            <p className="text-xs text-muted-foreground">Direct contacts, or one step further out.</p>
          </div>
          <MonoNumeric tone="muted">{hopLimit === 1 ? "1 hop" : "2 hops"}</MonoNumeric>
        </div>
        <div className="inline-flex rounded-md border border-border bg-background p-1 shadow-[var(--shadow-1)]">
          {[
            { value: 1, label: "Direct" },
            { value: 2, label: "Extended" }
          ].map((option) => {
            const active = hopLimit === option.value;
            return (
              <Button
                key={option.value}
                type="button"
                variant={active ? "default" : "ghost"}
                size="sm"
                onClick={() => onHopLimitChange(option.value)}
                className={cn("h-8 gap-1.5 px-3 text-xs", active && "shadow-none")}
              >
                {option.label}
              </Button>
            );
          })}
        </div>
      </div>

      {/* Confidence floor. */}
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-foreground">Relationship strength</p>
            <p className="text-xs text-muted-foreground">Hide weaker, lower-confidence ties.</p>
          </div>
          <ConfidenceIndicator value={confidenceFloor} mode="numeric" size="sm" />
        </div>
        <input
          className="w-full accent-primary"
          type="range"
          min={0}
          max={0.95}
          step={0.05}
          value={confidenceFloor}
          onChange={(event) => onConfidenceFloorChange(Number(event.target.value))}
          aria-label="Relationship strength floor"
        />
        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          <span>Show everything</span>
          <span>Strongest only</span>
        </div>
      </div>

      {/* Node-type filter — only shown when the graph actually has >1 kind. */}
      {availableKinds.length > 1 ? (
        <div className="space-y-2">
          <p className="text-sm font-medium text-foreground">Show node types</p>
          <div className="flex flex-wrap gap-1.5">
            {availableKinds.map((kind) => {
              const hidden = hiddenKinds.has(kind);
              return (
                <button
                  key={kind}
                  type="button"
                  aria-pressed={!hidden}
                  onClick={() => onToggleKind(kind)}
                  className={cn(
                    "focus-ring inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
                    hidden
                      ? "border-border bg-background text-muted-foreground line-through decoration-muted-foreground/60"
                      : "border-primary/30 bg-primary/10 text-foreground"
                  )}
                >
                  <KindDot kind={kind} />
                  {kindLabel(kind)}
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      {/* Render mode. */}
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-foreground">Display</p>
            <p className="text-xs text-muted-foreground">3D orbit, flat 2D, or auto for this device.</p>
          </div>
        </div>
        <div className="inline-flex rounded-md border border-border bg-background p-1 shadow-[var(--shadow-1)]">
          {modeButtons.map((mode) => {
            const Icon = mode.icon;
            const active = renderMode === mode.value;
            return (
              <Button
                key={mode.value}
                type="button"
                variant={active ? "default" : "ghost"}
                size="sm"
                onClick={() => onRenderModeChange(mode.value)}
                className={cn("h-8 gap-1.5 px-2.5 text-xs", active && "shadow-none")}
              >
                <Icon className="h-3.5 w-3.5" strokeWidth={1.7} />
                {mode.label}
              </Button>
            );
          })}
        </div>
      </div>
    </section>
  );
}
