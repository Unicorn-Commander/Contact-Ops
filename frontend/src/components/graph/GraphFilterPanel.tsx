import { Box, Monitor, Shuffle, Sparkles, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfidenceIndicator, MonoNumeric } from "@/design-system";
import type { GraphRenderMode } from "@/components/graph/GraphCanvas";
import { cn } from "@/lib/utils";

export function GraphFilterPanel({
  hopLimit,
  confidenceFloor,
  renderMode,
  onHopLimitChange,
  onConfidenceFloorChange,
  onRenderModeChange
}: {
  hopLimit: number;
  confidenceFloor: number;
  renderMode: GraphRenderMode;
  onHopLimitChange: (value: number) => void;
  onConfidenceFloorChange: (value: number) => void;
  onRenderModeChange: (value: GraphRenderMode) => void;
}) {
  const modeButtons: Array<{ value: GraphRenderMode; label: string; icon: LucideIcon }> = [
    { value: "auto", label: "Auto", icon: Sparkles },
    { value: "3d", label: "3D", icon: Box },
    { value: "2d", label: "2D", icon: Monitor }
  ];

  return (
    <section className="space-y-4 border-t border-border pt-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Render mode</p>
          <p className="text-sm text-foreground">3D discovery with a low-power 2D fallback.</p>
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

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Hop limit</p>
          <MonoNumeric tone="muted">{hopLimit} hops</MonoNumeric>
        </div>
        <div className="inline-flex rounded-md border border-border bg-background p-1 shadow-[var(--shadow-1)]">
          {[1, 2].map((value) => {
            const active = hopLimit === value;
            return (
              <Button
                key={value}
                type="button"
                variant={active ? "default" : "ghost"}
                size="sm"
                onClick={() => onHopLimitChange(value)}
                className={cn("h-8 min-w-10 px-3 text-xs", active && "shadow-none")}
              >
                {value}
              </Button>
            );
          })}
        </div>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Confidence floor</p>
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
          aria-label="Confidence floor"
        />
        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          <span>Broader discovery</span>
          <span>Stronger evidence only</span>
        </div>
      </div>

      <div className="flex items-center gap-2 rounded-md border border-border bg-background/70 px-3 py-2 text-xs text-muted-foreground">
        <Shuffle className="h-3.5 w-3.5 shrink-0" />
        <span>Higher floors remove weaker ties before layout, keeping the cluster readable.</span>
      </div>
    </section>
  );
}
