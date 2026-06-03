import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D, { type ForceGraphMethods } from "react-force-graph-3d";
import { Box, Network } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { MonoNumeric, Spinner } from "@/design-system";
import { forceConfig } from "@/lib/graph/forceConfig";
import { colorForNode, isProposeOnlyRelationship } from "@/lib/graph/colorScheme";
import { cn } from "@/lib/utils";
import type { EgoGraph, GraphLink, GraphNode } from "@/hooks/useEgoGraph";

export type GraphRenderMode = "auto" | "2d" | "3d";

type ScreenLabel = {
  id: string;
  name: string;
  kind: string;
  x: number;
  y: number;
  highlight: boolean;
  primary: boolean;
};

type LayoutGraphNode = GraphNode & {
  x?: number;
  y?: number;
  z?: number;
};

type SvgLayoutNode = GraphNode & {
  sx: number;
  sy: number;
};

const LABEL_CEILING = 12;

function detectLowPowerHint() {
  if (typeof window === "undefined") return false;
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
  const lowMemory = typeof navigator !== "undefined" && "deviceMemory" in navigator && Number((navigator as Navigator & { deviceMemory?: number }).deviceMemory ?? 8) <= 4;
  const lowCores = typeof navigator !== "undefined" && Number(navigator.hardwareConcurrency ?? 8) <= 4;
  const automatedBrowser = typeof navigator !== "undefined" && Boolean(navigator.webdriver);
  const hasWebGL = (() => {
    try {
      const canvas = document.createElement("canvas");
      return Boolean(canvas.getContext("webgl") || canvas.getContext("experimental-webgl"));
    } catch {
      return false;
    }
  })();
  return automatedBrowser || reducedMotion || lowMemory || lowCores || !hasWebGL;
}

function resolveNodeId(value: string | GraphNode | undefined | null) {
  if (!value) return null;
  return typeof value === "string" ? value : value.id;
}

function normalizeKind(kind: string) {
  return kind.toLowerCase();
}

function nodeKindClass(kind: string) {
  switch (normalizeKind(kind)) {
    case "person":
      return "border-primary/25 bg-primary/10 text-foreground";
    case "organization":
    case "org":
      return "border-info/25 bg-info/10 text-foreground";
    case "agent":
      return "border-confidence-amber/35 bg-confidence-amber/10 text-foreground";
    case "topic":
      return "border-warning/25 bg-warning/10 text-foreground";
    case "tag":
      return "border-confidence-rose/30 bg-confidence-rose/10 text-foreground";
    default:
      return "border-border bg-card/90 text-foreground";
  }
}

function nodePriority(node: GraphNode, selectedNodeId: string | null, hoveredNodeId: string | null, rootPersonId?: string) {
  if (node.id === selectedNodeId) return 0;
  if (node.id === hoveredNodeId) return 1;
  if (rootPersonId && node.id === rootPersonId) return 2;
  return 3;
}

function linkLabel(link: GraphLink) {
  const confidence = Math.round((link.strength ?? 0) * 100);
  return `${link.type} · ${confidence}% confidence`;
}

function nodeTooltip(node: GraphNode) {
  return `${node.name} · ${node.kind}`;
}

function tokenColorToCanvasColor(tokenColor: string, fallback = "currentColor") {
  if (typeof window === "undefined" || typeof document === "undefined") return fallback;
  const variable = tokenColor.match(/var\(--([^)]+)\)/)?.[1];
  if (!variable) return tokenColor;
  const channel = window.getComputedStyle(document.documentElement).getPropertyValue(`--${variable}`).trim();
  if (!channel) return fallback;
  const alpha = tokenColor.match(/\/\s*([^)]+)\)/)?.[1]?.trim();
  const cssColor = alpha ? `oklch(${channel} / ${alpha})` : `oklch(${channel})`;
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx) return cssColor;
  ctx.fillStyle = cssColor;
  return ctx.fillStyle || cssColor;
}

