import { env } from "@/lib/env";
import type {
  ActionEvent,
  AgentBreakerResult,
  AgentGovernance,
  BulkActionResult,
  CanonicalizeTagsResult,
  CardDavPassword,
  CleanOrgNamesResult,
  Connector,
  GetAgentTrustResult,
  ListAgentsResult,
  TierShiftResult,
  ConnectorProvider,
  ConnectorRun,
  DataQualityReport,
  ListPendingProposalsResult,
  ListResult,
  MergeOrganizationsResult,
  MergePeopleResult,
  Note,
  NoteTargetType,
  Organization,
  OrganizationSummary,
  Person,
  PersonalAccessToken,
  PersonalAccessTokenIssued,
  PersonSummary,
  Photo,
  ProposalEvidence,
  Tag,
  Tenant,
  Tier,
  UUID,
  ListWorkspaceMembersResult,
  WorkspaceRole
} from "@/lib/types";

type MCPRequest<P = unknown> = {
  jsonrpc: "2.0";
  method: "tools/list" | "tools/call" | "initialize";
  params?: P;
  id: string;
};

type MCPResponse<R = unknown> = {
  jsonrpc: "2.0";
  result?: R;
  error?: { code: number; message: string };
  id: string;
};

export class UnauthorizedError extends Error {
  constructor() {
    super("Authentication expired");
  }
}

export class MCPError extends Error {
  constructor(
    public code: number | string,
    message: string
  ) {
    super(message);
  }
}

// ---- Server-Sent Events helper for /chat ----------------------------------
//
// Posts a JSON body and yields parsed event objects from the text/event-stream
// response. The shape of each yielded event is whatever the server packs into
// a `data: ...` line — by convention an object with an `event` discriminator
// and event-specific fields. See `hooks/useChat.ts` for the discriminated
// union the chat consumes.

export type SSEEvent = Record<string, unknown> & { event?: string };

export type SSEStreamOptions = {
  signal?: AbortSignal;
};

/**
 * POST a JSON body to a same-origin endpoint and yield parsed `data:` events
 * from the SSE response. Bearer token is added by the caller via `token`.
 *
 * Throws `UnauthorizedError` on 401 (matches the MCP fetch pattern), or a
 * plain Error on transport / parse failures.
 */
