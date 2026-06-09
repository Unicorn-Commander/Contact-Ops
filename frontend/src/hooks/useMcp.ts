import { useMemo } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "react-oidc-context";
import { UnauthorizedError, tools } from "@/lib/mcp";
import { tenantKey, userKey, type TenantKeyFactory } from "@/lib/queryKeys";
import { env } from "@/lib/env";

export function useAccessToken() {
  const auth = useAuth();
  return auth.user?.access_token ?? "";
}

/**
 * The active workspace id, read from the access-token claim bag.
 *
 * This is derived from the TOKEN, not from React state or the URL — so the
 * instant `signinSilent` swaps the token after a workspace switch, every key
 * built from `useQueryKeys()` re-prefixes automatically.
 *
 * NOTE: the exact claim name is server-granted (see lib/env.ts). We read
 * `tenant_id` first (matches the DB column + the `switch-workspace` response
 * field), then fall back to `tenant` and `active_tenant_id`. Returns "none"
 * before the first token loads so keys are still stable/typed. VERIFY the claim
 * name against a decoded prod token (see the frontend review: this is
 * load-bearing for the post-swap assertion below).
 */
export function useActiveTenantId(): string {
  const auth = useAuth();
  const profile = auth.user?.profile as Record<string, unknown> | undefined;
  const claim =
    profile?.["tenant_id"] ?? profile?.["tenant"] ?? profile?.["active_tenant_id"];
  return typeof claim === "string" && claim.length > 0 ? claim : "none";
}

/** Tenant-scoped query-key factory bound to the live workspace. */
export function useQueryKeys(): TenantKeyFactory {
  const tenantId = useActiveTenantId();
  return useMemo(() => tenantKey(tenantId), [tenantId]);
}

function useAuthedQuery<T>(
  key: readonly unknown[],
  queryFn: (token: string) => Promise<T>,
  enabled = true
) {
  const auth = useAuth();
  const token = auth.user?.access_token;
  return useQuery({
    queryKey: key,
    enabled: Boolean(token) && enabled,
    queryFn: async () => {
      try {
        return await queryFn(token ?? "");
      } catch (error) {
        if (error instanceof UnauthorizedError) void auth.signinRedirect();
        throw error;
      }
    }
  });
}

export const useToolsList = () => {
  const qk = useQueryKeys();
  return useAuthedQuery(qk.tools, tools.list);
};
export const useDashboard = () => {
  const qk = useQueryKeys();
  return useAuthedQuery(qk.dashboard, tools.dashboard);
};
export const useRecentEvents = () => {
  const qk = useQueryKeys();
  return useAuthedQuery(qk.recentEvents, tools.recentEvents);
};
export const usePeople = (args: Record<string, unknown>) => {
  const qk = useQueryKeys();
  // Always use search_people: with an empty query it returns the full directory
  // (tenant-scoped) and it also applies the same filters. list_people requires a
  // narrowing filter and errors on the default (no-search) People view, which left
  // the directory blank until you typed something.
  return useAuthedQuery(qk.people(args), (token) => tools.searchPeople(args, token));
};
/**
 * The WHOLE directory, paged. search_people now returns a real next_cursor
 * (offset), so this walks every page — the People view fetches them all to
 * render an A→Z directory instead of a truncated first page.
 */
