import { useQuery } from "@tanstack/react-query";
import { useAuth } from "react-oidc-context";
import { useQueryKeys } from "@/hooks/useMcp";
import { tools } from "@/lib/mcp";

/**
 * Bootstrap probe for the Graph page.
 *
 * The graph hook (`useEgoGraph`) needs a ROOT person id to draw anything. To
 * avoid forcing the user to paste a UUID, the page wants to (a) know whether
 * the tenant has ANY people at all (0-contacts → import empty state, not a
 * "pick a root" state) and (b) auto-pick a sensible default root so the canvas
 * shows something immediately.
 *
 * Both answers come from a single cheap `list_people` call — NO new backend
 * endpoint. There is no "most-connected" ranking exposed to the client and the
 * access token carries no person mapping, so the pragmatic default root is the
 * first person the directory returns (recently-active / alphabetical per the
 * backend's default ordering). `total_count` (when present) is preferred for
 * the has-people signal so a single-page probe still reflects the true size.
 */

export type GraphBootstrap = {
  /** True once we know the tenant has at least one person. */
  hasPeople: boolean;
  /** A sensible default root to draw before the user picks one (or undefined). */
  defaultRootId?: string;
  /** Display name for the default root, for friendly "Showing X" copy. */
  defaultRootName?: string;
  isLoading: boolean;
  isError: boolean;
};

// A small page of people is enough to both detect emptiness and choose a root;
// keep it tiny so the probe is near-free and never competes with the directory.
const PROBE_LIMIT = 5;

export function useGraphBootstrap(enabled = true): GraphBootstrap {
  const auth = useAuth();
  const qk = useQueryKeys();
  const token = auth.user?.access_token ?? "";

  const query = useQuery({
    queryKey: qk.graphBootstrap,
    enabled: enabled && Boolean(token),
    // The set of people changes rarely relative to a graph-exploration session;
    // a short stale window avoids re-probing on every remount of the page.
    staleTime: 60_000,
    queryFn: () => tools.listPeople({ limit: PROBE_LIMIT, include_archived: false }, token)
  });

  const items = query.data?.items ?? [];
  const count = query.data?.total_count ?? query.data?.count ?? items.length;
  const first = items[0];

  return {
    hasPeople: count > 0,
    defaultRootId: first?.person_id,
    defaultRootName: first?.display_name,
    isLoading: query.isLoading,
    isError: query.isError
  };
}
