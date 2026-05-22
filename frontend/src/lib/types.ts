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
