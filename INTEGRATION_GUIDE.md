# Contact-Ops Integration Guide

**Date**: 2026-05-21
**Audience**: Developers of ecosystem apps (Listing-Ops, Crisis-Ops, Project-Ops, Meeting-Ops, Stable, Brigade, Majiks-*) integrating with Contact-Ops MCP.
**Status**: Phase 0 (scaffold). Tools listed below land in Phase 1+. The handshake and OBO infrastructure are in place from day one; the tools themselves are not.

For end-user docs (Aaron-as-user) see `docs/USER_GUIDE.md`. For day-to-day Contact-Ops developer workflow see `docs/DEVELOPER_GUIDE.md`. For the canonical design see `/Users/aaronstransky/Documents/Contact-Ops-MCP-Design.md`.

---

## 1. What Contact-Ops is for ecosystem apps

Contact-Ops is the canonical person and organization registry. Your app should never store its own copy of "who is this user" or "who is this org" beyond an ID reference. Instead:

- Hold a `contact_ops_person_id` (UUID) or `contact_ops_org_id` per record in your DB.
- Resolve display name / primary email / current employment at render time via `get_person` or `get_org`, with a short local cache (60s typical).
- Push back into Contact-Ops when your app learns something new (new participant in a meeting, new party in a case, new task assignee, new mention).

Each of your app's references to a person also creates an `identifier` row in Contact-Ops with your namespace (e.g., `meeting-ops.session.speaker`, `crisis-ops.case.party`, `project-ops.task.assignee`). This is how Contact-Ops's Dedup Agent knows that "person at Meeting-Ops session 1234 speaker 3" and "person on Project-Ops task assignment 5678" are the same person.

---

## 2. Onboarding your service

### 2.1 Register your service as an MCP client in Keycloak

Contact-Ops trusts five categories of MCP clients in the `uchub` realm on commander:

