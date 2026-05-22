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

export type Tenant = {
  tenant_id: UUID;
  slug: string;
  display_name: string;
  kind?: string;
  role?: string;
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
