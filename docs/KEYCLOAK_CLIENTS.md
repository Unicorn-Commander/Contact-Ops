# Keycloak Clients — Contact-Ops Phase 0

**Realm**: `uchub` on `auth.unicorncommander.ai` (commander, primary store) with broker federation from `auth.magicunicorn.dev` (bigboy, sovereign secondary).
**Status**: Implementation-ready spec. Bootstrap script at `scripts/keycloak_bootstrap.sh`.
**Companion**: `docs/decisions/0003-keycloak-client-topology.md` (ADR-0003).
**Authoritative design**: `/Users/aaronstransky/Documents/Contact-Ops-MCP-Design.md` §3.5 + §3.6 + §3.7 + §6.

This document defines the five OIDC clients that Contact-Ops registers in the `uchub` realm, the granular OAuth scopes each one needs, the RFC 8693 token-exchange OBO trust matrix, the RFC 8707 resource-indicator audience whitelist per client, the Keycloak protocol mappers that emit `uc_uid` + tenant claims, and the realm role ladder (`CLIENT / STAFF / MANAGER / ADMIN`).

---

## 1. Why five clients, not one

Contact-Ops is an agent-first MCP-native platform. The MCP server is the product. Five distinct audiences need to authenticate against the same realm with very different flows and trust assumptions:

1. **Server-to-server bearer validation** — the MCP server accepts pre-issued tokens from ecosystem peers; it never does a browser redirect.
2. **Human browser flow** — the future human UI at `contacts.magicunicorn.dev` needs PKCE + offline_access.
3. **Legacy CardDAV clients** — iOS Contacts, macOS Contacts, Thunderbird, do not speak OIDC. They speak HTTP Basic. They need their own client with a tighter scope envelope, and they need per-user app passwords minted under that client.
4. **Outbound federation publisher** — when the Data Intel Bridge Agent publishes a person to `verify.centerdeep.online`, it needs RFC 8693 token exchange OBO so the downstream system sees `REMOVED` → `data-intel-bridge-agent` → `contact-ops-publisher` in nested `act` claims, not just a bare service account.
5. **Inbound federation acceptance** — when Data Intel proposes an enrichment, the inbound bridge needs to validate a token where the audience says "Contact-Ops" AND the actor chain says "Data Intel", not just any random bearer.

