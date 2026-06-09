export type UUID = string;

export type ToolEnvelope = {
  isError?: boolean;
  code?: string;
  message?: string;
  hint?: string;
};

export type PersonSummary = {
  person_id: UUID;
  display_name: string;
  kind?: string;
  primary_email?: string | null;
  primary_phone?: string | null;
  current_org?: string | null;
  current_org_id?: UUID | null;
  headline?: string | null;
  tags?: string[];
  relationship_temperature?: string | null;
  last_interaction_at?: string | null;
  etag?: string;
  match_score?: number;
};

export type Person = ToolEnvelope & {
  person_id: UUID;
  etag: string;
  vcard_uid?: string | null;
  display_name: string;
  kind: string;
  status: string;
  pronouns?: string | null;
  headline?: string | null;
  bio?: string | null;
  occupation_title?: string | null;
  emails?: ContactMethod[];
  phones?: ContactMethod[];
  recent_events: ActionEvent[];
};

export type ContactMethod = {
  id?: UUID;
  address?: string;
  e164?: string;
  type?: string;
  label?: string;
  is_primary?: boolean;
  value?: string;
  confidence?: number;
  source_label?: string;
};

export type OrganizationSummary = {
  org_id: UUID;
  legal_name?: string;
  display_name: string;
  kind?: string;
  domain?: string | null;
  industry?: string | null;
  etag?: string;
  created_at?: string | null;
};

export type Organization = ToolEnvelope & {
  organization: OrganizationSummary & Record<string, unknown>;
  employee_count: number;
  recent_action_events: ActionEvent[];
};

export type ListResult<T> = ToolEnvelope & {
  items: T[];
  count: number;
  next_cursor?: string | null;
  total_count?: number | null;
};

export type Tag = {
  tag_id?: UUID;
  slug: string;
  display_name?: string;
  color?: string | null;
  usage_count?: number;
};

export type IsolationMode = "strict" | "standard" | "open";

export type Tenant = {
  tenant_id: UUID;
  slug: string;
  display_name: string;
  kind?: string;
  /** Per-workspace membership role: viewer < member < manager < admin. */
  role?: "viewer" | "member" | "manager" | "admin" | string;
  /**
   * Isolation dial (Phase 4 §1). `strict` (and any `hipaa_mode` tenant) hides the
   * owner's rows cross-workspace and requires step-up to switch into. Surfaced
   * for the lock badge in the switcher. Label-only projection: present for the
   * caller's own/active/membership workspaces.
   */
  isolation_mode?: IsolationMode;
  /** True when this row came from the caller's `user_tenant_membership` set (vs home/children). */
  is_membership?: boolean;
  hipaa_mode?: boolean;
  gdpr_mode?: boolean;
  retention_class?: string;
  member_count?: number;
  branding?: Record<string, unknown>;
};

export type ActionEvent = {
  event_id?: UUID;
  id?: UUID;
  event_type?: string;
  action_type?: string;
  status?: string;
  actor?: Record<string, unknown>;
  actor_chain?: unknown;
  created_at?: string;
  diff?: unknown;
  payload?: unknown;
  evidence?: unknown;
};

export type Photo = {
  photo_id: UUID;
  media_asset_id?: UUID;
  url?: string;
  download_url?: string;
  is_primary?: boolean;
  created_at?: string;
};

export type CardDavPassword = {
  id: UUID;
  label: string;
  created_at: string;
  last_used_at?: string | null;
};

// ---- Personal Access Tokens (matches Codex PAT backend) ----
//
// PATs let an external MCP client (Claude Code, Cline, Cursor, etc.) connect
// to mcp.contacts.magicunicorn.dev as the issuing user. The plaintext token
// (`co_pat_<48 chars>`) is shown exactly once on issuance — every subsequent
// list response carries only the last_4 suffix.

/** Row shape returned by `list_personal_access_tokens` — no plaintext, ever. */
export type PersonalAccessToken = {
  id: UUID;
  display_name: string;
  /** Last four characters of the plaintext, safe to render for identification. */
  last_4: string;
  /** Subset of the issuing user's scopes; empty array = inherits all. */
  scopes: string[];
  /** ISO 8601. Null = never expires. */
  expires_at: string | null;
  created_at: string;
  last_used_at: string | null;
};

/** Returned only by `issue_personal_access_token` — includes plaintext once. */
export type PersonalAccessTokenIssued = PersonalAccessToken & {
  /** Full bearer token, e.g. `co_pat_…48`. Show once, then drop. */
  plaintext_token: string;
};

// ---- Notes / Log (Settings + future profile-page surface) ----
// Matches the backend MCP `list_notes` / `append_note` shape on `feat-notes-backend`.

