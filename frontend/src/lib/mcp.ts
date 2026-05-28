import { env } from "@/lib/env";
import type {
  ActionEvent,
  BulkActionResult,
  CardDavPassword,
  ListPendingProposalsResult,
  ListResult,
  Note,
  NoteTargetType,
  Organization,
  OrganizationSummary,
  Person,
  PersonSummary,
  Photo,
  ProposalEvidence,
  Tag,
  Tenant,
  Tier,
  UUID
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

  const body = (await response.json()) as MCPResponse<R>;
  if (body.error) throw new MCPError(body.error.code, body.error.message);
  return body.result as R;
}

export function callTool<R>(name: string, args: Record<string, unknown>, token: string) {
  return mcpCall<R>("tools/call", { name, arguments: args }, token);
}

export const tools = {
  list: (token: string) => mcpCall<{ tools: Array<{ name: string; description?: string }> }>("tools/list", {}, token),
  dashboard: async (token: string) => {
    const [people, orgs, tenants] = await Promise.all([
      callTool<ListResult<PersonSummary>>("list_people", { limit: 1 }, token),
      callTool<ListResult<OrganizationSummary>>("list_organizations", { limit: 1 }, token),
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
  listOrganizations: (args: Record<string, unknown>, token: string) =>
    callTool<ListResult<OrganizationSummary>>("list_organizations", args, token),
  searchOrganizations: (args: Record<string, unknown>, token: string) =>
    callTool<ListResult<OrganizationSummary>>("search_organizations", args, token),
  getOrganization: (org_id: UUID, token: string) => callTool<Organization>("get_organization", { org_id }, token),
  createOrganization: (args: Record<string, unknown>, token: string) => callTool("create_organization", args, token),
  updateOrganization: (args: Record<string, unknown>, token: string) => callTool("update_organization", args, token),
  archiveOrganization: (args: Record<string, unknown>, token: string) => callTool("archive_organization", args, token),
  listTags: (token: string) => callTool<ListResult<Tag>>("list_tags", { limit: 200 }, token),
  applyTags: (args: Record<string, unknown>, token: string) => callTool("apply_tags", args, token),
  listTenants: (token: string) => callTool<ListResult<Tenant>>("list_tenants", { limit: 100 }, token),
  createTenant: (args: Record<string, unknown>, token: string) => callTool("create_tenant", args, token),
  updateTenantSettings: (args: Record<string, unknown>, token: string) => callTool("update_tenant_settings", args, token),
  uploadPhoto: (args: Record<string, unknown>, token: string) => callTool("upload_photo", args, token),
  confirmPhotoUpload: (args: Record<string, unknown>, token: string) => callTool("confirm_photo_upload", args, token),
  listPhotos: (person_id: UUID, token: string) => callTool<ListResult<Photo>>("list_photos", { person_id }, token),
  listCardDavPasswords: (token: string) =>
    callTool<ListResult<CardDavPassword>>("list_carddav_app_passwords", {}, token).catch(() => ({ items: [], count: 0 })),
  generateCardDavPassword: (args: Record<string, unknown>, token: string) =>
    callTool("generate_carddav_app_password", args, token),
  revokeCardDavPassword: (args: Record<string, unknown>, token: string) => callTool("revoke_carddav_app_password", args, token),

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
    callTool<{ ok: true }>("delete_note", args as unknown as Record<string, unknown>, token)
};
