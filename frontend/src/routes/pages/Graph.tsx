import { useState } from "react";
import { useParams } from "@tanstack/react-router";
import { GraphCanvas } from "@/components/graph/GraphCanvas";
import { GraphFilterPanel } from "@/components/graph/GraphFilterPanel";
import { GraphLegend } from "@/components/graph/GraphLegend";
import { NodeDetailDrawer } from "@/components/graph/NodeDetailDrawer";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useEgoGraph, type GraphNode } from "@/hooks/useEgoGraph";

export function GraphRoute() {
  const params = useParams({ strict: false }) as { id?: string };
  const [manualPersonId, setManualPersonId] = useState("");
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [hopLimit, setHopLimit] = useState(1);
  const [confidenceFloor, setConfidenceFloor] = useState(0);
  const personId = params.id ?? (manualPersonId || undefined);
  const graph = useEgoGraph(personId, hopLimit, confidenceFloor);

  return (
    <div className="relative -m-4 min-h-[calc(100vh-4rem)] bg-zinc-950 text-zinc-100 lg:-m-6">
      <div className="absolute left-4 top-4 z-10 w-[min(28rem,calc(100vw-2rem))] space-y-4 rounded-md border border-zinc-800 bg-zinc-950/90 p-4 shadow-2xl backdrop-blur">
        <div>
          <h1 className="text-xl font-semibold">Graph</h1>
          <p className="text-xs text-zinc-400">{graph.data ? `${graph.data.node_count} nodes, ${graph.data.edge_count} edges` : "Tenant-scoped ego network"}</p>
        </div>
        {!params.id && (
          <Input
            value={manualPersonId}
            onChange={(event) => setManualPersonId(event.target.value)}
            placeholder="Person UUID"
            className="bg-zinc-900"
          />
        )}
        <GraphFilterPanel
          hopLimit={hopLimit}
          confidenceFloor={confidenceFloor}
          onHopLimitChange={setHopLimit}
          onConfidenceFloorChange={setConfidenceFloor}
        />
        <GraphLegend />
      </div>
      {graph.isLoading ? (
        <div className="flex h-[calc(100vh-4rem)] items-center justify-center">
          <Skeleton className="h-40 w-80 bg-zinc-800" />
        </div>
      ) : (
        <GraphCanvas graph={graph.data} onNodeClick={setSelected} />
      )}
      <NodeDetailDrawer node={selected} onExpand={() => setHopLimit(2)} />
    </div>
  );
}
