import { useEffect } from "react";
import { Network, ArrowRight, ChevronRight, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ActionAttribution, AgentBadge, ConfidenceIndicator, HumanBadge, MonoNumeric } from "@/design-system";
import { isProposeOnlyRelationship } from "@/lib/graph/colorScheme";
import type { EgoGraph, GraphLink, GraphNode } from "@/hooks/useEgoGraph";

function resolveNodeName(graph: EgoGraph | undefined, id: string) {
  return graph?.nodes.find((node) => node.id === id)?.name ?? id;
}

type LinkEndpoint = string | { id: string };

type GraphLinkWithEndpoints = Pick<GraphLink, "type" | "strength"> & {
  source?: LinkEndpoint;
  target?: LinkEndpoint;
};

function resolveLinkNodes(link: GraphLinkWithEndpoints) {
  const source = typeof link.source === "string" ? link.source : link.source?.id ?? "";
  const target = typeof link.target === "string" ? link.target : link.target?.id ?? "";
  return { source, target };
}

export function NodeDetailDrawer({
  node,
  graph,
  rootPersonId,
  snapshotAt,
  onExpand,
  onClose
}: {
  node: GraphNode | null;
  graph?: EgoGraph;
  rootPersonId?: string;
  snapshotAt: Date | null;
  onExpand: () => void;
  onClose: () => void;
}) {
  // Esc closes the drawer (standard for an overlay panel).
  useEffect(() => {
    if (!node) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [node, onClose]);

  const incidentLinks = node
    ? graph?.links.filter((link) => {
        const { source, target } = resolveLinkNodes(link as GraphLinkWithEndpoints);
        return source === node.id || target === node.id;
      }) ?? []
    : [];
  const displayLinks = incidentLinks.slice(0, 4);

  return (
    <aside className="absolute inset-x-3 bottom-3 z-20 max-h-[26svh] overflow-hidden rounded-lg border border-border bg-card/90 shadow-[var(--shadow-3)] backdrop-blur-xl md:inset-y-3 md:right-3 md:left-auto md:bottom-auto md:h-[calc(100svh-5.5rem)] md:max-h-none md:w-[22rem]">
      {node ? (
        <div className="flex h-full min-h-0 flex-col">
          <div className="space-y-4 border-b border-border px-4 py-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 space-y-2">
                <div className="flex items-center gap-2">
                  <h2 className="truncate text-lg font-semibold text-foreground">{node.name}</h2>
                  {node.id === rootPersonId ? <Badge variant="secondary">Ego anchor</Badge> : <Badge variant="outline">{node.kind}</Badge>}
                </div>
                <p className="text-sm text-muted-foreground">{node.kind} · tenant-scoped projection</p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={onClose}
                aria-label="Close details"
                title="Close (Esc)"
                className="h-9 w-9 shrink-0 text-muted-foreground hover:text-foreground"
              >
                <X className="h-5 w-5" />
              </Button>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {node.id === rootPersonId ? (
                <HumanBadge name={node.name} label="ego anchor" size="xs" />
              ) : (
                <AgentBadge slug="graph-sync-worker" label="graph sync" size="xs" />
              )}
              <ActionAttribution
                actor={
                  node.id === rootPersonId
                    ? { type: "human", name: node.name, label: node.name }
                    : { type: "agent", slug: "graph-sync-worker", label: "Graph Sync Worker" }
                }
                verb={node.id === rootPersonId ? "anchors" : "projects"}
                timestamp={snapshotAt ?? new Date()}
                target={node.name}
                compact
              />
            </div>
          </div>

          <div className="co-scrollbar min-h-0 flex-1 overflow-y-auto px-4 py-4">
            <dl className="grid gap-3 text-sm">
              <div className="rounded-md border border-border bg-background/70 p-3">
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">Graph ID</dt>
                <dd className="mt-1 break-all font-mono text-[11px] text-foreground">{node.id}</dd>
              </div>
              <div className="rounded-md border border-border bg-background/70 p-3">
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">Tenant</dt>
                <dd className="mt-1 text-foreground">{node.tenant_id ?? "Current tenant"}</dd>
              </div>
              <div className="rounded-md border border-border bg-background/70 p-3">
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">Group</dt>
                <dd className="mt-1 text-foreground">{node.group}</dd>
              </div>
            </dl>

            <div className="mt-4 space-y-3">
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Connections</p>
                <MonoNumeric tone="muted">{incidentLinks.length}</MonoNumeric>
              </div>
              {displayLinks.length ? (
                <div className="space-y-2">
                  {displayLinks.map((link) => {
                    const { source, target } = resolveLinkNodes(link as GraphLinkWithEndpoints);
                    const otherNodeId = source === node.id ? target : source;
                    const otherNodeName = resolveNodeName(graph, otherNodeId);
                    const proposed = isProposeOnlyRelationship(link.type);
                    return (
                      <div key={`${source}-${target}-${link.type}`} className="space-y-2 rounded-md border border-border bg-background/70 p-3">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium text-foreground">{link.type}</p>
                            <p className="text-xs text-muted-foreground">
                              <ArrowRight className="mr-1 inline h-3 w-3 align-[-1px]" />
                              {otherNodeName}
                            </p>
                          </div>
                          <ConfidenceIndicator value={link.strength ?? 0} mode="binary" size="sm" showLabel={false} />
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <ActionAttribution
                            actor={{ type: "agent", slug: "graph-sync-worker", label: "Graph Sync Worker" }}
                            verb={proposed ? "proposes" : "projects"}
                            timestamp={snapshotAt ?? new Date()}
                            target={otherNodeName}
                            compact
                          />
                          {proposed ? <Badge variant="outline">propose_only</Badge> : <Badge variant="secondary">asserted</Badge>}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="rounded-md border border-border bg-background/70 px-3 py-4 text-sm text-muted-foreground">
                  No incident relationships are visible at the current hop and confidence floor.
                </div>
              )}
            </div>
          </div>

          <div className="border-t border-border px-4 py-3">
            <Button className="w-full" onClick={onExpand}>
              <ChevronRight className="h-4 w-4" />
              Expand to second-degree
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex h-full min-h-[220px] items-center justify-center px-4 text-center">
          <div className="space-y-3">
            <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-md border border-border bg-background text-muted-foreground">
              <Network className="h-5 w-5" />
            </div>
            <div>
              <p className="text-sm font-medium text-foreground">Select a node</p>
              <p className="mt-1 text-sm text-muted-foreground">The drawer will show provenance, confidence, and incident links.</p>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}
