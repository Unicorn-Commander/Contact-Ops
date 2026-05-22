import { env } from "@/lib/env";
import type {
  ActionEvent,
  CardDavPassword,
  ListResult,
  Organization,
  OrganizationSummary,
  Person,
  PersonSummary,
  Photo,
  Tag,
  Tenant,
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
  revokeCardDavPassword: (args: Record<string, unknown>, token: string) => callTool("revoke_carddav_app_password", args, token)
};
