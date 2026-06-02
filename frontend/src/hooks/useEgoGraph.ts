import { useQuery } from "@tanstack/react-query";
import { useAuth } from "react-oidc-context";
import { useQueryKeys } from "@/hooks/useMcp";
import { callTool } from "@/lib/mcp";
import { colorForEdge, colorForNode } from "@/lib/graph/colorScheme";

export type GraphNode = {
  id: string;
  name: string;
  kind: string;
  group: string;
  val?: number;
  color?: string;
  tenant_id?: string | null;
};

export type GraphLink = {
  source: string;
  target: string;
  type: string;
  strength?: number | null;
  color?: string;
};

export type EgoGraph = {
  nodes: GraphNode[];
  links: GraphLink[];
  node_count: number;
  edge_count: number;
  truncated: boolean;
};

export function useEgoGraph(personId?: string, hopLimit = 1, confidenceFloor = 0) {
  const auth = useAuth();
  const qk = useQueryKeys();
  return useQuery({
    queryKey: qk.egoGraph(personId, hopLimit, confidenceFloor),
    enabled: Boolean(personId && auth.user?.access_token),
    queryFn: async () => {
      const graph = await callTool<EgoGraph>(
        "extract_ego_graph",
        { person_id: personId, hop_limit: hopLimit, limit: 500 },
        auth.user?.access_token ?? ""
      );
      return {
        ...graph,
        nodes: graph.nodes.map((node) => ({ ...node, color: colorForNode(node.kind) })),
        links: graph.links
          .filter((link) => (link.strength ?? 1) >= confidenceFloor)
          .map((link) => ({ ...link, color: colorForEdge(link.type) }))
      };
    }
  });
}
