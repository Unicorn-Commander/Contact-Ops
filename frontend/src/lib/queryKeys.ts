/**
 * Single source of truth for React Query keys.
 *
 * EVERY workspace-scoped key is prefixed with the active tenant id (`["t", <id>]`)
 * so that:
 *   1. a workspace switch can rely on `queryClient.clear()` to guarantee no
 *      cross-workspace row ever paints, and
 *   2. even before `clear()` runs, the key prefix changes the instant the token
 *      swaps — so stale-tenant cache entries are abandoned automatically
 *      (defense in depth; HIPAA/strict tenants must never bleed — see
 *      Phase-4 design §6 / §3.8).
 *
 * The custom ESLint rule `local-rules/query-key-tenant-prefix` fails the build if
 * any `useQuery`/`useInfiniteQuery` key ARRAY LITERAL does not start with the
 * `"t"` (tenant) or `"g"` (global) sentinel. Keep new keys flowing through this
 * module. (The rule only catches inline array literals — real enforcement is the
 * discipline of building every key here; see the review's note on the guard's
 * blind spot for identifiers/calls.)
 *
 * Keys that are deliberately tenant-INDEPENDENT (the workspace list itself, a
 * user's PATs) are built with `globalKey(...)` / `userKey` and survive a switch.
 */

export type TenantId = string;

/** Workspace-independent keys (survive a `queryClient.clear()`-driven switch). */
export function globalKey<T extends readonly unknown[]>(...parts: T) {
  return ["g", ...parts] as const;
}

/**
 * Build a tenant-scoped key factory bound to one active tenant id.
 *
 * Usage in a hook:
 *   const qk = useQueryKeys();           // reads the active tenant from the token
 *   useQuery({ queryKey: qk.person(id), ... })
 */
export function tenantKey(tenantId: TenantId) {
  const base = ["t", tenantId] as const;
  return {
    // --- core (useMcp.ts) ---
    tools: [...base, "tools"] as const,
    dashboard: [...base, "dashboard"] as const,
    recentEvents: [...base, "events", "recent"] as const,
    people: (args: Record<string, unknown>) => [...base, "people", args] as const,
    peopleAll: (args: Record<string, unknown>) => [...base, "people-all", args] as const,
    person: (id: string) => [...base, "person", id] as const,
    orgs: (args: Record<string, unknown>) => [...base, "orgs", args] as const,
    org: (id: string) => [...base, "org", id] as const,
    tags: [...base, "tags"] as const,
    dataCoverage: [...base, "data-coverage"] as const,
    dataQuality: [...base, "data-quality"] as const,
    photos: (personId: string) => [...base, "photos", personId] as const,
    carddavPasswords: [...base, "carddav-passwords"] as const,
    workspaceMembers: [...base, "workspace-members"] as const,

    // --- agent command center (useAgents.ts) ---
    // Trust + governance are per-tenant (the backend filters agent_trust by
    // tenant_id and governance is workspace-scoped), so these carry the prefix.
    agents: [...base, "agents"] as const,
    agentGovernance: [...base, "agents", "governance"] as const,
    agentTrust: (slug: string) => [...base, "agents", "trust", slug] as const,

    // --- inbox (useInbox.ts) ---
    inboxRoot: [...base, "inbox"] as const,
    inbox: (filters: unknown) => [...base, "inbox", filters] as const,

    // --- connectors (useConnectors.ts) ---
    connectorsRoot: [...base, "connectors"] as const,
    connectorsList: [...base, "connectors", "list"] as const,
    connectorRuns: (args: unknown) => [...base, "connectors", "runs", args] as const,

    // --- notes (useNotes.ts) ---
    notesRoot: [...base, "notes"] as const,
    notes: (targetType: string, targetId: string | null, pageSize: number) =>
      [...base, "notes", targetType, targetId ?? null, pageSize] as const,
    notesScope: (targetType: string, targetId: string | null) =>
      [...base, "notes", targetType, targetId ?? null] as const,

    // --- ego graph (useEgoGraph.ts) ---
    egoGraph: (
      personId: string | undefined,
      hopLimit: number,
      confidenceFloor: number
    ) => [...base, "ego-graph", personId, hopLimit, confidenceFloor] as const,

    // --- command palette (CommandPalette.tsx) ---
    cmdkPeople: (q: string) => [...base, "cmdk", "people", q] as const,
    cmdkOrgs: (q: string) => [...base, "cmdk", "orgs", q] as const,
    cmdkTags: [...base, "cmdk", "tags"] as const,

    // --- inbox evidence panel (EvidencePanel.tsx) ---
    proposalEvidence: (proposalId: string) =>
      [...base, "proposal-evidence", proposalId] as const
  } as const;
}

/**
 * Keys that are PER-USER, not per-tenant.
 *
 *  - `personalAccessTokens` and the `tenants` list are scoped to the calling
 *    user (uc_uid), not the active workspace. We keep them tenant-INDEPENDENT so
 *    the switcher's own workspace list does not blank during a switch, and so a
 *    user's PATs (which span workspaces) are not needlessly refetched. These are
 *    allow-listed by the lint rule's `"g"` sentinel.
 */
export const userKey = {
  tenants: globalKey("tenants"),
  personalAccessTokens: globalKey("personal-access-tokens")
} as const;

export type TenantKeyFactory = ReturnType<typeof tenantKey>;