export const useAllPeople = (args: { query?: string }) => {
  const auth = useAuth();
  const token = auth.user?.access_token;
  const qk = useQueryKeys();
  return useInfiniteQuery({
    queryKey: qk.peopleAll(args),
    enabled: Boolean(token),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      tools.searchPeople({ ...args, limit: 100, cursor: pageParam }, token ?? ""),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
};
export const usePerson = (id: string) => {
  const qk = useQueryKeys();
  return useAuthedQuery(qk.person(id), (token) => tools.getPerson(id, token), Boolean(id));
};
export const useOrganizations = (args: Record<string, unknown>) => {
  const qk = useQueryKeys();
  return useAuthedQuery(qk.orgs(args), (token) =>
    args.query ? tools.searchOrganizations(args, token) : tools.listOrganizations(args, token)
  );
};
export const useOrganization = (id: string) => {
  const qk = useQueryKeys();
  return useAuthedQuery(qk.org(id), (token) => tools.getOrganization(id, token), Boolean(id));
};
export const useTags = () => {
  const qk = useQueryKeys();
  return useAuthedQuery(qk.tags, tools.listTags);
};
export const useDataCoverage = () => {
  const qk = useQueryKeys();
  return useAuthedQuery(qk.dataCoverage, tools.peopleCoverage);
};
export const usePhotos = (personId: string) => {
  const qk = useQueryKeys();
  return useAuthedQuery(qk.photos(personId), (token) => tools.listPhotos(personId, token));
};
export const useCardDavPasswords = () => {
  const qk = useQueryKeys();
  return useAuthedQuery(qk.carddavPasswords, tools.listCardDavPasswords);
};

/**
 * `useTenants` lists the workspaces the user MAY switch into. It is scoped to
 * the user (uc_uid), NOT the active workspace, so its key is deliberately
 * tenant-INDEPENDENT (lib/queryKeys.ts `userKey.tenants`). Keeping it unprefixed
 * means the switcher dropdown does not blank during a `queryClient.clear()`
 * switch.
 */
export const useTenants = () => useAuthedQuery(userKey.tenants, tools.listTenants);

export function useMcpMutation<TArgs extends Record<string, unknown>>(
  mutationFn: (args: TArgs, token: string) => Promise<unknown>
) {
  const token = useAccessToken();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: TArgs) => mutationFn(args, token),
    onSuccess: async () => {
      await queryClient.invalidateQueries();
    }
  });
}

// ---------------------------------------------------------------------------
// Workspace switch (Phase 4.3 / §4.4)
// ---------------------------------------------------------------------------

/** Discriminated result so the UI can branch on step-up vs hard-deny. */
export type SwitchOutcome =
  | { status: "switched"; tenant_id: string; tenant_slug: string }
  | { status: "step_up" } // caller is being redirected for per-workspace MFA / re-auth
  | { status: "forbidden" } // 403 — no membership / not eligible
  | { status: "unavailable" }; // 503, or claim failed to propagate — do NOT claim success

type SwitchSuccessBody = {
  ok?: boolean;
  tenant_id: string;
  tenant_slug: string;
};

/**
 * `useSwitchWorkspace` — POST {mcpBaseUrl}/api/auth/switch-workspace with the
 * current bearer, then re-scope the SPA to the new workspace via silent re-auth.
 *
 * Backend contract (the task-framing / shipped variant):
 *   200 { ok, tenant_id, tenant_slug }   → silent re-auth re-mints the token
 *   401 { step_up_required: true }       → strict/HIPAA: redirect with max_age=0
 *   401 (no step_up_required)            → dead bearer: plain re-auth
 *   403                                   → no access to workspace
 *   503                                   → switch not configured / transient
 *
 * SECURITY ORDERING (frontend review S1-1 / S3-1): we `clear()` + `cancelQueries()`
 * the cache BEFORE the token swap propagates, then `signinSilent`, then `clear()`
 * AGAIN. The token swap fires react-oidc-context's USER_LOADED, which re-renders
 * the tree with the NEW tenant prefix; clearing only after that line would leave
 * a window where old-tenant cache entries coexist with new-prefix keys. Clearing
 * on both sides (and cancelling in-flight old-token fetches) closes that window.
 * This is a HIPAA/strict boundary: zero tolerance for a stale cross-workspace row.
 *
 * CORRECTNESS ASSERTION (frontend review S2-1): `forceIframeAuth: true` is a
 * transport switch (iframe authorize + prompt=none vs refresh grant), NOT a
 * freshness guarantee. After the silent re-auth we ASSERT the new token's
 * `tenant_id` claim equals the target; on mismatch the claim did not propagate
 * (Keycloak session/mapper cache) and we return `unavailable` rather than falsely
 * reporting success while still showing the old workspace.
 */
