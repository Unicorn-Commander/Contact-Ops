import { useEffect, useMemo, useState } from "react";
import { useParams } from "@tanstack/react-router";
import { Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { GraphCanvas, type GraphRenderMode } from "@/components/graph/GraphCanvas";
import { GraphEmptyState } from "@/components/graph/GraphEmptyState";
import { GraphFilterPanel } from "@/components/graph/GraphFilterPanel";
import { GraphLegend } from "@/components/graph/GraphLegend";
import { GraphRootPicker } from "@/components/graph/GraphRootPicker";
import { NodeDetailDrawer } from "@/components/graph/NodeDetailDrawer";
import { Spinner } from "@/design-system";
import { useEgoGraph, type EgoGraph, type GraphLink, type GraphNode } from "@/hooks/useEgoGraph";
import { useGraphBootstrap } from "@/hooks/useGraphBootstrap";

/** Resolve a link endpoint (string id or hydrated node) to its id. */
function endpointId(value: GraphLink["source"] | GraphLink["target"]): string | undefined {
  if (!value) return undefined;
  return typeof value === "string" ? value : (value as { id?: string }).id;
}

export function GraphRoute() {
  const params = useParams({ strict: false }) as { id?: string };

  // ── Root selection ────────────────────────────────────────────────────────
  // Precedence: a person-page route param (locked root) → an explicit pick /
  // pasted id → an auto-picked default (unless the user has cleared back to the
  // search-first state). This is what lets the canvas show something immediately
  // while still honoring an explicit choice.
  const [pickedRootId, setPickedRootId] = useState<string | undefined>(undefined);
  const [pickedRootName, setPickedRootName] = useState<string | undefined>(undefined);
  const [cleared, setCleared] = useState(false);

  // Bootstrap probe only matters when we don't already have a route-param root.
  const bootstrap = useGraphBootstrap(!params.id);
  const autoRootId = cleared ? undefined : bootstrap.defaultRootId;

  const personId = params.id ?? pickedRootId ?? autoRootId;
  const rootName =
    pickedRootId && pickedRootId === personId
      ? pickedRootName
      : personId && personId === bootstrap.defaultRootId
        ? bootstrap.defaultRootName
        : undefined;
  const rootIsAuto = !params.id && !pickedRootId && Boolean(autoRootId);

  // ── Controls ─────────────────────────────────────────────────────────────
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hopLimit, setHopLimit] = useState(1);
  const [confidenceFloor, setConfidenceFloor] = useState(0);
  const [renderMode, setRenderMode] = useState<GraphRenderMode>("auto");
  const [hiddenKinds, setHiddenKinds] = useState<Set<string>>(new Set());
  const [snapshotAt, setSnapshotAt] = useState<Date | null>(null);

  const graph = useEgoGraph(personId, hopLimit, confidenceFloor);

  useEffect(() => {
    setSnapshotAt(graph.data ? new Date() : null);
  }, [graph.data]);

  // ── Node-type filter (page-level; GraphCanvas + useEgoGraph stay intact) ───
  // Derive the kinds present in the raw graph, then drop hidden-kind nodes and
  // any link that touches one. The ROOT is never hidden, so the canvas always
  // keeps an anchor even if its kind is toggled off.
  const availableKinds = useMemo(() => {
    if (!graph.data) return [] as string[];
    const seen = new Set<string>();
    for (const node of graph.data.nodes) seen.add(node.kind);
    return [...seen].sort();
  }, [graph.data]);

  const filteredGraph = useMemo<EgoGraph | undefined>(() => {
    if (!graph.data) return graph.data;
    if (hiddenKinds.size === 0) return graph.data;
    const keep = new Set<string>();
    for (const node of graph.data.nodes) {
      if (!hiddenKinds.has(node.kind) || node.id === personId) keep.add(node.id);
    }
    const nodes = graph.data.nodes.filter((node) => keep.has(node.id));
    const links = graph.data.links.filter((link) => {
      const s = endpointId(link.source);
      const t = endpointId(link.target);
      return Boolean(s && t && keep.has(s) && keep.has(t));
    });
    return { ...graph.data, nodes, links, node_count: nodes.length, edge_count: links.length };
  }, [graph.data, hiddenKinds, personId]);

  const selectedNode = useMemo(() => {
    if (!filteredGraph || !selectedNodeId) return null;
    return filteredGraph.nodes.find((node) => node.id === selectedNodeId) ?? null;
  }, [filteredGraph, selectedNodeId]);

  useEffect(() => {
    if (!selectedNodeId) return;
    if (!filteredGraph?.nodes?.some((node) => node.id === selectedNodeId)) {
      setSelectedNodeId(null);
    }
  }, [filteredGraph, selectedNodeId]);

  const toggleKind = (kind: string) =>
    setHiddenKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });

  const handlePick = (id: string, name?: string) => {
    setPickedRootId(id);
    setPickedRootName(name);
    setCleared(false);
    setSelectedNodeId(null);
    setHiddenKinds(new Set());
  };

  const handleClearRoot = () => {
    setPickedRootId(undefined);
    setPickedRootName(undefined);
    setCleared(true); // suppress auto-root → return to the search-first invite
    setSelectedNodeId(null);
  };

  const nodeCount = filteredGraph?.node_count ?? 0;
  const edgeCount = filteredGraph?.edge_count ?? 0;
  const hasGraph = Boolean(filteredGraph && filteredGraph.nodes.length > 0);

  // ── State machine for what the canvas area renders ─────────────────────────
  //   empty   → tenant has zero contacts (import CTAs)
  //   invite  → data exists but no root chosen (search-first hero)
  //   graph   → a root is set (loading / canvas handled by GraphCanvas)
  const tenantEmpty = !params.id && !bootstrap.isLoading && !bootstrap.isError && !bootstrap.hasPeople;
  const bootstrapResolving = !params.id && bootstrap.isLoading && !personId;
  const showInvite = !personId && !tenantEmpty && !bootstrapResolving && !graph.isLoading;

  return (
    <div className="relative isolate -m-4 min-h-[calc(100svh-4rem)] overflow-hidden bg-background text-foreground lg:-m-6">
      <div className="absolute inset-0 bg-background" aria-hidden="true" />

      {/* Config panel — hidden entirely on the zero-contacts empty state so the
          import CTAs own the screen. */}
      {!tenantEmpty ? (
        <div className="co-scrollbar absolute left-3 top-3 z-20 max-h-[46svh] w-[min(32rem,calc(100vw-1.5rem))] space-y-3 overflow-y-auto rounded-lg border border-border bg-card/90 p-4 shadow-[var(--shadow-3)] backdrop-blur-xl md:left-4 md:top-4 md:max-h-none md:w-[min(30rem,calc(100vw-2rem))] md:overflow-visible">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-semibold leading-7 tracking-tight">Relationship graph</h1>
                {renderMode === "2d" ? <Badge variant="secondary">2D</Badge> : null}
                {renderMode === "auto" ? <Badge variant="outline">Auto</Badge> : null}
              </div>
              <p className="text-sm text-muted-foreground">
                {hasGraph ? (
                  <>
                    <span className="co-mono-numeric font-mono">
                      <strong>{nodeCount}</strong> nodes / <strong>{edgeCount}</strong> edges
                    </span>{" "}
                    around{" "}
                    {rootName ? <span className="text-foreground">{rootName}</span> : "this person"}
                    {rootIsAuto ? <span className="text-muted-foreground"> · auto-selected</span> : null}
                  </>
                ) : (
                  "See how your people and organizations connect."
                )}
              </p>
            </div>
            {graph.isLoading ? <Spinner size="sm" label="Loading graph" /> : null}
          </div>

          {/* Search-to-root is the PRIMARY path. On a person-page route the root
              is fixed, so we show a static anchor line instead of the picker. */}
          {params.id ? (
            <p className="rounded-md border border-border bg-background/70 px-3 py-2 text-xs text-muted-foreground">
              Rooted on this person’s page. Open{" "}
              <span className="font-medium text-foreground">Graph</span> from anywhere to explore others.
            </p>
          ) : (
            <GraphRootPicker
              rootId={personId}
              rootName={rootName}
              onPick={handlePick}
              onClear={handleClearRoot}
            />
          )}

          <GraphFilterPanel
            hopLimit={hopLimit}
            confidenceFloor={confidenceFloor}
            renderMode={renderMode}
            availableKinds={availableKinds}
            hiddenKinds={hiddenKinds}
            onHopLimitChange={setHopLimit}
            onConfidenceFloorChange={setConfidenceFloor}
            onRenderModeChange={setRenderMode}
            onToggleKind={toggleKind}
          />
          <GraphLegend />
        </div>
      ) : null}

      {/* Zero-contacts: import CTAs (reused from OnboardingState). */}
      {tenantEmpty ? <GraphEmptyState /> : null}

      {/* Resolving the bootstrap probe before a default root is known. */}
      {bootstrapResolving ? (
        <div className="flex h-full min-h-[520px] w-full items-center justify-center px-4 md:pl-[33rem]">
          <div className="flex items-center gap-3 rounded-lg border border-border bg-card/85 px-6 py-5 text-sm text-muted-foreground shadow-[var(--shadow-2)] backdrop-blur-xl">
            <Spinner size="sm" label="" />
            <span>Finding a person to show first…</span>
          </div>
        </div>
      ) : null}

      {/* Data exists but no root chosen yet → inviting search-first hero. */}
      {showInvite ? (
        <div className="flex h-full min-h-[520px] w-full items-center justify-center px-4 md:pl-[33rem]">
          <div className="max-w-md space-y-4 rounded-lg border border-border bg-card/80 p-6 text-left shadow-[var(--shadow-2)] backdrop-blur-xl">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-md border border-border bg-background text-primary">
                <Search className="h-5 w-5" strokeWidth={1.8} />
              </div>
              <div>
                <h2 className="text-base font-semibold text-foreground">Search a person to explore their network</h2>
                <p className="text-sm text-muted-foreground">Pick anyone to root the graph on them.</p>
              </div>
            </div>
            <p className="text-sm text-muted-foreground">
              The canvas stays calm until you choose a root. Use the search on the left, then narrow with reach,
              strength, and node-type filters.
            </p>
          </div>
        </div>
      ) : null}

      {/* Loading skeleton while the chosen root's ego graph is fetched. */}
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

      {/* The renderer is unchanged. It only mounts once a root is set and the
          ego graph has settled — the page-level skeleton above owns the loading
          surface, so the canvas never double-renders a placeholder. */}
      {!tenantEmpty && !showInvite && !bootstrapResolving && !graph.isLoading ? (
        <GraphCanvas
          graph={filteredGraph}
          selectedNodeId={selectedNodeId}
          renderMode={renderMode}
          rootPersonId={personId}
          loading={graph.isLoading}
          onNodeClick={(node: GraphNode) => setSelectedNodeId(node.id)}
        />
      ) : null}

      {/* Detail drawer only when a node is selected (keeps the invite uncluttered). */}
      {!tenantEmpty && hasGraph ? (
        <NodeDetailDrawer
          node={selectedNode}
          graph={filteredGraph}
          rootPersonId={personId}
          snapshotAt={snapshotAt}
          onExpand={() => setHopLimit(2)}
        />
      ) : null}
    </div>
  );
}