export async function* sseStream(
  path: string,
  body: unknown,
  token: string,
  options: SSEStreamOptions = {}
): AsyncGenerator<SSEEvent, void, unknown> {
  const response = await fetch(`${env.mcpBaseUrl}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      Accept: "text/event-stream"
    },
    body: JSON.stringify(body),
    signal: options.signal
  });

  if (response.status === 401) throw new UnauthorizedError();
  if (!response.ok || !response.body) {
    throw new MCPError(response.status, `SSE request failed with HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line (\n\n). Tolerate \r\n\r\n too.
      let separator = buffer.indexOf("\n\n");
      while (separator === -1) {
        const crlf = buffer.indexOf("\r\n\r\n");
        if (crlf !== -1) {
          separator = crlf;
          break;
        }
        break;
      }

      while (separator !== -1) {
        const frame = buffer.slice(0, separator);
        buffer = buffer.slice(separator + (buffer[separator] === "\r" ? 4 : 2));

        // A frame may contain multiple lines; concatenate only the `data:`
        // payloads. Lines like `event:` / `id:` / `:keepalive` are ignored —
        // the discriminator lives inside the JSON payload by contract.
        const dataLines: string[] = [];
        for (const rawLine of frame.split(/\r?\n/)) {
          if (rawLine.startsWith("data:")) {
            dataLines.push(rawLine.slice(5).trim());
          }
        }
        if (dataLines.length > 0) {
          const payload = dataLines.join("\n");
          try {
            const parsed = JSON.parse(payload) as SSEEvent;
            yield parsed;
          } catch {
            // Malformed JSON in a `data:` line — surface as an error event so
            // the caller can render it instead of silently dropping bytes.
            yield { event: "error", message: `Malformed SSE payload: ${payload.slice(0, 120)}` };
          }
        }

        separator = buffer.indexOf("\n\n");
        if (separator === -1) {
          const crlf = buffer.indexOf("\r\n\r\n");
          if (crlf !== -1) separator = crlf;
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export async function mcpCall<R>(method: MCPRequest["method"], params: unknown, token: string): Promise<R> {
  const response = await fetch(`${env.mcpBaseUrl}/mcp`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      method,
      params,
      id: crypto.randomUUID()
    } satisfies MCPRequest)
  });

  if (response.status === 401) throw new UnauthorizedError();
  if (!response.ok) throw new MCPError(response.status, `MCP request failed with HTTP ${response.status}`);

  const body = (await response.json()) as MCPResponse<unknown>;
  if (body.error) throw new MCPError(body.error.code, body.error.message);
  return unwrapMcpResult<R>(body.result);
}

/**
 * Unwrap the MCP `tools/call` envelope.
 *
 * Every backend tool wraps its return value as:
 *   { content: [{type: "text", text: <JSON>}],
 *     structuredContent: <typed Pydantic dump>,
 *     isError: boolean }
 *
 * - On `isError: true`, throw an MCPError with the human-readable message
 *   extracted from `content[0].text` (which can itself be JSON-encoded with
 *   `code` + `message` — try to parse, fall back to the raw text).
 * - On success, return `structuredContent` as the typed result; if it's
 *   missing, fall back to parsing `content[0].text`, then to the raw value.
 *
 * Non-tools/call responses (e.g. `initialize`, `tools/list`) don't use the
 * envelope; they pass through unchanged.
 */
function unwrapMcpResult<R>(result: unknown): R {
  if (!result || typeof result !== "object") return result as R;
  const env = result as { isError?: boolean; content?: Array<{ type?: string; text?: string }>; structuredContent?: unknown };

  if (env.isError === true) {
    const firstText = Array.isArray(env.content) && env.content[0]?.type === "text" ? env.content[0]!.text ?? "" : "";
    let code: string | number = "TOOL_ERROR";
    let message = firstText || "Tool error";
    try {
      const parsed = JSON.parse(firstText);
      if (parsed && typeof parsed === "object") {
        const p = parsed as { code?: unknown; message?: unknown };
        if (typeof p.code === "string" || typeof p.code === "number") code = p.code;
        if (typeof p.message === "string") message = p.message;
      }
    } catch {
      // not JSON; firstText is already the message
    }
    throw new MCPError(code, message);
  }

  if ("structuredContent" in env && env.structuredContent !== undefined) {
    return env.structuredContent as R;
  }
  // Last resort: try to JSON.parse content[0].text
  const firstText = Array.isArray(env.content) && env.content[0]?.type === "text" ? env.content[0]!.text ?? "" : "";
  if (firstText) {
    try {
      return JSON.parse(firstText) as R;
    } catch {
      // Pass through
    }
  }
  return result as R;
}

export function callTool<R>(name: string, args: Record<string, unknown>, token: string) {
  return mcpCall<R>("tools/call", { name, arguments: args }, token);
}

export const tools = {
  list: (token: string) => mcpCall<{ tools: Array<{ name: string; description?: string }> }>("tools/list", {}, token),
  dashboard: async (token: string) => {
    const [people, orgs, tenants] = await Promise.all([
      callTool<ListResult<PersonSummary>>("search_people", { limit: 1 }, token),
      callTool<ListResult<OrganizationSummary>>("search_organizations", { limit: 1 }, token),
      callTool<ListResult<Tenant>>("list_tenants", { limit: 25 }, token)
    ]);
    return { people, orgs, tenants };
  },
  recentEvents: (token: string) =>
    callTool<ListResult<ActionEvent>>("list_action_events", { limit: 50 }, token).catch(() => ({
      items: [],
      count: 0
    })),
  listPeople: (args: Record<string, unknown>, token: string) => callTool<ListResult<PersonSummary>>("list_people", args, token),
  searchPeople: (args: Record<string, unknown>, token: string) =>
    callTool<ListResult<PersonSummary>>("search_people", args, token),
  /**
   * Field coverage for the dashboard "Data health" card. `search_people` caps at
   * 100 rows and exposes no cursor, so coverage is computed over a single
   * up-to-100 sample and labelled honestly (the registry here is ~800 contacts).
   * Only EMAIL and ORGANIZATION are reported: `primary_phone` is not in the
   * search payload and `tags` is not hydrated by search — either would render a
   * misleading 0%, so both are deliberately omitted rather than faked.
   */
  peopleCoverage: async (token: string) => {
    const res = await callTool<ListResult<PersonSummary>>("search_people", { limit: 100 }, token);
    const items = res.items ?? [];
    const n = items.length;
    const total = res.total_count ?? n;
    const share = (count: number) => (n ? count / n : 0);
    const withEmail = items.filter((p) => p.primary_email != null && String(p.primary_email).trim() !== "").length;
    const withOrg = items.filter((p) => p.current_org_id != null).length;
    const coverage = [
      { label: "Has an email", value: share(withEmail) },
      { label: "Linked to an org", value: share(withOrg) }
    ];
    const note =
      n > 0 && n < total
        ? `Across a sample of ${n} of ${total.toLocaleString()} contacts`
        : `Across all ${total.toLocaleString()} contacts`;
    return { coverage, note, sampleSize: n, total };
  },
  getPerson: (person_id: UUID, token: string) =>
    callTool<Person>(
      "get_person",
      {
        person_id,
        include: [
          "emails",
          "phones",
          "addresses",
          "identifiers",
          "urls",
          "im_handles",
          "tags",
          "notes",
          "relationships",
          "employment",
          "field_provenance"
        ],
        latest_interactions_limit: 20
      },
      token
    ),
  createPerson: (args: Record<string, unknown>, token: string) => callTool("create_person", args, token),
  updatePerson: (args: Record<string, unknown>, token: string) => callTool("update_person", args, token),
  archivePerson: (args: Record<string, unknown>, token: string) => callTool("archive_person", args, token),
  setRelationshipCategory: (args: Record<string, unknown>, token: string) =>
    callTool("set_relationship_category", args, token),
  listOrganizations: (args: Record<string, unknown>, token: string) =>
    callTool<ListResult<OrganizationSummary>>("list_organizations", args, token),
  searchOrganizations: (args: Record<string, unknown>, token: string) =>
    callTool<ListResult<OrganizationSummary>>("search_organizations", args, token),
  getOrganization: (org_id: UUID, token: string) => callTool<Organization>("get_organization", { org_id }, token),
  createOrganization: (args: Record<string, unknown>, token: string) => callTool("create_organization", args, token),
  updateOrganization: (args: Record<string, unknown>, token: string) => callTool("update_organization", args, token),
  archiveOrganization: (args: Record<string, unknown>, token: string) => callTool("archive_organization", args, token),
  listTags: async (token: string) => {
    // Backend list_tags returns { tag, person_count, org_count, ... }; the Tag
    // type the UI consumes is { slug, display_name, usage_count }. Map the
    // contract here so the tag cloud, the Tags stat strip, and the People
    // filter chips render real names + counts (they were blank dots / zeros before).
    const res = await callTool<{ items: Array<Record<string, unknown>>; count: number; next_cursor?: string | null }>(
      "list_tags",
      { limit: 200 },
      token
    );
    const items: Tag[] = (res.items ?? []).map((raw) => {
      const slug = String(raw.tag ?? raw.slug ?? "").trim();
      const usage = Number(raw.person_count ?? raw.usage_count ?? 0) + Number(raw.org_count ?? 0);
      const display_name = slug
        .replace(/[-_]+/g, " ")
        .replace(/\b\w/g, (c) => c.toUpperCase())
        .trim();
      return { slug, display_name: display_name || slug, usage_count: usage };
    });
    return { items, count: res.count ?? items.length, next_cursor: res.next_cursor ?? null } as ListResult<Tag>;
  },
  applyTags: (args: Record<string, unknown>, token: string) => callTool("apply_tags", args, token),
  listTenants: (token: string) => callTool<ListResult<Tenant>>("list_tenants", { limit: 100 }, token),
  createTenant: (args: Record<string, unknown>, token: string) => callTool("create_tenant", args, token),
  updateTenantSettings: (args: Record<string, unknown>, token: string) => callTool("update_tenant_settings", args, token),

  // ---- Workspace members (Team — backend mcp/tools/tenancy.py) ----
  // Admin-only management of the current workspace's user_tenant_membership.
  listWorkspaceMembers: (token: string, args: { include_inactive?: boolean } = {}) =>
    callTool<ListWorkspaceMembersResult>("list_workspace_members", args, token),
  inviteWorkspaceMember: (
    args: { email: string; role?: WorkspaceRole; is_default?: boolean },
    token: string
  ) =>
    callTool<{ user_uc_uid: string; email: string; role: WorkspaceRole; status: string; created: boolean }>(
      "invite_workspace_member",
      args as unknown as Record<string, unknown>,
      token
    ),
  updateWorkspaceMemberRole: (
    args: { user_uc_uid: string; role: WorkspaceRole },
    token: string
  ) =>
    callTool<{ user_uc_uid: string; role: WorkspaceRole; status: string }>(
      "update_workspace_member_role",
      args as unknown as Record<string, unknown>,
      token
    ),
  revokeWorkspaceMember: (args: { user_uc_uid: string }, token: string) =>
    callTool<{ user_uc_uid: string; status: string }>(
      "revoke_workspace_member",
      args as unknown as Record<string, unknown>,
      token
    ),
  uploadPhoto: (args: Record<string, unknown>, token: string) => callTool("upload_photo", args, token),
  confirmPhotoUpload: (args: Record<string, unknown>, token: string) => callTool("confirm_photo_upload", args, token),
  listPhotos: (person_id: UUID, token: string) => callTool<ListResult<Photo>>("list_photos", { person_id }, token),
  listCardDavPasswords: (token: string) =>
    callTool<ListResult<CardDavPassword>>("list_carddav_app_passwords", {}, token).catch(() => ({ items: [], count: 0 })),
  generateCardDavPassword: (args: Record<string, unknown>, token: string) =>
    callTool("generate_carddav_app_password", args, token),
  revokeCardDavPassword: (args: Record<string, unknown>, token: string) => callTool("revoke_carddav_app_password", args, token),

  // ---- Personal Access Tokens (matches Codex PAT backend) ----
  //
  // Plaintext is shown exactly once on issue. List/revoke never carry it.
  issuePat: (
    args: { display_name: string; scopes?: string[]; expires_in_days?: number | null },
    token: string
  ) =>
    callTool<PersonalAccessTokenIssued>(
      "issue_personal_access_token",
      args as unknown as Record<string, unknown>,
      token
    ),
  listPats: (token: string) =>
    callTool<ListResult<PersonalAccessToken>>("list_personal_access_tokens", {}, token).catch(() => ({
      items: [],
      count: 0
    })),
  revokePat: (args: { pat_id: UUID }, token: string) =>
    callTool<{ ok: true }>(
      "revoke_personal_access_token",
      args as unknown as Record<string, unknown>,
      token
    ),

  // ---- Data quality (matches backend mcp/tools/data_quality.py) ----
  dataQualityReport: (token: string, args: { sample_limit?: number } = {}) =>
    callTool<DataQualityReport>("data_quality_report", args, token),
  cleanOrgNames: (
    args: { org_ids?: UUID[]; dry_run?: boolean },
    token: string
  ) => callTool<CleanOrgNamesResult>("clean_org_names", args, token),
  canonicalizeTags: (args: { dry_run?: boolean }, token: string) =>
    callTool<CanonicalizeTagsResult>("canonicalize_tags", args, token),
  mergeOrganizations: (
    args: { survivor_id: UUID; loser_ids: UUID[]; dry_run?: boolean },
    token: string
  ) => callTool<MergeOrganizationsResult>("merge_organizations", args, token),
  unmergeOrganizations: (args: { merge_event_id: UUID }, token: string) =>
    callTool<{ survivor_id: UUID; restored: UUID[]; roles_restored: number; status: string }>(
      "unmerge_organizations",
      args,
      token
    ),
  mergePeople: (
    args: { survivor_id: UUID; loser_ids: UUID[]; dry_run?: boolean },
    token: string
  ) => callTool<MergePeopleResult>("merge_people", args, token),
  unmergePeople: (args: { merge_event_id: UUID }, token: string) =>
    callTool<{ survivor_id: UUID; restored: UUID[]; edges_restored: number; status: string }>(
      "unmerge_people",
      args,
      token
    ),

  // ---- Phase 3.3 inbox tools ----
  listPendingProposals: (args: Record<string, unknown>, token: string) =>
    callTool<ListPendingProposalsResult>("list_pending_proposals", args, token),
  approveProposal: (
    args: {
      proposal_id: UUID;
      tier_assigned: Tier;
      field_choices?: Record<string, "master" | "proposed" | "custom"> | null;
      custom_values?: Record<string, unknown> | null;
      typed_phrase?: string | null;
      time_to_decide_sec?: number | null;
      keyboard_path?: boolean;
      typed_phrase_used?: boolean;
    },
    token: string
  ) =>
    callTool<{ applied: boolean; action_event_id: UUID; inbox_decision_id: UUID }>(
      "approve_proposal",
      args as unknown as Record<string, unknown>,
      token
    ),
  rejectProposal: (
    args: {
      proposal_id: UUID;
      mode?: "reject" | "dismiss_duplicate" | "mute" | "undo";
      reason?: string | null;
      tier_assigned?: Tier;
      time_to_decide_sec?: number | null;
      keyboard_path?: boolean;
      suppression_aggregate_id?: UUID | null;
      suppression_field_name?: string | null;
      suppression_expires_at?: string | null;
    },
    token: string
  ) =>
    callTool<{ rejected: boolean; inbox_decision_id: UUID; suppression_rule_id?: UUID | null }>(
      "reject_proposal",
      args as unknown as Record<string, unknown>,
      token
    ),
  snoozeProposal: (
    args: {
      proposal_id: UUID;
      snooze_until: string;
      snooze_reason?:
        | "wait_for_event"
        | "tomorrow"
        | "end_of_week"
        | "next_monday"
        | "custom";
      pegged_event_id?: UUID | null;
      tier_assigned?: Tier;
      keyboard_path?: boolean;
    },
    token: string
  ) => callTool<{ snoozed: boolean; inbox_decision_id: UUID }>(
    "snooze_proposal",
    args as unknown as Record<string, unknown>,
    token
  ),
  bulkApprove: (
    args: {
      proposal_ids: UUID[];
      typed_phrase?: string | null;
      tier_assigned?: Tier;
      time_to_decide_sec?: number | null;
      keyboard_path?: boolean;
    },
    token: string
  ) => callTool<BulkActionResult>("bulk_approve", args as unknown as Record<string, unknown>, token),
  bulkReject: (
    args: {
      proposal_ids: UUID[];
      reason?: string | null;
      tier_assigned?: Tier;
      time_to_decide_sec?: number | null;
      keyboard_path?: boolean;
    },
    token: string
  ) => callTool<BulkActionResult>("bulk_reject", args as unknown as Record<string, unknown>, token),
  getProposalEvidence: (proposal_id: UUID, token: string) =>
    callTool<ProposalEvidence>("get_proposal_evidence", { proposal_id }, token),
  revertAutoApplied: (
    args: { proposal_id: UUID; reason?: string | null },
    token: string
  ) =>
    callTool<{
      reverted: boolean;
      inbox_decision_id: UUID;
      reverted_at: string;
      age_seconds: number;
    }>("revert_auto_applied", args as unknown as Record<string, unknown>, token),
  resolveConflictKeepBoth: (
    args: {
      primary_proposal_id: UUID;
      conflicting_proposal_id: UUID;
      source_tags: Record<UUID, string>;
      tier_assigned?: Tier;
      time_to_decide_sec?: number | null;
    },
    token: string
  ) =>
    callTool<{
      primary_applied: boolean;
      conflicting_applied: boolean;
      conflict_id: UUID;
      inbox_decision_id: UUID;
    }>("resolve_conflict_keep_both", args as unknown as Record<string, unknown>, token),

  // ---- Notes / Log (matches feat-notes-backend MCP tools) ----
  // target_type defaults to 'user'; target_id is omitted for user-targeted notes.
  listNotes: (
    args: {
      target_type?: NoteTargetType;
      target_id?: UUID | null;
      limit?: number;
      offset?: number;
    },
    token: string
  ) =>
    callTool<ListResult<Note>>(
      "list_notes",
      { target_type: "user", ...args } as unknown as Record<string, unknown>,
      token
    ),
  appendNote: (
    args: {
      text: string;
      target_type?: NoteTargetType;
      target_id?: UUID | null;
      source?: Record<string, unknown> | null;
    },
    token: string
  ) =>
    callTool<Note>(
      "append_note",
      { target_type: "user", ...args } as unknown as Record<string, unknown>,
      token
    ),
  deleteNote: (args: { note_id: UUID }, token: string) =>
    callTool<{ ok: true }>("delete_note", args as unknown as Record<string, unknown>, token),

  // ---- Connectors (matches feat-connectors-backend MCP tools) -------------
  // Each provider has its own configure path; OAuth providers (m365 / gmail)
  // hit `get_oauth_start_url` and then the backend handles the callback +
  // 302 back to `/connectors?success=<provider>`.
  listConnectors: (token: string) =>
    callTool<ListResult<Connector>>("list_connectors", {}, token).catch(() => ({
      items: [],
      count: 0
    })),
  configureIcloudConnector: (
    args: { apple_id: string; app_password: string; display_name: string },
    token: string
  ) =>
    callTool<{ id: UUID; provider: "icloud"; status: "configured" }>(
      "configure_icloud_connector",
      args as unknown as Record<string, unknown>,
      token
    ),
  getOauthStartUrl: (args: { provider: Exclude<ConnectorProvider, "icloud"> }, token: string) =>
    callTool<{ redirect_url: string; state: string }>(
      "get_oauth_start_url",
      args as unknown as Record<string, unknown>,
      token
    ),
  pullConnectorNow: (args: { connector_id: UUID; dry_run?: boolean }, token: string) =>
    callTool<ConnectorRun>("pull_connector_now", args as unknown as Record<string, unknown>, token),
  listConnectorRuns: (args: { connector_id?: UUID; limit?: number }, token: string) =>
    callTool<ListResult<ConnectorRun>>(
      "list_connector_runs",
      args as unknown as Record<string, unknown>,
      token
    ).catch(() => ({ items: [], count: 0 })),
  disconnectConnector: (args: { connector_id: UUID; reason?: string }, token: string) =>
    callTool<{ ok: true }>(
      "disconnect_connector",
      args as unknown as Record<string, unknown>,
      token
    ),

  // ---- Agent Command Center (matches backend mcp/tools/agent_admin.py) ----
  //
  // All ADMIN-role + "contactops:agents.admin" scope. A non-admin caller is
  // rejected server-side with an MCPError, which the /agents console surfaces as
  // an admin-only notice / toast (same pattern as the workspace-members surface).
  // Reads tolerate failure with a calm empty fallback so the page never hard-errors.
  listAgents: (token: string, args: { include_inactive?: boolean } = {}) =>
    callTool<ListAgentsResult>("list_agents", args, token).catch(() => ({
      agents: [],
      count: 0
    })),
  getAgentTrust: (
    args: { agent_slug: string; visibility?: "private" | "team" | "org" | "shared" },
    token: string
  ) =>
    callTool<GetAgentTrustResult>(
      "get_agent_trust",
      { visibility: "private", ...args } as unknown as Record<string, unknown>,
      token
    ),
  getAgentGovernance: (token: string) =>
    callTool<AgentGovernance>("get_agent_governance", {}, token),
  setAgentsPaused: (args: { paused: boolean; reason: string }, token: string) =>
    callTool<AgentGovernance>(
      "set_agents_paused",
      args as unknown as Record<string, unknown>,
      token
    ),
  promoteAgentTier: (
    args: {
      agent_slug: string;
      reason: string;
      visibility?: "private" | "team" | "org" | "shared";
    },
    token: string
  ) =>
    callTool<TierShiftResult>(
      "promote_agent_tier",
      { visibility: "private", ...args } as unknown as Record<string, unknown>,
      token
    ),
  demoteAgentTier: (
    args: {
      agent_slug: string;
      reason: string;
      visibility?: "private" | "team" | "org" | "shared";
    },
    token: string
  ) =>
    callTool<TierShiftResult>(
      "demote_agent_tier",
      { visibility: "private", ...args } as unknown as Record<string, unknown>,
      token
    ),
  pauseAgent: (args: { agent_slug?: string; reason: string }, token: string) =>
    callTool<AgentBreakerResult>(
      "pause_agent",
      args as unknown as Record<string, unknown>,
      token
    ),
  resumeAgent: (args: { agent_slug?: string; reason: string }, token: string) =>
    callTool<AgentBreakerResult>(
      "resume_agent",
      args as unknown as Record<string, unknown>,
      token
    )
};