export function useSwitchWorkspace() {
  const auth = useAuth();
  const queryClient = useQueryClient();

  return useMutation<SwitchOutcome, Error, string>({
    mutationFn: async (targetTenantId: string): Promise<SwitchOutcome> => {
      const token = auth.user?.access_token ?? "";
      // Bearer-only, matching the rest of lib/mcp.ts (frontend review S3-2):
      // NO `credentials: "include"` — the switch endpoint sets no cookie, and
      // sending credentials would force a stricter CORS contract that can fail
      // the cross-origin call under a wildcard Allow-Origin.
      const res = await fetch(`${env.mcpBaseUrl}/api/auth/switch-workspace`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({ target_tenant_id: targetTenantId })
      });

      if (res.status === 401) {
        // Parse defensively — body may be empty or non-JSON on some gateways.
        const body = await res.json().catch(() => ({}) as Record<string, unknown>);
        if (body?.step_up_required === true) {
          // EXPLICIT per-workspace step-up (strict/HIPAA): force a fresh
          // interactive auth. `max_age: 0` makes Keycloak re-prompt for
          // credentials/MFA even with a live SSO session. (Frontend review S2-2:
          // use the typed first-class field, not extraQueryParams string "0".)
          await auth.signinRedirect({ max_age: 0 });
        } else {
          // No step_up signal → the bearer itself is dead/expired. A plain
          // re-auth (normal silent-renew-then-redirect) suffices; do NOT force a
          // credential re-prompt here (frontend review S2-3).
          await auth.signinRedirect();
        }
        return { status: "step_up" };
      }

      if (res.status === 403) return { status: "forbidden" };
      if (res.status === 503) return { status: "unavailable" };
      if (!res.ok) throw new Error(`switch-workspace failed: HTTP ${res.status}`);

      const data = (await res.json()) as SwitchSuccessBody;

      // (S1-1 / S3-1) Drop old-tenant data and abort in-flight old-token fetches
      // BEFORE the token swap re-renders the tree under the new prefix.
      await queryClient.cancelQueries();
      queryClient.clear();

      // Force a fresh-authorization silent re-auth. `forceIframeAuth: true`
      // routes through the AUTHORIZE endpoint with prompt=none (instead of the
      // refresh_token grant), so Keycloak re-runs protocol mappers against the
      // now-updated user record and mints a token carrying the new tenant_id.
      let newUser;
      try {
        newUser = await auth.signinSilent({ forceIframeAuth: true });
      } catch {
        // prompt=none could not be satisfied (login_required / interaction_required):
        // SSO session expired mid-switch. Fall back to interactive auth.
        await auth.signinRedirect();
        return { status: "step_up" };
      }

      // (S2-1) Post-swap assertion: confirm the new token actually carries the
      // target tenant. `forceIframeAuth` does not GUARANTEE the claim re-minted
      // (Keycloak cache); if it didn't, the prefix never changed and the switch
      // silently no-op'd. Treat a mismatch as failure rather than report success.
      const newProfile = newUser?.profile as Record<string, unknown> | undefined;
      const newClaim =
        newProfile?.["tenant_id"] ??
        newProfile?.["tenant"] ??
        newProfile?.["active_tenant_id"];
      if (newClaim !== targetTenantId) {
        // Belt-and-suspenders: still drop whatever the (wrong-tenant) re-auth may
        // have cached, then signal the UI to NOT claim success.
        queryClient.clear();
        return { status: "unavailable" };
      }

      // (S1-1) Belt-and-suspenders clear after the new token lands, in case a
      // late old-token fetch resolved into cache during the swap window.
      queryClient.clear();

      return {
        status: "switched",
        tenant_id: data.tenant_id,
        tenant_slug: data.tenant_slug
      };
    }
  });
}