export type NoteTargetType = "user" | "person" | "org";
export type NoteAuthorType = "user" | "agent";

export type Note = {
  id: UUID;
  target_type: NoteTargetType;
  /** null when target_type === "user" (the note is on the calling user themself). */
  target_id?: UUID | null;
  author_type: NoteAuthorType;
  /** uc_uid for `user`, agent slug (e.g. "ai-contacts-analyst") for `agent`. */
  author_id: string;
  /** Plain text with newlines preserved. Markdown-ish formatting is rendered loosely. */
  text: string;
  /** Optional structured context, e.g. `{proposal_id, cluster_id, action}`; rendered as a compact one-line label. */
  source?: Record<string, unknown> | null;
  created_at: string;
};

// ---- Phase 3.3 inbox types (mirror of backend schemas/inbox.py) ----

export type Tier = 0 | 1 | 2 | 3 | 4;

export type EntityKind = "person" | "org";

export type ReversibilityClass =
  | "reversible"
  | "reversible_24h"
  | "soft_delete"
  | "irreversible";

export type ComplianceExposure = "none" | "low" | "medium" | "high";

export type ProposalStatusFilter = "proposed" | "snoozed" | "resolved";

export type ClusterKind = "entity" | "dedup" | "agent-batch" | "bulk-tag-lifecycle";

export type RejectMode = "reject" | "dismiss_duplicate" | "mute" | "undo";

export type SnoozeReason =
  | "wait_for_event"
  | "tomorrow"
  | "end_of_week"
  | "next_monday"
  | "custom";

export type BulkSkipReason =
  | "hipaa_requires_t4"
  | "already_resolved"
  | "cross_tenant_mixed"
  | "tier_4_in_selection"
  | "stale_optimistic_lock"
  | "suppressed_by_rule"
  | "not_found"
  | "wrong_tenant";

export type ProposalCompliance = {
  hipaa: boolean;
  exposure: ComplianceExposure;
};

export type CaseFileLink = {
  project_id: UUID;
  project_name: string;
  task_id?: UUID | null;
  task_name?: string | null;
};

export type Proposal = {
  proposal_id: UUID;
  action_event_id: UUID;
  agent_id: string;
  agent_version: string;
  tenant_id: UUID;
  tenant_slug: string;
  entity_id: UUID;
  entity_kind: EntityKind;
  entity_display_name: string;
  entity_avatar_url?: string | null;
  action_type: string;
  payload_before: Record<string, unknown> | null;
  payload_after: Record<string, unknown>;
  confidence: number;
  reversibility_class: ReversibilityClass;
  compliance: ProposalCompliance;
  trust_tier_at_creation: Tier;
  trace_id?: string | null;
  evidence_pack_id?: UUID | null;
  parent_proposal_id?: UUID | null;
  rationale: string;
  created_at: string;
  snoozed_until?: string | null;
  cross_tenant: boolean;
  touches_case_file: boolean;
  case_file_links: CaseFileLink[];
  fields_changed: number;
  is_dedup: boolean;
  is_edge: boolean;
  is_delete: boolean;
  bulk_count: number;
  cluster_id: UUID;
};

export type ProposalCluster = {
  cluster_id: UUID;
  cluster_kind: ClusterKind;
  entity_id: UUID;
  entity_display_name: string;
  entity_avatar_url?: string | null;
  tenant_id: UUID;
  proposal_ids: UUID[];
  cumulative_confidence_avg: number;
  agent_slugs: string[];
  earliest_created_at: string;
  latest_created_at: string;
};

export type ListPendingProposalsResult = {
  clusters: ProposalCluster[];
  proposals: Proposal[];
  next_cursor: string | null;
  total_estimate: number;
};

export type SourceEvent = {
  event_id: UUID;
  kind: string;
  title: string;
  occurred_at: string;
  deep_link: string;
};

export type ProposalEvidence = {
  proposal_id: UUID;
  source_events: SourceEvent[];
  reasoning: string | null;
  laminar_trace_url: string | null;
  prov_activity_id: UUID | null;
  cost_cents: number;
  tokens_input: number;
  tokens_output: number;
};

export type BulkActionResult = {
  applied?: number;
  rejected?: number;
  skipped: UUID[];
  reasons: Record<string, BulkSkipReason>;
  inbox_decision_ids: UUID[];
};

export type BulkActionPayload = {
  proposal_ids: UUID[];
  total: number;
  eligible: number;
  blocked_hipaa: number;
  blocked_mixed_tenant: number;
  blocked_tier_4: number;
};

