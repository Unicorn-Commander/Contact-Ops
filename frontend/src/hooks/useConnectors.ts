/**
 * useConnectors — react-query hooks for the /connectors page.
 *
 * Mirrors the same patterns as `useMcp` / `useVcardImport`:
 *   - Bearer token comes from `react-oidc-context` via `useAccessToken()`.
 *   - 401 redirects to sign-in via `signinRedirect()`.
 *   - Query keys come from the tenant-prefixed factory (`useQueryKeys()`); the
 *     `connectors*` keys are scoped under `["t", <tenant>, "connectors", ...]` so
 *     any mutation can `invalidate({ queryKey: qk.connectorsRoot })` to refetch
 *     the whole surface (and a workspace switch never leaks across tenants).
 *
 * The page composes these hooks; nothing here knows about JSX. Polling for
 * in-flight runs is centralised in `useConnectorRuns` via the `enabledPoll`
 * argument — pass `true` while at least one run is `running` and the query
 * refetches every 3s, false to fall back to the default refetchOnWindowFocus.
 *
 * Contract — verbatim with the Codex backend on `feat-connectors-backend`:
 *   list_connectors() → ListResult<Connector>
 *   configure_icloud_connector({apple_id, app_password, display_name})
 *     → {id, provider:'icloud', status:'configured'}
 *   get_oauth_start_url({provider:'m365'|'gmail'})
 *     → {redirect_url, state}
 *   pull_connector_now({connector_id, dry_run?}) → ConnectorRun
 *   list_connector_runs({connector_id?, limit?}) → ListResult<ConnectorRun>
 *   disconnect_connector({connector_id, reason?}) → {ok:true}
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "react-oidc-context";
import { useAccessToken, useQueryKeys } from "@/hooks/useMcp";
import { UnauthorizedError, tools } from "@/lib/mcp";
import type {
  Connector,
  ConnectorProvider,
  ConnectorRun,
  ListResult,
  UUID
} from "@/lib/types";

/** Poll interval while at least one run is in `running` state. */
export const RUNS_POLL_INTERVAL_MS = 3000;

// ---- Queries ---------------------------------------------------------------

/**
 * `list_connectors()` — all configured connectors for the current user/tenant.
 * Returns a `ListResult<Connector>` so the page can render the empty state
 * uniformly when `items.length === 0`.
 */
export function useConnectors() {
  const auth = useAuth();
  const token = auth.user?.access_token;
  const qk = useQueryKeys();
  return useQuery<ListResult<Connector>>({
    queryKey: qk.connectorsList,
    enabled: Boolean(token),
    queryFn: async () => {
      try {
        return await tools.listConnectors(token ?? "");
      } catch (error) {
        if (error instanceof UnauthorizedError) void auth.signinRedirect();
        throw error;
      }
    }
  });
}

/**
 * `list_connector_runs({connector_id?, limit?})` — recent runs across all (or one)
 * connectors. `enabledPoll` toggles the 3s refetch interval; pass true while any
 * row is `running` and false once everything has settled.
 */
export function useConnectorRuns(
  args: { connector_id?: UUID; limit?: number } = {},
  enabledPoll = false
) {
  const auth = useAuth();
  const token = auth.user?.access_token;
  const qk = useQueryKeys();
  return useQuery<ListResult<ConnectorRun>>({
    queryKey: qk.connectorRuns(args),
    enabled: Boolean(token),
    queryFn: async () => {
      try {
        return await tools.listConnectorRuns(args, token ?? "");
      } catch (error) {
        if (error instanceof UnauthorizedError) void auth.signinRedirect();
        throw error;
      }
    },
    // Poll while any run is in-flight so the spinner + count update without
    // the user having to refresh. The page owns the `enabledPoll` toggle —
    // it knows when a run is running because it just dispatched the mutation.
    refetchInterval: enabledPoll ? RUNS_POLL_INTERVAL_MS : false,
    refetchIntervalInBackground: false
  });
}

// ---- Mutations -------------------------------------------------------------

/**
 * `configure_icloud_connector({apple_id, app_password, display_name})` — stores
 * the iCloud app-specific password and seeds a connector row. The backend
 * surfaces invalid-credentials as a tool error so the caller can render the
 * banner without parsing magic strings out of the message.
 */
export function useConfigureIcloud() {
  const auth = useAuth();
  const token = useAccessToken();
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation<
    { id: UUID; provider: "icloud"; status: "configured" },
    Error,
    { apple_id: string; app_password: string; display_name: string }
  >({
    mutationFn: async (args) => {
      try {
        return await tools.configureIcloudConnector(args, token);
      } catch (error) {
        if (error instanceof UnauthorizedError) {
          void auth.signinRedirect();
        }
        throw error;
      }
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: qk.connectorsRoot });
    }
  });
}

/**
 * `get_oauth_start_url({provider})` — backend mints the redirect URL +
 * state nonce for the M365 / Gmail OAuth dance. The page opens that URL in a
 * popup (preferred) and falls back to a full-page redirect if blocked.
 */
export function useGetOauthStartUrl() {
  const auth = useAuth();
  const token = useAccessToken();
  return useMutation<
    { redirect_url: string; state: string },
    Error,
    { provider: Exclude<ConnectorProvider, "icloud"> }
  >({
    mutationFn: async (args) => {
      try {
        return await tools.getOauthStartUrl(args, token);
      } catch (error) {
        if (error instanceof UnauthorizedError) void auth.signinRedirect();
        throw error;
      }
    }
  });
}

/**
 * `pull_connector_now({connector_id, dry_run?})` — kicks off a pull. Backend
 * may return either a sync result (status `succeeded` / `failed` / `partial`
 * with parsed/proposed counts) or an async `running` row that the runs poll
 * will resolve.
 */
export function usePullConnectorNow() {
  const auth = useAuth();
  const token = useAccessToken();
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation<ConnectorRun, Error, { connector_id: UUID; dry_run?: boolean }>({
    mutationFn: async (args) => {
      try {
        return await tools.pullConnectorNow(args, token);
      } catch (error) {
        if (error instanceof UnauthorizedError) void auth.signinRedirect();
        throw error;
      }
    },
    onSuccess: async () => {
      // Refetch both the connector list (last_pull_at + counts) and the runs
      // table so the just-spawned row appears immediately.
      await queryClient.invalidateQueries({ queryKey: qk.connectorsRoot });
    }
  });
}

/**
 * `disconnect_connector({connector_id, reason?})` — removes the credential
 * material; the connector row itself stays so the user can reconnect later
 * with the same display name + history.
 */
export function useDisconnectConnector() {
  const auth = useAuth();
  const token = useAccessToken();
  const queryClient = useQueryClient();
  const qk = useQueryKeys();
  return useMutation<{ ok: true }, Error, { connector_id: UUID; reason?: string }>({
    mutationFn: async (args) => {
      try {
        return await tools.disconnectConnector(args, token);
      } catch (error) {
        if (error instanceof UnauthorizedError) void auth.signinRedirect();
        throw error;
      }
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: qk.connectorsRoot });
    }
  });
}

// ---- Helpers ---------------------------------------------------------------

/** Any of the rows in `running`? Used to drive `enabledPoll`. */
export function hasInflightRun(runs?: ListResult<ConnectorRun>): boolean {
  // Guard both levels: runs may be undefined (query not yet resolved) AND
  // runs.items may be undefined (server returned an unexpected envelope).
  return Boolean(runs?.items?.some((row) => row.status === "running"));
}
