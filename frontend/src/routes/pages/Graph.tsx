import { useEffect, useMemo, useState } from "react";
import { useParams } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Crosshair, PanelRightClose, SlidersHorizontal } from "lucide-react";
import { GraphCanvas, type GraphRenderMode } from "@/components/graph/GraphCanvas";
import { GraphFilterPanel } from "@/components/graph/GraphFilterPanel";
import { NodeDetailDrawer } from "@/components/graph/NodeDetailDrawer";
import { Spinner } from "@/design-system";
import { usePeople } from "@/hooks/useMcp";
import { useEgoGraph, useGraphOverview, type GraphNode } from "@/hooks/useEgoGraph";

export function GraphRoute() {
  const params = useParams({ strict: false }) as { id?: string };
  const [selectedPersonId, setSelectedPersonId] = useState<string | undefined>(undefined);
  const [search, setSearch] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hopLimit, setHopLimit] = useState(2);
  const [confidenceFloor, setConfidenceFloor] = useState(0);
  // Default to 3D — the relationship graph is most legible as an orbitable 3D
  // network, and it's what the owner expects. The 3D node renderer falls back
  // to a default sphere if a custom avatar sprite ever fails (never blank), and
  // users can switch to 2D/Auto in the controls.
  const [renderMode, setRenderMode] = useState<GraphRenderMode>("3d");
  const [snapshotAt, setSnapshotAt] = useState<Date | null>(null);
  // Controls live in a collapsible right-hand sidebar (default open). Bump
  // fitNonce to imperatively re-fit the camera ("Recenter") from the controls.
  const [controlsOpen, setControlsOpen] = useState(true);
  const [fitNonce, setFitNonce] = useState(0);
  // Relationship types the user has toggled OFF in the controls. Persisted across
  // overview ↔ ego switches; a hidden type that isn't present is simply a no-op.
  const [hiddenEdgeTypes, setHiddenEdgeTypes] = useState<Set<string>>(() => new Set());
  const toggleEdgeType = (type: string) =>
    setHiddenEdgeTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  const showAllEdgeTypes = () => setHiddenEdgeTypes(new Set());

  // No person selected → show the whole tenant network (overview). Pick a person
  // (a /graph/:id deep link or the search picker) → drill into their ego network.
  const personId = params.id ?? selectedPersonId;
  const isOverview = !personId;

  const overview = useGraphOverview(isOverview);
  const ego = useEgoGraph(personId, hopLimit, confidenceFloor);
  const graph = isOverview ? overview : ego;

  // People search for the picker. search_people with an empty query returns the
  // directory (we just don't render it until the user types).
  const peopleQuery = usePeople({ query: search.trim(), limit: 8 });
  const results = search.trim() ? (peopleQuery.data?.items ?? []) : [];

  useEffect(() => {
    setSnapshotAt(graph.data ? new Date() : null);
  }, [graph.data]);

  const selectedNode = useMemo(() => {
    if (!graph.data || !selectedNodeId) return null;
    return graph.data.nodes.find((node) => node.id === selectedNodeId) ?? null;
  }, [graph.data, selectedNodeId]);

  useEffect(() => {
    if (!selectedNodeId) return;
    if (!graph.data?.nodes?.some((node) => node.id === selectedNodeId)) {
      setSelectedNodeId(null);
    }
  }, [graph.data, selectedNodeId]);

  const nodeCount = graph.data?.node_count ?? 0;
  const edgeCount = graph.data?.edge_count ?? 0;
  const hasGraph = Boolean(graph.data);

  // Distinct relationship types in the current graph (with counts), most common
  // first — drives the association filter chips. Computed from the UNFILTERED
  // data so a toggled-off type's chip stays put rather than vanishing.
  const edgeTypes = useMemo(() => {
    const counts = new Map<string, number>();
    for (const link of graph.data?.links ?? []) {
      counts.set(link.type, (counts.get(link.type) ?? 0) + 1);
    }
    return [...counts.entries()]
      .map(([type, count]) => ({ type, count }))
      .sort((a, b) => b.count - a.count || a.type.localeCompare(b.type));
  }, [graph.data]);

  function pickPerson(id: string) {
    setSelectedPersonId(id);
    setSearch("");
    setSelectedNodeId(null);
  }

  function backToOverview() {
    setSelectedPersonId(undefined);
    setSelectedNodeId(null);
  }

  // The controls sidebar and the node-detail drawer both occupy the right edge,
  // so keep them mutually exclusive: opening one dismisses the other.
  function openControls() {
    setSelectedNodeId(null);
    setControlsOpen(true);
  }
  function handleNodeSelect(node: GraphNode) {
    setSelectedNodeId(node.id);
    setControlsOpen(false);
  }
  function recenter() {
    setFitNonce((value) => value + 1);
  }

  return (
    <div className="relative isolate -m-4 min-h-[calc(100svh-4rem)] overflow-hidden bg-background text-foreground lg:-m-6">
      <div className="absolute inset-0 bg-background" aria-hidden="true" />

      {/* Context header — always visible, compact. Title, live counts, the
          focus-reset, and (when the sidebar is collapsed) the button that
          reopens it. Top-left so it never sits under the right-side controls
          or node-detail panels. */}
      <div className="absolute left-3 top-3 z-20 w-[min(20rem,calc(100vw-1.5rem))] rounded-lg border border-border bg-card/90 p-3 shadow-[var(--shadow-3)] backdrop-blur-xl md:left-4 md:top-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-semibold leading-7 tracking-tight">Graph</h1>
              {renderMode === "3d" ? <Badge variant="secondary">3D</Badge> : null}
              {renderMode === "2d" ? <Badge variant="secondary">2D</Badge> : null}
              {renderMode === "auto" ? <Badge variant="outline">Auto</Badge> : null}
              {graph.isLoading ? <Spinner size="sm" label="Loading graph" /> : null}
            </div>
            <p className="text-sm text-muted-foreground">
              {hasGraph ? (
                <>
                  <span className="co-mono-numeric font-mono">
                    <strong>{nodeCount}</strong> nodes / <strong>{edgeCount}</strong> edges
                  </span>{" "}
                  {isOverview ? "across your whole network" : "in this ego network"}
                </>
              ) : isOverview ? (
                "Your whole contact network"
              ) : (
                "Ego network"
              )}
            </p>
            {selectedPersonId ? (
              <Button variant="outline" size="sm" className="mt-2" onClick={backToOverview}>
                Whole network
              </Button>
            ) : null}
          </div>
          {!controlsOpen ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={openControls}
              aria-expanded={controlsOpen}
              title="Show controls"
              className="shrink-0 gap-1.5"
            >
              <SlidersHorizontal className="h-4 w-4" />
              Controls
            </Button>
          ) : null}
        </div>
      </div>

      {/* Right-hand collapsible controls sidebar (a bottom sheet on mobile).
          Person picker + filters + render mode + Recenter live here; collapse
          it to give the canvas the full width. Mutually exclusive with the
          node-detail drawer, which shares the right edge. */}
      {controlsOpen ? (
        <aside className="co-scrollbar absolute inset-x-3 bottom-3 z-30 max-h-[60svh] space-y-4 overflow-y-auto rounded-lg border border-border bg-card/95 p-4 shadow-[var(--shadow-3)] backdrop-blur-xl md:inset-x-auto md:inset-y-4 md:right-4 md:bottom-auto md:max-h-none md:w-[min(22rem,calc(100vw-2rem))]">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
              <h2 className="text-sm font-semibold tracking-tight">Controls</h2>
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setControlsOpen(false)}
              title="Collapse controls"
              aria-label="Collapse controls"
              className="h-8 w-8 text-muted-foreground hover:text-foreground"
            >
              <PanelRightClose className="h-4 w-4" />
            </Button>
          </div>

          {/* Person picker — type a name to center the graph on one contact; a
              /graph/:id deep link pins it and hides the picker. */}
          {!params.id ? (
            <div className="space-y-2">
              <label
                className="text-xs font-medium uppercase tracking-wide text-muted-foreground"
                htmlFor="graph-person-search"
              >
                {selectedPersonId ? "Focused on a contact" : "Search a contact to focus"}
              </label>
              <Input
                id="graph-person-search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search people by name…"
                className="bg-background"
              />
              {results.length > 0 ? (
                <ul className="co-scrollbar max-h-56 overflow-y-auto rounded-md border border-border bg-card">
                  {results.map((person) => (
                    <li key={person.person_id}>
                      <button
                        type="button"
                        onClick={() => pickPerson(person.person_id)}
                        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-muted"
                      >
                        <span className="truncate font-medium text-foreground">
                          {person.display_name}
                        </span>
                        {person.current_org ? (
                          <span className="truncate text-xs text-muted-foreground">
                            {person.current_org}
                          </span>
                        ) : null}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          <GraphFilterPanel
            hopLimit={hopLimit}
            confidenceFloor={confidenceFloor}
            renderMode={renderMode}
            edgeTypes={edgeTypes}
            hiddenEdgeTypes={hiddenEdgeTypes}
            onHopLimitChange={setHopLimit}
            onConfidenceFloorChange={setConfidenceFloor}
            onRenderModeChange={setRenderMode}
            onToggleEdgeType={toggleEdgeType}
            onShowAllEdgeTypes={showAllEdgeTypes}
          />

          <Button variant="outline" size="sm" className="w-full gap-2" onClick={recenter}>
            <Crosshair className="h-4 w-4" />
            Recenter view
          </Button>
        </aside>
      ) : null}

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

      {!graph.isLoading && hasGraph && nodeCount === 0 ? (
        <div className="absolute inset-0 flex items-center justify-center px-4">
          <div className="max-w-md rounded-lg border border-border bg-card/80 p-6 text-center text-sm text-muted-foreground shadow-[var(--shadow-2)] backdrop-blur-xl">
            No connections to show yet. As contacts share organizations or get
            linked, they&apos;ll appear here.
          </div>
        </div>
      ) : null}

      <GraphCanvas
        graph={graph.data}
        selectedNodeId={selectedNodeId}
        renderMode={renderMode}
        rootPersonId={personId}
        hiddenEdgeTypes={hiddenEdgeTypes}
        loading={graph.isLoading}
        fitNonce={fitNonce}
        onNodeClick={handleNodeSelect}
        onBackgroundClick={() => setSelectedNodeId(null)}
      />

      {selectedNode ? (
        <NodeDetailDrawer
          node={selectedNode}
          graph={graph.data}
          rootPersonId={personId}
          snapshotAt={snapshotAt}
          onExpand={() => setHopLimit(2)}
          onClose={() => setSelectedNodeId(null)}
        />
      ) : null}
    </div>
  );
}