// ---- Data quality (matches backend mcp/tools/data_quality.py) -------------

export type DqOrgIssue = {
  org_id: UUID;
  name: string;
  cleaned?: string;
  edge_count?: number;
};

export type DqDuplicateMember = {
  org_id: UUID;
  name: string;
  cleaned?: string;
  edge_count?: number;
};

export type DqDuplicateGroup = {
  key: string;
  members: DqDuplicateMember[];
  suggested_survivor_id: UUID;
};

export type DqTagVariant = { tag: string; person_count: number };
export type DqTagVariantGroup = { canonical: string; variants: DqTagVariant[] };

export type DataQualityReport = {
  org_total: number;
  issues: {
    quote_wrapped: DqOrgIssue[];
    phone_as_name: DqOrgIssue[];
    whitespace: DqOrgIssue[];
  };
  org_duplicate_groups: DqDuplicateGroup[];
  tag_variant_groups: DqTagVariantGroup[];
  summary: {
    quote_wrapped: number;
    phone_as_name: number;
    whitespace: number;
    duplicate_groups: number;
    duplicate_orgs: number;
    tag_variant_groups: number;
  };
};

export type CleanOrgNamesResult = {
  dry_run: boolean;
  changed: Array<{
    org_id: UUID;
    display_before: string;
    display_after: string;
    legal_before: string;
    legal_after: string;
  }>;
  changed_count: number;
  skipped_phone_like: number;
  status: string;
};

export type CanonicalizeTagsResult = {
  dry_run: boolean;
  mapping: Record<string, string>;
  memberships_changed: number;
  sample: Array<{ person_id: UUID; before: string[]; after: string[] }>;
  status: string;
};

export type MergeOrganizationsResult = {
  dry_run: boolean;
  survivor_id: UUID;
  merged: UUID[];
  roles_repointed: number;
  merge_event_id: UUID | null;
  status: string;
};

// ---- Connectors (matches feat-connectors-backend MCP tools) ---------------
//
// The /connectors page pulls contacts from third-party sources. Each provider
// has its own auth flow (iCloud = app password, M365/Gmail = OAuth) but the
// connector record itself is uniform. Triggering a pull spawns a connector_run
// row that the page polls until it flips out of `running`.

/** Which third-party we're talking to. */
export type ConnectorProvider = "icloud" | "m365" | "gmail";

/**
 * Lifecycle status of a single configured connector:
 *
 *   - "configured"   credentials saved, no pulls yet (or last pull cleared)
 *   - "connected"    at least one successful pull on file
 *   - "disconnected" user explicitly disconnected (creds removed)
 *   - "error"        last pull / token check failed; `last_error` carries why
 */
export type ConnectorStatus = "configured" | "connected" | "disconnected" | "error";

/**
 * Per-run status — drives the inline spinner + polling loop on the page.
 * Backend reports `running` while a pull is in-flight, then transitions to
 * one of the terminal states.
 */
export type ConnectorRunStatus = "running" | "succeeded" | "failed" | "partial";

/** Summary fields a run can fill in. Counts may be missing on a `running` row. */
export type ConnectorRunSummary = {
  parsed_count?: number;
  proposed_count?: number;
  deduped_count?: number;
  skipped_count?: number;
  /** Free-form summary the backend ships for the expansion row (jsonb on the wire). */
  details?: Record<string, unknown> | null;
};

export type Connector = ToolEnvelope & {
  id: UUID;
  provider: ConnectorProvider;
  display_name: string;
  status: ConnectorStatus;
  /** ISO 8601 timestamp; null when never pulled. */
  last_pull_at?: string | null;
  /**
   * Inline counts from the most recent run. Backend mirrors the run row so the
   * card can render "47 proposed" without a separate runs lookup. */
  last_pull_summary?: ConnectorRunSummary | null;
  /** Backend-formatted user-facing reason — surfaced verbatim in the Error state. */
  last_error?: string | null;
  configured_at: string;
};

export type ConnectorRun = ToolEnvelope & {
  run_id: UUID;
  connector_id: UUID;
  provider: ConnectorProvider;
  /** Optional but populated for the recent-runs table. */
  display_name?: string;
  status: ConnectorRunStatus;
  /** ISO 8601 timestamps. `finished_at` is null while running. */
  started_at: string;
  finished_at?: string | null;
  /** Whether this run was triggered with `dry_run=true`. */
  dry_run?: boolean;
  parsed_count?: number;
  proposed_count?: number;
  deduped_count?: number;
  skipped_count?: number;
  /** Per-run free-form summary; the expansion row pretty-prints this JSON. */
  summary?: Record<string, unknown> | null;
  error?: string | null;
};
