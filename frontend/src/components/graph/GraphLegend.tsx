import { AgentBadge, ActionAttribution, ConfidenceIndicator, HumanBadge, MonoNumeric } from "@/design-system";

function EdgeSwatch({
  tone,
  dashed = false
}: {
  tone: "info" | "confidence-amber";
  dashed?: boolean;
}) {
  return (
    <span
      className={
        dashed
          ? `h-0.5 w-8 border-t border-dashed ${tone === "info" ? "text-info" : "text-confidence-amber"} border-current`
          : `${tone === "info" ? "bg-info" : "bg-confidence-amber"} h-0.5 w-8 rounded-full`
      }
      aria-hidden="true"
    />
  );
}

export function GraphLegend() {
  return (
    <section className="space-y-3 border-t border-border pt-3 text-sm">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Nodes</p>
          <div className="space-y-2 rounded-md border border-border bg-background/70 p-3">
            <div className="flex items-center gap-2">
              <HumanBadge name="person" label="Person" size="xs" />
              <span className="text-foreground">Person and org nodes carry the ego network.</span>
            </div>
            <div className="flex items-center gap-2">
              <AgentBadge slug="graph-sync-worker" label="graph sync" size="xs" />
              <span className="text-foreground">Agent nodes are rendered as explicit machine actors.</span>
            </div>
          </div>
        </div>

        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Edges</p>
          <div className="space-y-2 rounded-md border border-border bg-background/70 p-3">
            <div className="flex items-center gap-2">
              <EdgeSwatch tone="info" />
              <span className="text-foreground">Human-asserted relationship</span>
            </div>
            <div className="flex items-center gap-2">
              <EdgeSwatch tone="confidence-amber" dashed />
              <span className="text-foreground">Agent-proposed or propose_only edge</span>
            </div>
            <div className="flex items-center gap-2">
              <ConfidenceIndicator value={0.92} mode="binary" size="sm" showLabel={false} />
              <span className="text-foreground">Solid means stronger evidence. Hatched means review energy.</span>
            </div>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-background/70 px-3 py-2 text-xs text-muted-foreground">
        <ActionAttribution
          actor={{ type: "agent", slug: "graph-sync-worker", label: "Graph Sync Worker" }}
          verb="projects"
          timestamp={new Date()}
          target="the tenant graph"
          compact
        />
        <span className="inline-flex items-center gap-1">
          <MonoNumeric tone="muted">AIG</MonoNumeric>
          <span>edges from agents are visually distinct from human-asserted lines.</span>
        </span>
      </div>
    </section>
  );
}
