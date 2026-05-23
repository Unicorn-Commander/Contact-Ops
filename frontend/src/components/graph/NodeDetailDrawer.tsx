import { Network } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { GraphNode } from "@/hooks/useEgoGraph";

export function NodeDetailDrawer({
  node,
  onExpand
}: {
  node: GraphNode | null;
  onExpand: () => void;
}) {
  return (
    <aside className="absolute right-0 top-0 h-full w-80 border-l bg-background/95 p-5 shadow-xl backdrop-blur">
      {node ? (
        <div className="space-y-5">
          <div>
            <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-md bg-emerald-500/15 text-emerald-400">
              <Network className="h-5 w-5" />
            </div>
            <h2 className="text-lg font-semibold">{node.name}</h2>
            <p className="text-sm text-muted-foreground">{node.kind}</p>
          </div>
          <dl className="space-y-3 text-sm">
            <div>
              <dt className="text-xs text-muted-foreground">Tenant</dt>
              <dd className="break-all">{node.tenant_id ?? "Current tenant"}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Graph ID</dt>
              <dd className="break-all font-mono text-xs">{node.id}</dd>
            </div>
          </dl>
          <Button className="w-full" onClick={onExpand}>
            Expand 2nd-degree
          </Button>
        </div>
      ) : (
        <div className="flex h-full items-center text-sm text-muted-foreground">Select a node</div>
      )}
    </aside>
  );
}