Mashing these into one client would mean: (a) all clients have all scopes (over-broad), (b) RFC 8707 resource indicators have nothing to discriminate on (you can't say "this token is for MCP only"), (c) the CardDAV basic-auth path leaks into the browser flow and you accidentally enable Basic-Auth-over-PKCE which is a footgun, (d) you can't audit publish vs. accept separately in the Keycloak event log.

Per `feedback_oidc_user_id_canary.md`, we also key on `sub` for user identity inside the service; `uc_uid` is the display/platform claim. The `user-attributes` scope (per `reference_keycloak_user_attributes.md`) emits `uc_uid` for free and is assigned to every client below.

---

## 2. Realm prerequisites

Before running `scripts/keycloak_bootstrap.sh`, the `uchub` realm must already have:

| Item | Source | How to verify |
|------|--------|---------------|
| `user-attributes` client scope | `reference_keycloak_user_attributes.md` | `kcadm.sh get client-scopes -r uchub --fields name` includes `user-attributes` |
| Auto-uc_uid SPI listener | `reference_keycloak_auto_ucuid.md` | `kcadm.sh get events/config -r uchub` shows `auto-ucuid` in `eventsListeners` |
| VERIFY_PROFILE flag handled | `feedback_keycloak_verify_profile.md` | New service accounts get firstName/lastName set; we set them at create time |
| Admin password live value | `reference_keycloak_admin.md` | `/home/muut/UC-1-Hub/.env` on commander, env var `KEYCLOAK_ADMIN_PASSWORD` |

The bootstrap script verifies items 1 and 4 before doing anything; items 2 and 3 are deployment-level and out of scope for the client topology.

---

## 3. Client roster — one-line summary

| Client ID | Type | Flows | Primary caller | Audience emits |
|-----------|------|-------|----------------|----------------|
| `contact-ops-mcp` | bearer-only | none (validation only) | ecosystem MCP peers, agents | `contact-ops-mcp` |
| `contact-ops-app` | public | code+PKCE | human browser at `contacts.magicunicorn.dev` | `contact-ops-mcp`, `contact-ops-carddav` |
| `contact-ops-carddav` | confidential | direct grants (Basic-over-TLS) | iOS/macOS/Thunderbird via app password | `contact-ops-carddav` |
| `contact-ops-publisher` | confidential | client_credentials + token-exchange | Data Intel Bridge Agent (outbound) | `verify.centerdeep.online`, `contact-ops-mcp` |
| `contact-ops-bridge-inbound` | confidential | client_credentials | Data Intel (inbound proposals) | `contact-ops-mcp` |

`Audience emits` is the RFC 8707 `aud` claim placed in tokens minted by this client. The MCP server verifies `aud` includes its own `client_id` before honoring any call.

---

## 4. Granular OAuth scopes

Per design doc §3.6 and §6, the realm carries a Contact-Ops scope namespace. Phase 0 lands the following scopes as Keycloak client scopes (assigned as optional or default per client below):

```
# Person domain
person:read
person:write
person:propose_merge
person:apply_merge
person:bulk

# Organization domain
org:read
org:write
org:bulk

# Field-level provenance
field:propose_set
field:apply_set

# Relationship
relationship:propose
relationship:apply

# Tag
tag:apply

# Voice
voice:match
voice:assign

# Agent registry
agent:register

# Admin tenant ops (cross-tenant, ADMIN-role gated)
admin:tenant

# CardDAV
carddav:sync

# Federation
data-intel:catalogue:submit
data-intel:catalogue:search
```

Each scope is a Keycloak client scope of type `Optional`, with one protocol mapper (Audience type) that adds the scope literal to the `scope` claim. The MCP server checks `scope` substring + `aud` separately.

The full set above is what Phase 0 needs. Phase 1+ adds `email:*`, `phone:*`, `address:*`, `identifier:*`, `media:*`, `merge:undo`, `acl:*`, `interaction:*`, `proposal:*`, `audit:*`, `import:*`, `export:*`, `consent:apply` per design doc §3.6 — they're deliberately deferred to keep Phase 0 lean. The bootstrap script creates only the Phase 0 scopes; extending later is additive.

---

## 5. Client-by-client specification

### 5.1 `contact-ops-mcp`

The canonical MCP server. This is the audience every other client requests tokens for.

| Property | Value |
|----------|-------|
| Display name | Contact-Ops MCP Server |
| Client ID | `contact-ops-mcp` |
| Type | confidential |
| Access type | `bearer-only` (no browser flow, no direct grants) |
| Standard flow | OFF |
| Implicit flow | OFF |
| Direct access grants | OFF |
| Service accounts | ON (for self-introspection + audit posting) |
| Authorization | OFF (Phase 0; revisit in Phase 2 for fine-grained acl) |
| Root URL | `https://mcp.contacts.magicunicorn.dev` |
| Base URL | `/` |
| Valid redirect URIs | none (bearer-only) |
| Web origins | none |
| Admin URL | `https://mcp.contacts.magicunicorn.dev/admin/keycloak` (for back-channel logout) |

**Default client scopes**: `openid`, `profile`, `email`, `user-attributes`, `roles`
**Optional client scopes**: every Phase 0 scope from §4 — `person:read`, `person:write`, `person:propose_merge`, `person:apply_merge`, `person:bulk`, `org:read`, `org:write`, `org:bulk`, `field:propose_set`, `field:apply_set`, `relationship:propose`, `relationship:apply`, `tag:apply`, `voice:match`, `voice:assign`, `agent:register`, `admin:tenant`, `carddav:sync`, `data-intel:catalogue:submit`, `data-intel:catalogue:search`

**Mappers** (in addition to what `user-attributes` provides):
- `uc_uid` — comes free via `user-attributes` scope (no per-client mapper needed)
- `tenant_id` — user-attribute mapper, reads `tenant_id` user attribute, emits as `tenant_id` claim
- `tenant_slug` — user-attribute mapper, reads `tenant_slug`, emits as `tenant_slug`
- `tenant_hipaa` — user-attribute mapper, reads `tenant_hipaa` (string `"true"`/`"false"`), emits as `tenant_hipaa` boolean
- `act` — hardcoded-claim mapper, value `{}` (placeholder; actual `act` chain comes from token exchange, not this mapper). This mapper exists only to register the claim shape with introspection.
- `aud` — audience mapper, value `contact-ops-mcp` (Keycloak adds this automatically when a token is for this client)
- realm roles → `realm_access.roles` (default Keycloak behavior, retained)
- client roles → `resource_access.contact-ops-mcp.roles`

**OBO trust** (who can request a token with `aud=contact-ops-mcp`):
- `contact-ops-app` (browser flow with PKCE) — yes
- `contact-ops-publisher` (via token exchange when relaying inbound proposals to itself) — yes
- `contact-ops-bridge-inbound` (Data Intel pushing inbound enrichments) — yes
- Trusted ecosystem peer clients per `project_uc_mcp_federation.md`: `meeting-ops-mcp`, `project-ops-mcp`, `crisis-ops-mcp`, `data-intel-mcp`, `brigade-mcp` — yes, via RFC 8693 token exchange. Each ecosystem MCP is registered separately and granted `token-exchange` permission against this client.

**Session / token lifetimes**:
- Access token: 300s (5 min) — matches realm default per `reference_keycloak_topology.md`
- Refresh: not applicable (bearer-only)
- Client session: irrelevant (bearer-only doesn't carry a session)

**Audience whitelist (RFC 8707 `resource` indicator)**: tokens for this client MUST carry `aud=contact-ops-mcp`. The MCP server rejects any token where `aud` does not include `contact-ops-mcp` even if scopes look right; this defeats confused-deputy attacks where a token minted for a different audience leaks into MCP traffic.

---

### 5.2 `contact-ops-app`

The Phase 3 human UI. Lands now so future PKCE redirects don't require a bootstrap-script rerun against prod Keycloak.

| Property | Value |
|----------|-------|
| Display name | Contact-Ops Web App |
| Client ID | `contact-ops-app` |
| Type | public |
| Access type | `public` (PKCE required, no client secret) |
| Standard flow | ON |
| Implicit flow | OFF (deprecated) |
| Direct access grants | OFF |
| Service accounts | OFF |
| Authorization | OFF |
| Root URL | `https://contacts.magicunicorn.dev` |
| Base URL | `/` |
| Valid redirect URIs | `https://contacts.magicunicorn.dev/auth/callback`, `https://contacts.magicunicorn.dev/auth/silent-renew`, `http://localhost:5173/auth/callback` (dev), `http://localhost:5173/auth/silent-renew` (dev) |
| Valid post-logout redirect URIs | `https://contacts.magicunicorn.dev/`, `http://localhost:5173/` |
| Web origins | `https://contacts.magicunicorn.dev`, `http://localhost:5173`, `+` (per redirect URI) |
| PKCE code challenge method | `S256` (enforced) |

**Default client scopes**: `openid`, `profile`, `email`, `user-attributes`, `roles`, `offline_access`
**Optional client scopes**: `person:read`, `person:write`, `org:read`, `org:write`, `relationship:propose`, `relationship:apply`, `tag:apply`, `voice:match`, `field:propose_set`, `field:apply_set`, `carddav:sync` (so the UI can mint app passwords on behalf of the user)

**Mappers**: same as `contact-ops-mcp` (uc_uid, tenant_id, tenant_slug, tenant_hipaa, aud, realm roles, client roles).

**OBO trust**: this client requests tokens for itself; downstream, the UI exchanges its own access token via RFC 8693 to obtain a token with `aud=contact-ops-mcp` for actual API calls. The token-exchange permission is granted on `contact-ops-mcp` (target), not here.

**Session / token lifetimes**:
- Access token: 300s
- Refresh token: matches realm `ssoSessionMaxLifespan` (1 year per `reference_keycloak_topology.md`)
- Offline token: matches realm `offlineSessionMaxLifespan` (1 year, no hard cap)
- Idle: 30d per realm default

**Audience whitelist**: tokens from this client carry `aud=contact-ops-app` by default; the browser exchanges for `aud=contact-ops-mcp` and `aud=contact-ops-carddav` as needed via token exchange.

**Required actions config**: per `feedback_keycloak_verify_profile.md`, this client's first-login flow respects `VERIFY_PROFILE` — users without firstName/lastName get the profile-completion screen. Don't disable this.

---

### 5.3 `contact-ops-carddav`

Per design doc §3.3, CardDAV at `carddav.contacts.magicunicorn.dev/<user>/<tenant>/`. Legacy clients can't do OIDC; they do HTTP Basic over TLS with per-user app passwords.

| Property | Value |
|----------|-------|
| Display name | Contact-Ops CardDAV |
| Client ID | `contact-ops-carddav` |
| Type | confidential |
| Access type | `confidential` |
| Standard flow | OFF |
| Implicit flow | OFF |
| Direct access grants | ON (resource owner password — needed for app-password minting) |
| Service accounts | ON (for the carddav adapter to introspect tokens server-side) |
| Authorization | OFF |
| Root URL | `https://carddav.contacts.magicunicorn.dev` |
| Base URL | `/` |
| Valid redirect URIs | none (no browser flow) |
| Web origins | none |
| Client secret | rotated at bootstrap, stored in `unicorn-postgresql.contact_ops_db.secrets` (encrypted with tenant DEK) |

**Default client scopes**: `openid`, `profile`, `email`, `user-attributes`, `roles`, `carddav:sync`
**Optional client scopes**: `person:read`, `person:write`, `field:propose_set`, `email:read`, `email:write`, `phone:read`, `phone:write` (the latter four land in Phase 1; bootstrap script declares them as optional so the assignment is forward-compatible)

**Mappers**: uc_uid (via user-attributes), tenant_id, tenant_slug, tenant_hipaa, aud, realm roles, client roles. Also a `device_id` mapper from the `device_id` user attribute (each app password is bound to a device); this lets us revoke a single iPhone without revoking macOS.

**App-password model**: the Contact-Ops backend mints app passwords via a server-side flow that calls Keycloak's Direct Grants endpoint with `client_id=contact-ops-carddav` and the user's master credentials, then issues an `offline_access` token tagged with `device_id`. The HTTP Basic Authorization header at the CardDAV adapter carries `username=uc_uid:device_id` and `password=offline_token`. The carddav adapter introspects the token via Keycloak's `/protocol/openid-connect/token/introspect` endpoint using its own service-account credentials.

**OBO trust**:
- `contact-ops-app` can request `aud=contact-ops-carddav` tokens (browser UI shows "Generate iPhone Contacts password") — yes
- ecosystem peers — no, CardDAV is per-user

**Session / token lifetimes**:
- App-password offline token: 1 year (matches realm `offlineSessionMaxLifespan`), revocable per-device via Keycloak's offline-session endpoint
- Access token (when introspected): not stored; carddav adapter introspects on every PROPFIND

**Audience whitelist**: tokens for this client MUST carry `aud=contact-ops-carddav`. The carddav adapter rejects any token where `aud` is missing this value.

---

### 5.4 `contact-ops-publisher`

Outbound federation. The Data Intel Bridge Agent and the Ecosystem Federation Agent run as this client. Per design doc §6.9 and §7.1, this is the OAuth identity that publishes to `verify.centerdeep.online`.

| Property | Value |
|----------|-------|
| Display name | Contact-Ops Publisher (outbound federation) |
| Client ID | `contact-ops-publisher` |
| Type | confidential |
| Access type | `confidential` |
| Standard flow | OFF |
| Implicit flow | OFF |
| Direct access grants | OFF |
| Service accounts | ON |
| Authorization | OFF |
| Root URL | (none — service identity) |
| Valid redirect URIs | none |
| Web origins | none |
| Client secret | rotated at bootstrap, stored in `unicorn-postgresql.contact_ops_db.secrets` |

**Default client scopes**: `openid`, `profile`, `email`, `roles`, `data-intel:catalogue:submit`, `data-intel:catalogue:search`
**Optional client scopes**: `field:propose_set`, `identifier:propose_add` (Phase 1), `person:read`, `org:read` — required for inbound proposal materialization

**Mappers**: actor-chain mapper (`act` claim), client_id, aud. The service account user has `tenant_id` set to the system-tenant; tenant scoping for actual publishes comes from the OBO chain, not the service-account's own tenant.

**OBO trust** — this client both REQUESTS and IS the TARGET of token exchange:
- IS the requester: when publishing to Data Intel, it does an RFC 8693 exchange that takes the source action_event's actor chain (e.g., `REMOVED` → `contact-ops-relationship-inference-agent`) and nests it under `contact-ops-publisher`, producing a token for `aud=verify.centerdeep.online` with the full nested `act`. This is the pattern in design doc §3.7.
- IS the target: when relaying inbound to its own MCP layer (rare; mostly for self-introspection during outbound retries), it exchanges its own service-account token for `aud=contact-ops-mcp`.

The Keycloak token-exchange permission matrix:
- `contact-ops-publisher` permitted to exchange tokens whose subject is any tenant user (validated by client policy: only when the source token has `aud=contact-ops-mcp` or a recognized ecosystem MCP)
- `contact-ops-publisher` permitted to exchange its OWN service-account token for `aud=contact-ops-mcp` (so it can audit-post)

**Session / token lifetimes**:
- Access token: 600s (10 min — longer than human flow because outbound publishes can stall on network)
- Service-account session: continuous (no SSO session)
- Exchanged tokens: 300s, refreshed on the fly

**Audience whitelist**: tokens FROM this client carry `aud=verify.centerdeep.online` for outbound publish, `aud=contact-ops-mcp` for self-audit. The publisher refuses to call any endpoint where the exchange-issued token's `aud` doesn't include the expected target — this is the RFC 8707 enforcement point on the publisher side.

---

### 5.5 `contact-ops-bridge-inbound`

The inbound channel. Per design doc §7.2, Data Intel pushes proposed enrichments here. Everything that comes in is `propose_only` and gets human review.

| Property | Value |
|----------|-------|
| Display name | Contact-Ops Bridge (inbound from Data Intel) |
| Client ID | `contact-ops-bridge-inbound` |
| Type | confidential |
| Access type | `confidential` |
| Standard flow | OFF |
| Implicit flow | OFF |
| Direct access grants | OFF |
| Service accounts | ON |
| Authorization | OFF |
| Root URL | `https://mcp.contacts.magicunicorn.dev` |
| Valid redirect URIs | none |
| Web origins | none |
| Client secret | rotated at bootstrap |

**Default client scopes**: `openid`, `profile`, `email`, `roles`, `field:propose_set`, `relationship:propose`
**Optional client scopes**: `person:read`, `org:read`, `tag:apply` (apply only — never tag:create from inbound), `voice:match`

**Mappers**: actor-chain mapper (`act`), client_id, aud (`contact-ops-mcp`). The service-account user is bound to a system-tenant for audit; per-call tenant scoping comes from the request body.

**OBO trust**:
- IS the requester: when Data Intel pushes a proposal, Data Intel mints a token with its own client and exchanges for `aud=contact-ops-mcp` via `contact-ops-bridge-inbound`. The chain looks like `data-intel-system → data-intel-enrichment-worker → contact-ops-bridge-inbound`. The Contact-Ops MCP server validates the chain and stores it verbatim in `action_event.actor`.
- IS the target: not directly. Data Intel does NOT mint tokens with `aud=contact-ops-bridge-inbound`; it mints tokens with `aud=contact-ops-mcp` and the `azp` (authorized party) is `contact-ops-bridge-inbound`. The MCP server uses `azp` to discriminate inbound-federation vs. direct-app calls.

**Session / token lifetimes**:
- Access token: 600s
- Service-account session: continuous

**Audience whitelist**: tokens FROM this client (or exchanged through it) carry `aud=contact-ops-mcp`. The `azp` is `contact-ops-bridge-inbound`. MCP server enforces `azp=contact-ops-bridge-inbound` to gate "inbound federation" code paths (always propose-only, never auto-apply except for the per-event-type opt-in per design doc §7.2).

---

## 6. OBO trust matrix (RFC 8693)

The Keycloak `token-exchange` permission feature controls who can mint a token for whom. Phase 0 wires the following permissions:

| Subject token's `azp` (source client) | Target audience (`aud`) | Permitted? | Use case |
|---------------------------------------|--------------------------|------------|----------|
| `contact-ops-app` | `contact-ops-mcp` | yes | Human UI calls MCP |
| `contact-ops-app` | `contact-ops-carddav` | yes | Human UI mints app password |
| `contact-ops-mcp` | `contact-ops-mcp` | no | self-exchange disallowed |
| `contact-ops-mcp` | external (`verify.centerdeep.online`) | no | MCP never publishes directly |
| `contact-ops-publisher` | `verify.centerdeep.online` | yes | Publish outbound |
| `contact-ops-publisher` | `contact-ops-mcp` | yes | Self-audit |
| `contact-ops-bridge-inbound` | `contact-ops-mcp` | yes | Materialize inbound proposal |
| `contact-ops-carddav` | anything | no | terminal — Basic-auth only |
| `meeting-ops-mcp` | `contact-ops-mcp` | yes | Cross-MCP federation (P-00039) |
| `project-ops-mcp` | `contact-ops-mcp` | yes | Cross-MCP federation |
| `crisis-ops-mcp` | `contact-ops-mcp` | yes | Cross-MCP federation |
| `data-intel-mcp` | `contact-ops-mcp` | yes | Data Intel calling Contact-Ops |
| `brigade-mcp` | `contact-ops-mcp` | yes | Research agent federation |
| `listing-ops-mcp` | `contact-ops-mcp` | yes (Phase 5) | Listing-Ops references contacts |
| `stable-mcp` | `contact-ops-mcp` | yes (Phase 5) | Stable references contacts |

The bootstrap script wires the first set (rows 1-8). The ecosystem-peer rows (9-15) require those clients to already exist in `uchub` realm and are wired in their respective service's own bootstrap (Project-Ops MCP already exists; Meeting-Ops MCP per `project_meeting_ops_blocked.md` exists; the others are pending). The bootstrap script DETECTS whether each peer exists and only wires the permission if it does — safe to re-run as peers come online.

Per design doc §3.7, each exchange nests the prior token's claims under `act`. Keycloak supports this natively in 26.x via the `Trusted clients can do impersonation/exchange` permission, with the `requestor` constraint matching the source client.

---

## 7. Resource indicators (RFC 8707)

Every token request carries a `resource` parameter (sometimes multiple). Keycloak 26.x honors this in the token endpoint and the token-exchange endpoint; the resulting access token's `aud` is restricted to the requested resources.

Contact-Ops resources:
- `https://mcp.contacts.magicunicorn.dev` → `aud=contact-ops-mcp`
- `https://carddav.contacts.magicunicorn.dev` → `aud=contact-ops-carddav`
- `https://verify.centerdeep.online` → `aud=verify.centerdeep.online`

The MCP server validates `aud` on every request:
```python
def validate_audience(token: dict) -> bool:
    aud = token.get("aud", [])
    if isinstance(aud, str):
        aud = [aud]
    return "contact-ops-mcp" in aud
```

Tokens minted without a `resource` parameter (legacy clients) get the default audience set per client config (the `Audience` mapper). The bootstrap script sets the default audience per client — so even a token request that forgets `resource=` still ends up with the right `aud`. Belt and suspenders.

---

## 8. Realm role ladder

Per design doc §5 conventions, Contact-Ops uses a four-tier ladder. These are REALM roles (not client roles), assigned to users:

| Role | Powers | Examples |
|------|--------|----------|
| `CLIENT` | read-only on shared records | external partners, customer logins to white-label tenant |
| `STAFF` | write within their tenant; can propose anything, approve their own low-stakes proposals | most Magic Unicorn employees, GFL staff |
| `MANAGER` | merge, ACL, tenant settings, approve high-stakes proposals | tenant owners, team leads |
| `ADMIN` | delete, agent trust tiers, cross-tenant, glass-break ops | Aaron, infra ops |

The bootstrap script creates these realm roles if they don't exist (idempotent). Existing users keep their current role assignments; the script does NOT auto-grant any role to existing users.

For human Keycloak tokens, Contact-Ops uses the higher of the token's realm role and the caller's active workspace membership role for the token's tenant: membership `admin` maps to `ADMIN`, `manager` to `MANAGER`, `member` to `STAFF`, and `viewer` to `CLIENT`. An active membership can therefore add the workspace-scoped role a self-onboarded owner needs, but it never lowers a stronger realm role; PAT and Brigade tokens keep their own validation paths.

Client roles (per `resource_access.<client>.roles`) are NOT used in Phase 0; we keep authorization at the scope + role level and use action_event-side enforcement for fine-grained policy. This avoids the centerdeep-data-intel `keycloak_resource_access` parsing complexity.

---

## 9. Verify-profile + uc_uid bootstrap concerns

Per `feedback_keycloak_verify_profile.md`: if a service-account user lacks firstName/lastName, certain OIDC flows trigger the VERIFY_PROFILE required-action mid-flow and the user thinks SSO is broken. The bootstrap script sets firstName/lastName on every service-account user it creates (per-client):

- `contact-ops-mcp` service account: `firstName=Contact-Ops`, `lastName=MCP-Server`
- `contact-ops-publisher` service account: `firstName=Contact-Ops`, `lastName=Publisher`
- `contact-ops-bridge-inbound` service account: `firstName=Contact-Ops`, `lastName=Bridge-Inbound`
- `contact-ops-carddav` service account: `firstName=Contact-Ops`, `lastName=CardDAV`

The auto-uc_uid SPI (per `reference_keycloak_auto_ucuid.md`) will populate `uc_uid` for these accounts automatically. The bootstrap script does NOT manually set `uc_uid` — that's the SPI's job. If for some reason the SPI is not deployed on the target realm, the bootstrap script warns and sets `uc_uid = service-account-<client_id>` explicitly so downstream services that key on `uc_uid` don't fail.

---

## 10. Idempotency contract

The bootstrap script (`scripts/keycloak_bootstrap.sh`) is safe to re-run. It uses Keycloak's `kcadm.sh` to:

1. Check if a client exists (`kcadm.sh get clients -r uchub -q clientId=$cid --fields id`)
2. If exists, UPDATE in place (`kcadm.sh update clients/$id -f -`)
3. If not, CREATE (`kcadm.sh create clients -r uchub -f -`)
4. For each scope, check if it's already in the client's defaultClientScopes / optionalClientScopes before adding
5. For each mapper, check by mapper name before creating

Effects of re-running on an already-bootstrapped realm:
- No duplicate clients
- No duplicate scopes
- No duplicate mappers
- No secret rotation (existing secrets preserved; new secret is minted only on first creation). Pass `--rotate-secrets` to force rotation.

---

## 11. Operations cheat sheet

```bash
# View all Contact-Ops clients
kcadm.sh get clients -r uchub -q 'clientId=contact-ops-*' --fields id,clientId,protocol,publicClient

# View one client's full config
kcadm.sh get clients/$(kcadm.sh get clients -r uchub -q clientId=contact-ops-mcp --fields id --format csv --noquotes -q clientId=contact-ops-mcp) -r uchub

# View what scopes a client has
kcadm.sh get clients/$ID/default-client-scopes -r uchub
kcadm.sh get clients/$ID/optional-client-scopes -r uchub

# View a service account user's attributes (kcadm hides unmanaged attrs, use REST)
curl -H "Authorization: Bearer $(kcadm.sh config credentials ... && kcadm.sh get token)" \
  https://auth.unicorncommander.ai/admin/realms/uchub/users/$UID

# Mint a service-account token (for testing)
curl -X POST https://auth.unicorncommander.ai/realms/uchub/protocol/openid-connect/token \
  -d "grant_type=client_credentials" \
  -d "client_id=contact-ops-publisher" \
  -d "client_secret=$SECRET" \
  -d "scope=data-intel:catalogue:submit"

# Token exchange (RFC 8693) — example: contact-ops-app → aud=contact-ops-mcp
curl -X POST https://auth.unicorncommander.ai/realms/uchub/protocol/openid-connect/token \
  -d "grant_type=urn:ietf:params:oauth:grant-type:token-exchange" \
  -d "subject_token=$USER_TOKEN" \
  -d "subject_token_type=urn:ietf:params:oauth:token-type:access_token" \
  -d "client_id=contact-ops-app" \
  -d "client_secret=$SECRET" \
  -d "audience=contact-ops-mcp"
```

---

## 12. Open questions

Tracked in `/Users/aaronstransky/Documents/Contact-Ops-Open-Questions.md` as they arise. Initial set:

- (Q-KC-01) Should `contact-ops-app` get `admin:tenant` scope optional, or only `contact-ops-mcp` service accounts? Tentative: only MCP service accounts; humans use the MCP via their own user token.
- (Q-KC-02) Ecosystem peer registration — when Listing-Ops/Stable land, do we centralize their token-exchange permission grant here, or have each peer's bootstrap declare its own outbound? Tentative: each peer declares outbound; the bootstrap below only declares inbound permissions for clients THAT ALREADY EXIST.
- (Q-KC-03) Phase 2: client-roles vs. scope-only authorization. Revisit when fine-grained ACL lands.
- (Q-KC-04) Should the CardDAV app-password flow use Keycloak's user-managed app passwords (Keycloak 26.x supports this natively) instead of our own offline-token-tagged-with-device approach? Tentative: defer to Phase 2 when CardDAV lands; Phase 0 just configures the client.

---

## 13. References

- Design doc `/Users/aaronstransky/Documents/Contact-Ops-MCP-Design.md` §3.5, §3.6, §3.7, §6, §7
- `reference_keycloak_topology.md` — two-instance federation
- `reference_keycloak_admin.md` — where the live admin password lives
- `reference_keycloak_user_attributes.md` — user-attributes scope and its 6 mappers
- `feedback_oidc_user_id_canary.md` — `sub` for identity, `uc_uid` for display
- `reference_keycloak_auto_ucuid.md` — auto-uc_uid SPI on both realms
- `feedback_keycloak_verify_profile.md` — firstName/lastName trap
- `project_uc_mcp_federation.md` — RFC 8693 OBO pattern across ecosystem MCPs
- ADR-0003 at `docs/decisions/0003-keycloak-client-topology.md`