- `contact-ops-app` — human UI (you don't use this).
- `contact-ops-mcp` — your service's standard MCP client.
- `contact-ops-carddav` — CardDAV adapter (you don't use this).
- `contact-ops-publisher` — outbound to Data Intel (you don't use this).
- `contact-ops-bridge-inbound` — inbound from Data Intel (you don't use this).

For your ecosystem app, register a confidential OIDC client in the `uchub` realm following the Keycloak client bootstrap procedure. The client config should include:

- **Client ID**: `<your-app>-mcp-client` (e.g., `crisis-ops-mcp-client`)
- **Access type**: confidential
- **Service accounts enabled**: yes (for service-to-service calls)
- **Direct access grants**: disabled
- **OAuth 2.0 device authorization**: disabled
- **Resource indicators (RFC 8707) enabled**: yes
- **Token exchange (RFC 8693) enabled**: yes
- **Mapper**: `uc_uid` from user-attributes scope (gives you `uc_uid` in JWT)
- **Allowed audiences**: include `mcp.contacts.magicunicorn.dev`

The specific Keycloak client bootstrap doc lives in another track. For Phase 0, ask Aaron to register your client manually via the Keycloak admin UI at `auth.unicorncommander.ai/admin` (per `~/.claude/projects/-Users-aaronstransky/memory/reference_keycloak_admin.md`).

### 2.2 Obtain an OBO token via RFC 8693 token exchange

When a human in your app triggers an action that needs to call Contact-Ops on their behalf, you exchange the user's access token for a downscoped token that targets Contact-Ops, preserving the actor chain.

```python
import httpx

KEYCLOAK_TOKEN_URL = "https://auth.unicorncommander.ai/realms/uchub/protocol/openid-connect/token"
CONTACT_OPS_AUDIENCE = "mcp.contacts.magicunicorn.dev"

async def exchange_for_contact_ops_token(
    subject_token: str,
    client_id: str,
    client_secret: str,
    requested_scopes: list[str],
) -> str:
    """
    RFC 8693 token exchange. Returns an access token targeting
    Contact-Ops with the original user (and any prior actor chain) preserved
    as a nested `act` claim.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            KEYCLOAK_TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": client_id,
                "client_secret": client_secret,
                "subject_token": subject_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "audience": CONTACT_OPS_AUDIENCE,
                "scope": " ".join(requested_scopes),
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]
```

The returned token carries a nested `act` claim recording your service as the immediate actor and the user (or upstream caller) as the authority. Contact-Ops records the full chain in `action_event.actor` and denormalizes the root user into `action_event.human_authority` for inbox filtering.

Example actor chain for a Crisis-Ops case lookup that triggers a Contact-Ops party search:

```json
{
  "sub": "ai-system",
  "act": {
    "sub": "crisis-ops-mcp",
    "act": {
      "sub": "contact-ops-relationship-inference-agent-v1.2",
      "act": {
        "sub": "REMOVED"
      }
    }
  }
}
```

---

## 3. Calling Contact-Ops MCP tools

### 3.1 Endpoint + transport

| Setting | Value |
|---------|-------|
| URL | `https://mcp.contacts.magicunicorn.dev/mcp` |
| Transport | HTTP POST (JSON-RPC 2.0) for tool calls; SSE for tool list / push updates |
| Auth | `Authorization: Bearer <obo-access-token>` from §2.2 |
| Content-Type | `application/json` |
| Resource indicator | Set `resource=https://mcp.contacts.magicunicorn.dev` in the OAuth flow per RFC 8707 |

### 3.2 JSON-RPC envelope

Standard MCP JSON-RPC 2.0:

```json
{
  "jsonrpc": "2.0",
  "id": "request-uuid-1",
  "method": "tools/call",
  "params": {
    "name": "get_person",
    "arguments": {
      "person_id": "01985-...",
      "include": ["emails", "phones", "current_employments"]
    }
  }
}
```

Response on success:

```json
{
  "jsonrpc": "2.0",
  "id": "request-uuid-1",
  "result": {
    "content": [{"type": "text", "text": "<JSON string of result>"}],
    "isError": false
  }
}
```

Response on tool-level error (still HTTP 200; JSON-RPC distinguishes app errors from transport errors):

```json
{
  "jsonrpc": "2.0",
  "id": "request-uuid-1",
  "result": {
    "content": [{"type": "text", "text": "{\"isError\":true,\"code\":\"PERSON_NOT_FOUND\",\"message\":\"...\",\"retryable\":false}"}],
    "isError": true
  }
}
```

JSON-RPC transport errors (malformed envelope, server crash) come back as HTTP 4xx/5xx with a `error` object.

### 3.3 Phase 0 tools

Phase 0 has **no tools registered**. The MCP server responds to:

- `initialize` — handshake, returns server info and capabilities.
- `tools/list` — returns an empty list.
- `tools/call` — returns `isError: true` with code `TOOL_NOT_FOUND` for any name.

Phase 1 lights up the core read + write tools (people, orgs, employment, identifiers, emails, phones, addresses, tags, search). Watch the design doc §5 for the full tool surface as it lands.

### 3.4 Tool conventions (applies to all tools, when they land)

- **Tenant**: read from the JWT claim. Do NOT pass a `tenant_id` argument. Cross-tenant tools use `scope` or `include_tenants`.
- **Idempotency**: every `create_*` / `bulk_*` tool accepts `idempotency_key: UUID`. Replays within 24h return the original result.
- **Etag**: every `update_*` tool requires the current `etag` from the prior read. Stale etag returns `STALE_ETAG`.
- **Confidence**: every agent-callable mutation accepts `confidence: 0-1`. Below threshold, the tool returns `{status: "proposed", proposal_id}`. Above, returns `{status: "applied", event_id}`.
- **Pagination**: cursor-based. Default `limit` 25, max 100. Returns `items`, `count`, `next_cursor`, optional `total_count`.
- **Errors**: `{isError: true, code, message, retryable, hint?, retry_after_ms?, details?}`. Common codes: `UNAUTHORIZED`, `FORBIDDEN_ROLE`, `FORBIDDEN_SCOPE`, `TENANT_NOT_FOUND`, `RATE_LIMITED`, `STALE_ETAG`, `VALIDATION_ERROR`, `INTERNAL`, plus tool-specific codes.

---

## 4. Common integration patterns

### 4.1 Resolve a person at render time

```python
async def resolve_person(person_id: str, access_token: str) -> dict:
    """Cached for 60s in your service."""
    cached = await cache.get(f"contact_ops:person:{person_id}")
    if cached:
        return cached

    result = await mcp_call(
        access_token=access_token,
        tool="get_person",
        arguments={
            "person_id": person_id,
            "include": ["primary_email", "current_employments"],
        },
    )
    await cache.set(f"contact_ops:person:{person_id}", result, ttl=60)
    return result
```

### 4.2 Upsert when your app learns about a new person

Use `upsert_person` for any inbound (CSV import, LinkedIn pull, scraped data, user paste). Provide at least one natural key in `match_by`:

```python
async def push_meeting_speaker_to_contacts(
    meeting_id: str,
    speaker_email: str | None,
    speaker_name: str,
    access_token: str,
) -> dict:
    if not speaker_email:
        # No email -> use voice fingerprint match instead (Phase 3)
        return await mcp_call(
            access_token=access_token,
            tool="voice_match_run",
            arguments={"voice_sample_id": "..."},
        )

    return await mcp_call(
        access_token=access_token,
        tool="upsert_person",
        arguments={
            "match_by": [{"namespace": "email", "value": speaker_email}],
            "display_name": speaker_name,
            "emails": [{"address": speaker_email, "type": "work"}],
            "source_label": f"meeting-ops:session:{meeting_id}",
            "confidence": 0.85,
            "idempotency_key": str(uuid4()),
        },
    )
```

### 4.3 Register your app as an identifier source

When your app first encounters a person, register your namespace + ID as an identifier so future calls can dedupe:

```python
await mcp_call(
    access_token=access_token,
    tool="add_identifier",
    arguments={
        "person_id": contact_ops_person_id,
        "namespace": "project-ops.user",
        "value": project_ops_user_id,
        "confidence": 1.0,
    },
)
```

Now any other ecosystem app pushing data tagged `project-ops.user:<id>` will hit the same canonical person.

### 4.4 Subscribe to person/org changes

`graph_sync_outbox` is the internal-only outbox table. If your service needs to react to person/org changes (e.g., re-render a UI when a person's name changes), the recommended pattern is **server-sent events on the MCP endpoint**:

```python
async with httpx.AsyncClient() as client:
    async with client.stream(
        "GET",
        "https://mcp.contacts.magicunicorn.dev/events",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "text/event-stream"},
        params={"event_types": "person.applied,org.applied"},
    ) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                event = json.loads(line[6:])
                await handle_event(event)
```

SSE endpoint lands in Phase 1 alongside the core tools. Filter by `event_types` query param.

### 4.5 Push contact data to Data Intel (separate flow)

Data Intel is a peer system, not a feature of Contact-Ops. Push to Data Intel via Data Intel's own endpoint:

```python
await client.post(
    "https://verify.centerdeep.online/catalogue/submit",
    headers={"Authorization": f"Bearer {data_intel_access_token}"},
    json={
        "company": {...},
        "contacts": [...],
        "source_app": "your-app-name",
    },
)
```

**Most ecosystem apps should NOT push to Data Intel.** Contact-Ops handles outbound publishing automatically via the Data Intel Bridge agent when:
1. The tenant has `data_intel_publish_consent = true`.
2. The person/org has an active `consent_records` row with `purpose = 'data_intel_share'`.
3. The tenant is not HIPAA.

Only push directly if your service is the source of truth for B2B verification data (e.g., a future SMTP-verify worker that doesn't go through Contact-Ops at all).

### 4.6 Query Contact-Ops vs Data Intel

| You want... | Call... |
|-------------|---------|
| Display info for a known person in your app | Contact-Ops `get_person` |
| "Is this email deliverable" | Data Intel `/api/v1/verify/email` |
| "Have we ever heard of this person" (across all apps) | Contact-Ops `find_person_by_identifier` |
| "Do we know anything about example.com" (B2B catalogue) | Data Intel `/api/v1/catalogue/lookup/domain/example.com` |
| "Who in my tenant knows this person" | Contact-Ops `who_knows` (graph query, Phase 4) |
| "Suggest similar companies" | Data Intel (Qdrant collection of org descriptions) |

When in doubt: if it's about an entity your tenant actively manages, use Contact-Ops. If it's a passive lookup against the global B2B catalogue, use Data Intel.

---

## 5. Interpreting action_event audit entries

When you call Contact-Ops, every mutation lands in `action_event` with your service in the actor chain. You can read your own audit trail via `list_audit_log`:

```python
result = await mcp_call(
    access_token=access_token,
    tool="list_audit_log",
    arguments={
        "actor_contains_sub": "crisis-ops-mcp",
        "event_types": ["person.applied_update", "identifier.applied_add"],
        "since": "2026-05-01T00:00:00Z",
        "limit": 50,
    },
)
```

Each entry includes `actor` (full OBO chain), `evidence` (sources + trace_id + prompt_hash + model + tool_calls), and `payload` (before/after diff).

Use this to:
- Debug why a person record looks unexpected ("which app last touched this?")
- Build per-app activity dashboards in your service
- Reconcile your service's local cache against Contact-Ops authoritative state

---

## 6. Error handling + retry semantics

### 6.1 Error envelope

```json
{
  "isError": true,
  "code": "STALE_ETAG",
  "message": "Etag mismatch: refresh and retry",
  "retryable": false,
  "hint": "Call get_person to obtain the current etag",
  "details": {
    "current_etag": "abc123...",
    "supplied_etag": "def456..."
  }
}
```

### 6.2 Retry guidance

| Code | Retryable? | Backoff |
|------|-----------|---------|
| `UNAUTHORIZED` | No | Refresh token, then retry once |
| `FORBIDDEN_ROLE` | No | Don't retry; check role/scopes |
| `FORBIDDEN_SCOPE` | No | Don't retry; widen requested scope |
| `TENANT_NOT_FOUND` | No | Don't retry; check tenant claim |
| `RATE_LIMITED` | Yes | Use `retry_after_ms` from envelope |
| `STALE_ETAG` | No | Re-read, then retry with fresh etag |
| `VALIDATION_ERROR` | No | Don't retry; fix input |
| `INTERNAL` | Yes | Exponential backoff with jitter, max 3 retries |
| Tool-specific (e.g., `PERSON_NOT_FOUND`) | Depends on the tool | Read the tool docs |

### 6.3 Idempotency on retries

Always set `idempotency_key` on `create_*` and `bulk_*` calls so that retries after network errors don't create duplicates:

```python
from uuid import uuid4

idempotency_key = str(uuid4())

for attempt in range(3):
    try:
        result = await mcp_call(
            access_token=access_token,
            tool="create_person",
            arguments={
                "display_name": "Jane Doe",
                "idempotency_key": idempotency_key,
                # ...
            },
        )
        break
    except TransientError:
        await asyncio.sleep(2 ** attempt)
```

The server returns the original result on replay (same `idempotency_key`), so retrying after a timeout never produces a duplicate.

---

## 7. Language-specific examples

### 7.1 Python (httpx)

```python
import httpx
import json
from uuid import uuid4

class ContactOpsMCPClient:
    def __init__(self, base_url: str, access_token: str):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )

    async def call_tool(self, name: str, arguments: dict) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        resp = await self.client.post(f"{self.base_url}/mcp", json=payload)
        resp.raise_for_status()
        result = resp.json()
        if "error" in result:
            raise RuntimeError(f"MCP error: {result['error']}")
        # Extract the tool result from the content envelope
        content = result["result"]["content"][0]["text"]
        parsed = json.loads(content)
        if parsed.get("isError"):
            raise ToolError(parsed["code"], parsed["message"], parsed.get("hint"))
        return parsed

    async def close(self):
        await self.client.aclose()


class ToolError(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None):
        self.code = code
        self.message = message
        self.hint = hint
        super().__init__(f"{code}: {message}" + (f" ({hint})" if hint else ""))


# Usage (Phase 1+, once tools exist)
async def main():
    client = ContactOpsMCPClient(
        base_url="https://mcp.contacts.magicunicorn.dev",
        access_token=obo_token,
    )
    try:
        person = await client.call_tool("get_person", {
            "person_id": "01985-...",
            "include": ["emails", "current_employments"],
        })
        print(person["display_name"])
    finally:
        await client.close()
```

### 7.2 TypeScript

```typescript
type MCPRequest = {
  jsonrpc: "2.0";
  id: string;
  method: string;
  params?: unknown;
};

type ToolError = {
  isError: true;
  code: string;
  message: string;
  retryable: boolean;
  hint?: string;
  details?: unknown;
};

class ContactOpsMCPClient {
  constructor(
    private baseUrl: string,
    private accessToken: string,
  ) {}

  async callTool<T = unknown>(name: string, args: unknown): Promise<T> {
    const payload: MCPRequest = {
      jsonrpc: "2.0",
      id: crypto.randomUUID(),
      method: "tools/call",
      params: { name, arguments: args },
    };
    const resp = await fetch(`${this.baseUrl}/mcp`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      throw new Error(`MCP transport error: ${resp.status} ${resp.statusText}`);
    }
    const body = await resp.json();
    if (body.error) {
      throw new Error(`MCP error: ${JSON.stringify(body.error)}`);
    }
    const text = body.result.content[0].text as string;
    const parsed = JSON.parse(text);
    if ((parsed as ToolError).isError) {
      const e = parsed as ToolError;
      const err = new Error(`${e.code}: ${e.message}`);
      (err as any).code = e.code;
      (err as any).retryable = e.retryable;
      throw err;
    }
    return parsed as T;
  }
}
```

### 7.3 Bash / curl

```bash
# Tool call (Phase 1+, once tools exist)
curl -X POST https://mcp.contacts.magicunicorn.dev/mcp \
  -H "Authorization: Bearer $OBO_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "req-1",
    "method": "tools/call",
    "params": {
      "name": "get_person",
      "arguments": {
        "person_id": "01985-...",
        "include": ["emails", "current_employments"]
      }
    }
  }'

# Handshake
curl -X POST https://mcp.contacts.magicunicorn.dev/mcp \
  -H "Authorization: Bearer $OBO_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "init-1",
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "my-app", "version": "1.0.0"}
    }
  }'

# List available tools
curl -X POST https://mcp.contacts.magicunicorn.dev/mcp \
  -H "Authorization: Bearer $OBO_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "list-1",
    "method": "tools/list"
  }'
```

---

## 8. OAuth scopes you'll need

Request scopes that match what your service actually does. Don't ask for the kitchen sink — Contact-Ops audits scope usage. Common scope sets:

| Use case | Scopes |
|----------|--------|
| Render person info in your UI | `person:read` |
| Render org info | `org:read` |
| Push new participants from Meeting-Ops | `person:write`, `identifier:write`, `voice:write` |
| Add case parties from Crisis-Ops | `person:write`, `relationship:write`, `identifier:write` |
| Add task assignees from Project-Ops | `person:read`, `identifier:write`, `person_org_role:write` |
| Bulk import from CSV | `person:write`, `person:bulk`, `org:write`, `org:bulk` |
| Query graph (Phase 4) | `graph:read` |
| Approve agent proposals (Phase 3) | `proposal:read`, `proposal:approve` |

Full scope list in design doc §3.6 (lines 250-324).

---

## 9. Federation chain example

Example chain when Crisis-Ops adds a party to a case:

```
1. Human (Aaron) clicks "Add party" in Crisis-Ops UI.
2. Crisis-Ops backend gets the user's access token, exchanges it for an
   OBO token targeting Contact-Ops:
     audience = "mcp.contacts.magicunicorn.dev"
     scopes = "person:write identifier:write relationship:write"
3. Crisis-Ops POSTs to https://mcp.contacts.magicunicorn.dev/mcp:
     {
       "method": "tools/call",
       "params": {
         "name": "upsert_person",
         "arguments": {
           "match_by": [{"namespace": "email", "value": "REMOVED"}],
           "display_name": "Opposing Counsel Name",
           "source_label": "crisis-ops:case:CASE-0042",
           "confidence": 0.95
         }
       }
     }
4. Contact-Ops backend validates the OBO token, extracts the actor chain:
     human (Aaron) -> crisis-ops-mcp -> contact-ops-mcp
   Records the chain in action_event.actor.
5. Contact-Ops returns {person_id, etag, status: "applied"}.
6. Crisis-Ops follows up with link_relationship:
     {
       "from_person_id": <Aaron's person_id>,
       "to_person_id": <new person_id>,
       "relation_type": "counsel_for",
       "case_id": "CASE-0042",
       "confidence": 1.0
     }
7. Because counsel_for is in the propose_only forever list, the relationship
   lands as a proposal regardless of confidence. Aaron approves via the Inbox.
```

This is the pattern that makes the ecosystem coherent: every ecosystem app talks to Contact-Ops the same way, every change carries the full actor chain, and every legal-class assertion gets human review.

---

## 10. Migration order from §9.5 of the design doc

When Phase 5 of Contact-Ops lands, ecosystem apps migrate to use Contact-Ops as the canonical contact registry in this order:

1. **Listing-Ops** (~1 week): 5 user rows -> `contact_ops_person_id`. Easiest, used as the smoke test.
2. **Crisis-Ops** (~1.5 weeks, highest value): clients, witnesses, opposing counsel per case. HIPAA fence applied. Sullivan + Rocky FRB911 + Shafen LIFE/LOANS cases seeded.
3. **Project-Ops** (~1 week): users + clients + organizations.
4. **Meeting-Ops** (~1 week): participants per session via Voice Match Agent.
5. **Stable** (~3 days): workspace members + mention-based interactions.
6. **Brigade** (~3 days): agent identities, cross-referenced via `brigade:agent_id` namespace.
7. **Nextcloud address book** (~3 days): CardDAV pull.
8. **iOS contacts** (~3 days, manual one-shot): Aaron exports vCard, runs `import_vcard`.

If you're working on one of these apps, coordinate with the orchestrator (Aaron via Claude) before starting your migration — schema-touching changes in your service should pair with Contact-Ops federation work.

---

## 11. Open questions for your integration

Add to `/Users/aaronstransky/Documents/Contact-Ops-Open-Questions.md` if any of these apply:

- Does your app need a Postmark sender domain attribution? (Yes, mostly.)
- Does your app have a writable Keycloak client today, or do you need one bootstrapped?
- Does your app's data have any HIPAA or regulated-PII implications that should trigger `hipaa_mode = true` on a tenant?
- Does your app already have an identifier namespace registered with Contact-Ops? (Phase 1 will manage these.)
- Does your app have a "soft delete" concept that maps cleanly onto `archive_person`, or are you doing something fancier?
