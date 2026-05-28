import { useEffect, useMemo, useState } from "react";
import { useParams } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { GraphCanvas, type GraphRenderMode } from "@/components/graph/GraphCanvas";
import { GraphFilterPanel } from "@/components/graph/GraphFilterPanel";
import { GraphLegend } from "@/components/graph/GraphLegend";
import { NodeDetailDrawer } from "@/components/graph/NodeDetailDrawer";
import { Spinner } from "@/design-system";
import { useEgoGraph, type GraphNode } from "@/hooks/useEgoGraph";

export function GraphRoute() {
  const params = useParams({ strict: false }) as { id?: string };
  const [manualPersonId, setManualPersonId] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hopLimit, setHopLimit] = useState(1);
  const [confidenceFloor, setConfidenceFloor] = useState(0);
  const [renderMode, setRenderMode] = useState<GraphRenderMode>("auto");
  const [snapshotAt, setSnapshotAt] = useState<Date | null>(null);

  const personId = params.id ?? (manualPersonId.trim() || undefined);
  const graph = useEgoGraph(personId, hopLimit, confidenceFloor);

  useEffect(() => {
    setSnapshotAt(graph.data ? new Date() : null);
  }, [graph.data]);

  const selectedNode = useMemo(() => {
    if (!graph.data || !selectedNodeId) return null;
    return graph.data.nodes.find((node) => node.id === selectedNodeId) ?? null;
  }, [graph.data, selectedNodeId]);

  useEffect(() => {
    if (!selectedNodeId) return;
    if (!graph.data?.nodes.some((node) => node.id === selectedNodeId)) {
      setSelectedNodeId(null);
    }
  }, [graph.data, selectedNodeId]);

  const nodeCount = graph.data?.node_count ?? 0;
  const edgeCount = graph.data?.edge_count ?? 0;
  const hasGraph = Boolean(graph.data);

  return (
    <div className="relative isolate -m-4 min-h-[calc(100svh-4rem)] overflow-hidden bg-background text-foreground lg:-m-6">
      <div className="absolute inset-0 bg-background" aria-hidden="true" />

      <div className="co-scrollbar absolute left-3 top-3 z-20 max-h-[46svh] w-[min(32rem,calc(100vw-1.5rem))] space-y-3 overflow-y-auto rounded-lg border border-border bg-card/90 p-4 shadow-[var(--shadow-3)] backdrop-blur-xl md:left-4 md:top-4 md:max-h-none md:w-[min(30rem,calc(100vw-2rem))] md:overflow-visible">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold leading-7 tracking-tight">Graph</h1>
              {renderMode === "2d" ? <Badge variant="secondary">2D</Badge> : null}
              {renderMode === "auto" ? <Badge variant="outline">Auto</Badge> : null}
            </div>
            <p className="text-sm text-muted-foreground">
              {hasGraph ? (
                <>
                  <span className="co-mono-numeric font-mono">
                    <strong>{nodeCount}</strong> nodes / <strong>{edgeCount}</strong> edges
                  </span>{" "}
                  in the current ego network
                </>
              ) : (
                "Tenant-scoped ego network discovery"
              )}
            </p>
          </div>
          {graph.isLoading ? <Spinner size="sm" label="Loading graph" /> : null}
        </div>

        {!params.id ? (
          <div className="space-y-2">
            <label className="text-xs font-medium uppercase tracking-wide text-muted-foreground" htmlFor="graph-person-id">
              Person UUID
            </label>
            <Input
              id="graph-person-id"
              value={manualPersonId}
              onChange={(event) => setManualPersonId(event.target.value)}
              placeholder="Paste a person UUID to start exploring"
              className="bg-background"
            />
          </div>
        ) : null}

        <GraphFilterPanel
          hopLimit={hopLimit}
          confidenceFloor={confidenceFloor}
          renderMode={renderMode}
          onHopLimitChange={setHopLimit}
          onConfidenceFloorChange={setConfidenceFloor}
          onRenderModeChange={setRenderMode}
        />
        <GraphLegend />
      </div>

      {graph.isLoading ? (
        <div className="absolute inset-0 flex items-center justify-center px-4">
          <div className="w-full max-w-xl space-y-4 rounded-lg border border-border bg-card/80 p-6 shadow-[var(--shadow-2)] backdrop-blur-xl">
            <div className="space-y-2">
              <Skeleton className="h-6 w-32" />
              <Skeleton className="h-4 w-72" />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Skeleton className="h-40 w-full" />
              <Skeleton className="h-40 w-full" />
            </div>
          </div>
        </div>
      ) : null}

      <GraphCanvas
        graph={graph.data}
        selectedNodeId={selectedNodeId}
        renderMode={renderMode}
        rootPersonId={personId}
        loading={graph.isLoading}
        onNodeClick={(node: GraphNode) => setSelectedNodeId(node.id)}
      />

      <NodeDetailDrawer
        node={selectedNode}
        graph={graph.data}
        rootPersonId={personId}
        snapshotAt={snapshotAt}
        onExpand={() => setHopLimit(2)}
      />
    </div>
  );
}
