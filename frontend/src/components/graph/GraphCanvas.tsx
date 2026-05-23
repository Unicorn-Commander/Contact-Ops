import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D from "react-force-graph-3d";
import { forceConfig } from "@/lib/graph/forceConfig";
import type { EgoGraph, GraphNode } from "@/hooks/useEgoGraph";

export function GraphCanvas({
  graph,
  onNodeClick
}: {
  graph?: EgoGraph;
  onNodeClick: (node: GraphNode) => void;
}) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<any>(null);
  const [size, setSize] = useState({ width: 900, height: 700 });

  useEffect(() => {
    if (!wrapRef.current) return;
    const observer = new ResizeObserver(([entry]) => {
      const rect = entry.contentRect;
      setSize({ width: Math.max(320, rect.width), height: Math.max(320, rect.height) });
    });
    observer.observe(wrapRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const instance = graphRef.current;
    if (!instance) return;
    instance.d3Force("charge")?.strength(forceConfig.chargeStrength);
    instance.d3Force("link")?.distance(forceConfig.linkDistance);
    instance.d3Force("center")?.strength(forceConfig.centerStrength);
  }, [graph]);

  const data = useMemo(() => graph ?? { nodes: [], links: [] }, [graph]);

  return (
    <div ref={wrapRef} className="h-full min-h-[520px] w-full overflow-hidden bg-zinc-950">
      <ForceGraph3D
        ref={graphRef}
        width={size.width}
        height={size.height}
        graphData={data}
        backgroundColor="#09090b"
        nodeLabel={(node: any) => node.name}
        nodeColor={(node: any) => node.color}
        nodeVal={(node: any) => node.val ?? 4}
        linkColor={(link: any) => link.color}
        linkOpacity={0.55}
        linkWidth={(link: any) => Math.max(1, (link.strength ?? 0.5) * 3)}
        cooldownTicks={forceConfig.cooldownTicks}
        warmupTicks={forceConfig.warmupTicks}
        onNodeClick={(node: any) => onNodeClick(node as GraphNode)}
      />
    </div>
  );
}