export function GraphCanvas({
  graph,
  selectedNodeId,
  renderMode,
  rootPersonId,
  loading = false,
  onNodeClick
}: {
  graph?: EgoGraph;
  selectedNodeId: string | null;
  renderMode: GraphRenderMode;
  rootPersonId?: string;
  loading?: boolean;
  onNodeClick: (node: GraphNode) => void;
}) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<ForceGraphMethods<GraphNode, GraphLink> | undefined>(undefined);
  const [size, setSize] = useState({ width: 900, height: 700 });
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [engineTick, setEngineTick] = useState(0);
  const lowPowerHint = useMemo(() => detectLowPowerHint(), []);
  const effectiveMode: "2d" | "3d" = renderMode === "auto" ? (lowPowerHint ? "2d" : "3d") : renderMode;
  const [colorVersion, setColorVersion] = useState(0);

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
    instance.d3Force("charge")?.strength(effectiveMode === "2d" ? -300 : forceConfig.chargeStrength);
    instance.d3Force("link")?.distance(effectiveMode === "2d" ? 150 : forceConfig.linkDistance);
    instance.d3Force("center")?.strength(forceConfig.centerStrength);
    instance.d3ReheatSimulation();
  }, [effectiveMode, graph]);

  useEffect(() => {
    const controls = graphRef.current?.controls?.() as
      | { autoRotate?: boolean; autoRotateSpeed?: number; enableDamping?: boolean; enablePan?: boolean; enableZoom?: boolean }
      | undefined;
    if (!controls) return;
    controls.autoRotate = effectiveMode === "3d" && !lowPowerHint;
    controls.autoRotateSpeed = 0.18;
    controls.enableDamping = true;
    controls.enablePan = true;
    controls.enableZoom = true;
  }, [effectiveMode, lowPowerHint]);

  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const update = () => setColorVersion((value) => value + 1);
    media?.addEventListener?.("change", update);
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style"] });
    return () => {
      media?.removeEventListener?.("change", update);
      observer.disconnect();
    };
  }, []);

  const canvasColors = useMemo(() => {
    void colorVersion;
    return {
      node: (kind: string) => tokenColorToCanvasColor(colorForNode(kind)),
      edge: (channel: string, confidence: number) => tokenColorToCanvasColor(`oklch(var(--${channel}) / ${confidence})`)
    };
  }, [colorVersion]);

  const data = useMemo(() => graph ?? { nodes: [], links: [] }, [graph]);
  const incidentNodeIds = useMemo(() => {
    const selected = selectedNodeId ? [selectedNodeId] : [];
    const hovered = hoveredNodeId && hoveredNodeId !== selectedNodeId ? [hoveredNodeId] : [];
    const root = rootPersonId && rootPersonId !== selectedNodeId && rootPersonId !== hoveredNodeId ? [rootPersonId] : [];
    const linked = new Set<string>([...selected, ...hovered, ...root].filter(Boolean));
    const focusIds = [...linked];
    for (const link of data.links) {
      const source = resolveNodeId(link.source as string | GraphNode | undefined);
      const target = resolveNodeId(link.target as string | GraphNode | undefined);
      if (!source || !target) continue;
      if (linked.has(source)) linked.add(target);
      if (linked.has(target)) linked.add(source);
    }
    return focusIds.concat([...linked].filter((id) => !focusIds.includes(id)));
  }, [data.links, hoveredNodeId, rootPersonId, selectedNodeId]);

  const labels = useMemo(() => {
    if (!graph || data.nodes.length === 0) return [];
    const focusIds = new Set<string>();
    if (selectedNodeId) focusIds.add(selectedNodeId);
    if (hoveredNodeId) focusIds.add(hoveredNodeId);
    if (rootPersonId) focusIds.add(rootPersonId);
    for (const link of data.links) {
      const source = resolveNodeId(link.source as string | GraphNode | undefined);
      const target = resolveNodeId(link.target as string | GraphNode | undefined);
      if (source && focusIds.has(source) && target) focusIds.add(target);
      if (target && focusIds.has(target) && source) focusIds.add(source);
    }
    const focusNodes = data.nodes.filter((node) => focusIds.has(node.id));
    const ranked = [...focusNodes].sort(
      (a, b) =>
        nodePriority(a, selectedNodeId, hoveredNodeId, rootPersonId) -
          nodePriority(b, selectedNodeId, hoveredNodeId, rootPersonId) ||
        (b.val ?? 0) - (a.val ?? 0) ||
        a.name.localeCompare(b.name)
    );
    return ranked.slice(0, LABEL_CEILING);
  }, [data.links, data.nodes, graph, hoveredNodeId, rootPersonId, selectedNodeId]);

  const projectedLabels = useMemo<ScreenLabel[]>(() => {
    const instance = graphRef.current;
    if (!instance) return [];
    void engineTick;
    return labels
      .map((node) => {
        const layoutNode = node as LayoutGraphNode;
        if (layoutNode.x == null || layoutNode.y == null || layoutNode.z == null) return null;
        const projected = instance.graph2ScreenCoords(layoutNode.x, layoutNode.y, layoutNode.z);
        if (
          !Number.isFinite(projected.x) ||
          !Number.isFinite(projected.y) ||
          projected.x < -120 ||
          projected.x > size.width + 120 ||
          projected.y < -120 ||
          projected.y > size.height + 120
        ) {
          return null;
        }
        return {
          id: node.id,
          name: node.name,
          kind: node.kind,
          x: projected.x,
          y: projected.y,
          highlight: node.id === selectedNodeId || node.id === hoveredNodeId,
          primary: node.id === selectedNodeId
        };
      })
      .filter(Boolean) as ScreenLabel[];
  }, [engineTick, hoveredNodeId, labels, selectedNodeId, size.height, size.width]);

  const edgeColor = useCallback(
    (link: GraphLink) => {
      const confidence = Math.max(0.15, Math.min(1, link.strength ?? 0.6));
      const channel = isProposeOnlyRelationship(link.type) ? "confidence-amber" : "info";
      return canvasColors.edge(channel, confidence);
    },
    [canvasColors]
  );

  const edgeWidth = useCallback((link: GraphLink) => {
    const confidence = Math.max(0.25, Math.min(1.6, (link.strength ?? 0.6) * 1.6));
    return isProposeOnlyRelationship(link.type) ? Math.max(0.8, confidence * 0.75) : confidence;
  }, []);

  const edgeParticles = useCallback(
    (link: GraphLink) => {
      const source = resolveNodeId(link.source as string | GraphNode | undefined);
      const target = resolveNodeId(link.target as string | GraphNode | undefined);
      const active = source === selectedNodeId || target === selectedNodeId || source === hoveredNodeId || target === hoveredNodeId;
      return active ? (isProposeOnlyRelationship(link.type) ? 2 : 1) : 0;
    },
    [selectedNodeId, hoveredNodeId]
  );

  const edgeParticleColor = useCallback((link: GraphLink) => edgeColor(link), [edgeColor]);

  const highlightedNodeSet = useMemo(() => {
    const set = new Set<string>();
    if (selectedNodeId) set.add(selectedNodeId);
    if (hoveredNodeId) set.add(hoveredNodeId);
    if (rootPersonId) set.add(rootPersonId);
    for (const id of incidentNodeIds) set.add(id);
    return set;
  }, [incidentNodeIds, hoveredNodeId, rootPersonId, selectedNodeId]);

  // Stable accessor identities: react-force-graph re-applies any prop whose
  // reference changes, and onEngineTick re-renders every simulation tick, so
  // inline accessors would be re-applied to the graph every frame during layout.
  const nodeColorAccessor = useCallback((node: GraphNode) => canvasColors.node(node.kind), [canvasColors]);
  const nodeValAccessor = useCallback(
    (node: GraphNode) => {
      const highlighted = highlightedNodeSet.has(node.id);
      return (node.val ?? 4) * (highlighted ? 1.6 : 1);
    },
    [highlightedNodeSet]
  );
  const nodeLabelAccessor = useCallback((node: GraphNode) => nodeTooltip(node), []);
  const linkColorAccessor = useCallback((link: GraphLink) => edgeColor(link), [edgeColor]);
  const linkWidthAccessor = useCallback((link: GraphLink) => edgeWidth(link), [edgeWidth]);
  const linkCurvatureAccessor = useCallback((link: GraphLink) => (isProposeOnlyRelationship(link.type) ? 0.12 : 0.04), []);
  const linkArrowLengthAccessor = useCallback((link: GraphLink) => (isProposeOnlyRelationship(link.type) ? 4 : 0), []);
  const linkArrowColorAccessor = useCallback((link: GraphLink) => edgeColor(link), [edgeColor]);
  const linkParticlesAccessor = useCallback((link: GraphLink) => edgeParticles(link), [edgeParticles]);
  const linkParticleWidthAccessor = useCallback((link: GraphLink) => (isProposeOnlyRelationship(link.type) ? 1.5 : 1), []);
  const linkParticleColorAccessor = useCallback((link: GraphLink) => edgeParticleColor(link), [edgeParticleColor]);
  const linkLabelAccessor = useCallback((link: GraphLink) => linkLabel(link), []);
  const handleNodeClick = useCallback((node: GraphNode) => onNodeClick(node), [onNodeClick]);
  const handleNodeHover = useCallback((node: GraphNode | null) => setHoveredNodeId(node ? node.id : null), []);
  const handleBackgroundClick = useCallback(() => setHoveredNodeId(null), []);
  const handleEngineTick = useCallback(() => setEngineTick((value) => value + 1), []);

  const svgNodes = useMemo<SvgLayoutNode[]>(() => {
    if (!graph || data.nodes.length === 0) return [];
    const compact = size.width < 640;
    const centerX = size.width / 2;
    const centerY = compact ? size.height * 0.58 : size.height / 2;
    const radius = Math.max(compact ? 68 : 120, Math.min(size.width, size.height) * (compact ? 0.2 : 0.32));
    const sorted = [...data.nodes].sort((a, b) => nodePriority(a, selectedNodeId, hoveredNodeId, rootPersonId) - nodePriority(b, selectedNodeId, hoveredNodeId, rootPersonId));
    const rootIndex = sorted.findIndex((node) => node.id === rootPersonId);
    if (rootIndex > 0) {
      const [root] = sorted.splice(rootIndex, 1);
      sorted.unshift(root);
    }
    return sorted.map((node, index) => {
      if (index === 0) return { ...node, sx: centerX, sy: centerY };
      const angle = ((index - 1) / Math.max(1, sorted.length - 1)) * Math.PI * 2 - Math.PI / 2;
      const confidenceOffset = ((node.val ?? 4) % 3) * 18;
      return {
        ...node,
        sx: centerX + Math.cos(angle) * (radius + confidenceOffset),
        sy: centerY + Math.sin(angle) * (radius + confidenceOffset)
      };
    });
  }, [data.nodes, graph, hoveredNodeId, rootPersonId, selectedNodeId, size.height, size.width]);

  const svgNodeMap = useMemo(() => new Map(svgNodes.map((node) => [node.id, node])), [svgNodes]);

  const visibleSvgLabels = useMemo(() => {
    const allowed = new Set(labels.map((label) => label.id));
    return svgNodes.filter((node) => allowed.has(node.id)).slice(0, LABEL_CEILING);
  }, [labels, svgNodes]);

  const graphBody = loading ? (
    <div className="flex h-full min-h-[520px] w-full items-center justify-center md:pl-[33rem]">
      <div className="space-y-3 rounded-lg border border-border bg-card/85 px-6 py-5 shadow-[var(--shadow-2)] backdrop-blur-xl">
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <Spinner size="sm" label="Loading graph" />
          <span>Projecting the ego network</span>
        </div>
        <Skeleton className="h-40 w-[min(26rem,80vw)]" />
      </div>
    </div>
  ) : !graph || data.nodes.length === 0 ? (
    <div className="flex h-full min-h-[520px] w-full items-center justify-center px-4 md:pl-[33rem]">
      <div className="max-w-md space-y-4 rounded-lg border border-border bg-card/80 p-6 text-left shadow-[var(--shadow-2)] backdrop-blur-xl">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-md border border-border bg-background text-muted-foreground">
            <Network className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-foreground">Select a person UUID</h2>
            <p className="text-sm text-muted-foreground">Open a person route or paste a UUID above to draw the ego network.</p>
          </div>
        </div>
        <p className="text-sm text-muted-foreground">
          The canvas stays calm until the graph has a root person. Once a person is loaded, labels only appear on selection and hover.
        </p>
      </div>
    </div>
  ) : effectiveMode === "2d" ? (
    <div ref={wrapRef} className="relative h-full min-h-[520px] w-full overflow-hidden bg-background">
      <svg
        className="absolute inset-0 block h-full w-full"
        viewBox={`0 0 ${size.width} ${size.height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="2D ego network fallback"
      >
        <g>
          {data.links.map((link, index) => {
            const sourceId = resolveNodeId(link.source as string | GraphNode | undefined);
            const targetId = resolveNodeId(link.target as string | GraphNode | undefined);
            const source = sourceId ? svgNodeMap.get(sourceId) : null;
            const target = targetId ? svgNodeMap.get(targetId) : null;
            if (!source || !target) return null;
            const proposeOnly = isProposeOnlyRelationship(link.type);
            return (
              <line
                key={`${source.id}-${target.id}-${link.type}-${index}`}
                x1={source.sx}
                y1={source.sy}
                x2={target.sx}
                y2={target.sy}
                stroke={edgeColor(link)}
                strokeWidth={edgeWidth(link)}
                strokeDasharray={proposeOnly ? "4 5" : undefined}
                opacity={0.82}
              />
            );
          })}
        </g>
        <g>
          {svgNodes.map((node) => {
            const highlighted = highlightedNodeSet.has(node.id);
            const radius = Math.max(7, Math.min(22, (node.val ?? 4) * (highlighted ? 1.9 : 1.35)));
            return (
              <g
                key={node.id}
                role="button"
                tabIndex={0}
                aria-label={nodeTooltip(node)}
                onClick={() => onNodeClick(node)}
                onMouseEnter={() => setHoveredNodeId(node.id)}
                onMouseLeave={() => setHoveredNodeId(null)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") onNodeClick(node);
                }}
                className="cursor-pointer outline-none"
              >
                <circle cx={node.sx} cy={node.sy} r={radius + 5} fill={canvasColors.node(node.kind)} opacity={highlighted ? 0.18 : 0.08} />
                <circle cx={node.sx} cy={node.sy} r={radius} fill={canvasColors.node(node.kind)} opacity={0.94} />
                <circle cx={node.sx} cy={node.sy} r={radius} fill="none" stroke={canvasColors.node(node.kind)} strokeWidth={highlighted ? 3 : 1.5} opacity={0.65} />
              </g>
            );
          })}
        </g>
      </svg>
      <div className="pointer-events-none absolute inset-0">
        {visibleSvgLabels.map((label) => (
          <div
            key={label.id}
            className={cn(
              "absolute inline-flex max-w-44 -translate-x-1/2 -translate-y-[calc(100%+0.75rem)] items-center gap-2 rounded-md border px-2 py-1 shadow-[var(--shadow-1)] backdrop-blur-xl",
              nodeKindClass(label.kind),
              (label.id === selectedNodeId || label.id === hoveredNodeId) && "ring-1 ring-primary/25"
            )}
            style={{
              left: label.sx,
              top: label.sy
            }}
          >
            <span className={cn("h-2 w-2 shrink-0 rounded-full", label.id === selectedNodeId ? "bg-primary" : "bg-muted-foreground")} />
            <span className="min-w-0">
              <span className="block truncate text-[11px] font-medium leading-none text-foreground">{label.name}</span>
              <span className="block truncate font-mono text-[10px] leading-none text-muted-foreground">{label.kind}</span>
            </span>
          </div>
        ))}
      </div>
      <div className="pointer-events-none absolute left-3 bottom-3 flex items-center gap-2 rounded-md border border-border bg-card/80 px-3 py-2 text-[11px] text-muted-foreground shadow-[var(--shadow-1)] backdrop-blur-xl">
        <Box className="h-3.5 w-3.5" />
        <span>2D fallback active</span>
        <MonoNumeric tone="muted">{visibleSvgLabels.length}</MonoNumeric>
        <span>labels</span>
      </div>
    </div>
  ) : (
    <div ref={wrapRef} className="relative h-full min-h-[520px] w-full overflow-hidden bg-background">
      <ForceGraph3D
        ref={graphRef}
        width={size.width}
        height={size.height}
        graphData={data}
        backgroundColor="transparent"
        nodeId="id"
        numDimensions={3}
        nodeLabel={nodeLabelAccessor}
        nodeColor={nodeColorAccessor}
        nodeVal={nodeValAccessor}
        nodeOpacity={0.96}
        nodeResolution={16}
        linkColor={linkColorAccessor}
        linkOpacity={0.78}
        linkWidth={linkWidthAccessor}
        linkCurvature={linkCurvatureAccessor}
        linkDirectionalArrowLength={linkArrowLengthAccessor}
        linkDirectionalArrowColor={linkArrowColorAccessor}
        linkDirectionalArrowRelPos={0.92}
        linkDirectionalParticles={linkParticlesAccessor}
        linkDirectionalParticleSpeed={0.004}
        linkDirectionalParticleWidth={linkParticleWidthAccessor}
        linkDirectionalParticleColor={linkParticleColorAccessor}
        linkLabel={linkLabelAccessor}
        showNavInfo={false}
        enableNavigationControls
        enablePointerInteraction
        cooldownTicks={forceConfig.cooldownTicks}
        warmupTicks={forceConfig.warmupTicks}
        onEngineTick={handleEngineTick}
        onNodeClick={handleNodeClick}
        onNodeHover={handleNodeHover}
        onBackgroundClick={handleBackgroundClick}
      />
      <div className="pointer-events-none absolute inset-0">
        {projectedLabels.map((label) => (
          <div
            key={label.id}
            className={cn(
              "absolute inline-flex max-w-44 -translate-x-1/2 -translate-y-1/2 items-center gap-2 rounded-md border px-2 py-1 shadow-[var(--shadow-1)] backdrop-blur-xl",
              nodeKindClass(label.kind),
              label.highlight && "ring-1 ring-primary/25",
              label.primary && "z-20"
            )}
            style={{
              left: label.x,
              top: label.y
            }}
          >
            <span className={cn("h-2 w-2 shrink-0 rounded-full", label.primary ? "bg-primary" : "bg-muted-foreground")} />
            <span className="min-w-0">
              <span className="block truncate text-[11px] font-medium leading-none text-foreground">{label.name}</span>
              <span className="block truncate font-mono text-[10px] leading-none text-muted-foreground">{label.kind}</span>
            </span>
          </div>
        ))}
      </div>
      <div className="pointer-events-none absolute left-3 bottom-3 flex items-center gap-2 rounded-md border border-border bg-card/80 px-3 py-2 text-[11px] text-muted-foreground shadow-[var(--shadow-1)] backdrop-blur-xl">
        <Box className="h-3.5 w-3.5" />
        <span>3D orbit active</span>
        <MonoNumeric tone="muted">{projectedLabels.length}</MonoNumeric>
        <span>labels</span>
      </div>
    </div>
  );

  return <div className="relative h-full min-h-[520px] w-full">{graphBody}</div>;
}
