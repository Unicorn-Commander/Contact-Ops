# ADR-0003: Keycloak Client Topology for Contact-Ops

**Status**: Accepted
**Date**: 2026-05-21
**Deciders**: Aaron Stransky, Claude (orchestrator)
**Related**: `docs/KEYCLOAK_CLIENTS.md`, `scripts/keycloak_bootstrap.sh`

---

## Context

Contact-Ops is an agent-first MCP-native person + organization registry on the Magic Unicorn ecosystem. Auth lives in Keycloak's `uchub` realm, which already serves ~37 ecosystem OIDC clients across two peer-federated instances (commander + bigboy, per `reference_keycloak_topology.md`).

Phase 0 needs to land OIDC clients that cover four distinct auth surfaces:

1. **MCP server bearer validation** — server-to-server, no browser flow. The MCP server validates pre-issued tokens whose `aud` includes `contact-ops-mcp` and whose `act` chain identifies the upstream caller (human or peer MCP).
2. **Browser human UI** (Phase 3, lands now) — PKCE code flow against `contacts.magicunicorn.dev`. Long-lived sessions per realm config (`ssoSessionMaxLifespan=1y`).
3. **CardDAV legacy clients** — iOS Contacts, macOS Contacts, Thunderbird, which can't do OIDC. They authenticate via HTTP Basic over TLS using per-device app passwords backed by Keycloak offline tokens.
4. **Outbound and inbound federation** to Data Intel (and ultimately to peer ecosystem MCPs like Meeting-Ops, Project-Ops, Crisis-Ops). These need RFC 8693 token exchange to nest actor chains across multi-hop calls so the downstream system sees the full provenance from `REMOVED` through the agent that took the action.

Per the canonical design doc §3.5 and §3.7, every Contact-Ops `action_event.actor` field stores the full nested `act` claim verbatim, and `human_authority` denormalizes the root user for fast inbox filtering. That requires the OAuth layer to faithfully propagate `act` through every hop, which in turn requires distinct clients for the source vs. target of each exchange.

Phase 0 commitments per design doc §3.6 also require a granular scope namespace (`person:propose_merge`, `field:propose_set`, `relationship:propose`, `voice:match`, etc.) rather than the centerdeep-data-intel coarse-grained `submit` / `verify` split. Each scope is a Keycloak client scope assigned `optional` on the MCP client and `default` on the publisher/bridge clients per their role.

Memory context that shaped this design:
- `reference_keycloak_user_attributes.md` — the `user-attributes` scope already emits `uc_uid` + subscription claims to any client assigned the scope. We reuse it instead of declaring per-client mappers.
- `reference_keycloak_auto_ucuid.md` — the auto-uc_uid SPI auto-sets `uc_uid` on user creation in both realms. We rely on it for service-account users; we don't fight it.
- `feedback_oidc_user_id_canary.md` — Contact-Ops keys user identity on `sub`, never `preferred_username` or `email`. `uc_uid` is for display only.
- `feedback_keycloak_verify_profile.md` — service-account users without `firstName`/`lastName` trigger the VERIFY_PROFILE required-action mid-flow and OIDC looks broken. The bootstrap script sets both at create time.
- `reference_keycloak_admin.md` — live admin password is in `/home/muut/UC-1-Hub/.env` on commander. Bootstrap reads from `$KC_ADMIN_PASS` env var, never a flag.
- `project_uc_mcp_federation.md` — the RFC 8693 OBO pattern for cross-MCP federation in the ecosystem mesh.

---

## Decision

Contact-Ops registers **five OIDC clients** in the `uchub` realm:

| Client ID | Type | Role |
|-----------|------|------|
| `contact-ops-mcp` | bearer-only, service accounts on | The audience every other client requests tokens for; MCP server validates `aud=contact-ops-mcp`. |
| `contact-ops-app` | public, PKCE code flow | Future human UI at `contacts.magicunicorn.dev`. |
| `contact-ops-carddav` | confidential, direct grants + service accounts | CardDAV adapter accepts HTTP Basic; mints per-device app passwords backed by offline tokens. |
| `contact-ops-publisher` | confidential, service account + token exchange | Outbound federation to Data Intel and peers. Exchanges its own service-account token (or an action's actor chain) for `aud=verify.centerdeep.online`. |
| `contact-ops-bridge-inbound` | confidential, service account | Inbound channel from Data Intel. Tokens minted via token exchange land here with `azp=contact-ops-bridge-inbound` so the MCP server can gate "inbound federation" code paths (always `propose_only`). |

The full client roster, scope assignments, mapper definitions, and OBO trust matrix live in `docs/KEYCLOAK_CLIENTS.md`. The bootstrap script `scripts/keycloak_bootstrap.sh` lands all five clients idempotently and verifies the prerequisites (`user-attributes` scope, realm reachable).

**Scope namespace** is granular per design doc §3.6. Phase 0 lands 20 scopes (person, org, field, relationship, tag, voice, agent, admin, carddav, data-intel). Phase 1+ adds the remaining ~30 (email, phone, address, identifier, merge, acl, interaction, proposal, audit, consent, import, export). Adding scopes later is purely additive — clients are configured with all Phase 0 scopes as `optional` so a token issuer can request whatever subset it needs.

**Role ladder** is realm-level: `CONTACT_OPS_CLIENT < CONTACT_OPS_STAFF < CONTACT_OPS_MANAGER < CONTACT_OPS_ADMIN`, matching design doc §5 conventions. The bootstrap script creates these roles. Client roles (per `resource_access.<client>.roles`) are NOT used in Phase 0 — authorization stays at the scope + role level, with action_event-side enforcement for fine-grained policy. This avoids the centerdeep-data-intel `keycloak_resource_access` parsing pattern that's brittle to refactor.

**Audience enforcement** uses RFC 8707 resource indicators. Tokens MUST carry `aud=contact-ops-mcp` to call the MCP server; tokens MUST carry `aud=contact-ops-carddav` to talk to the CardDAV adapter; tokens MUST carry `aud=https://verify.centerdeep.online` to talk to Data Intel. Each client has a default-audience mapper so even a request that forgets the `resource` parameter still produces the right `aud`. The MCP server, CardDAV adapter, and Data Intel HTTP layer each validate `aud` independently as the first line of defense before any scope check.

**Token-exchange permissions** (RFC 8693) are wired by the bootstrap script as a best-effort grant. Keycloak 26.x requires the `token-exchange` feature flag to be enabled at server start; if it isn't, the grants silently no-op and the script logs a warning. The full matrix lives in `docs/KEYCLOAK_CLIENTS.md` §6; Phase 0 covers 8 edges, with optional grants for ecosystem peer MCPs that get added if those clients are already in the realm.

---

## Consequences

### What this enables

- **Federation with Data Intel** via the well-trodden RFC 8693 path. The publisher mints tokens with `aud=verify.centerdeep.online` and the nested actor chain; the bridge accepts tokens minted by Data Intel where `azp=contact-ops-bridge-inbound`. Both sides can audit each call to a specific source user + agent.
- **CardDAV sync** without polluting the browser flow. The CardDAV adapter has its own audience and its own scopes; the iOS Contacts app gets an offline token bound to a device_id mapper, revocable per-device via Keycloak's session-management endpoints.
- **Agent OBO chains** that survive multi-hop calls. When a Crisis-Ops case triggers a Contact-Ops party search, the resulting `action_event.actor` records `ai-system → crisis-ops-mcp → contact-ops-relationship-inference-agent-v1.2 → REMOVED` exactly as design doc §3.7 specifies.
- **Re-runnable bootstrap** — Aaron can re-run the bootstrap on the same realm to pick up scope additions or mapper changes without risking duplicate clients, scope re-assignments, or accidental secret rotation.
- **Easy peer onboarding** — when Meeting-Ops, Project-Ops, Crisis-Ops, or future ecosystem MCPs come online, re-running the bootstrap automatically wires their token-exchange permission against `contact-ops-mcp`. No hand-edit needed.

### What this costs

- **Five clients to manage** instead of one. Mitigated by the bootstrap script being idempotent + the doc being machine-readable. Aaron doesn't have to click around the Keycloak admin UI to make any of this happen.
- **Keycloak feature dependency** — token-exchange must be enabled on the server at startup (Keycloak `--features=token-exchange,...`). Per `reference_keycloak_admin.md`, the live env is in `/home/muut/UC-1-Hub/.env`; bootstrap warns if the feature is absent.
- **Per-client secret rotation discipline** — three of the five clients are confidential and have secrets. The bootstrap stores them in `unicorn-postgresql.contact_ops_db.secrets` encrypted with the tenant DEK; rotation is a manual `--rotate-secrets` re-run of the bootstrap.
- **Mapper duplication risk** — every client gets its own copy of the tenant_id / tenant_slug / tenant_hipaa mappers. Future improvement: consolidate into a single client scope (Phase 1 cleanup). For Phase 0 the duplication is cheap and explicit.

### What's reversible

- **Topology can be flattened later** if we discover the five-client split is over-engineered. Each client is independent; deleting one doesn't break the others (modulo wiring up token-exchange chains).
- **Scopes can be added or removed** without touching client config. They're separate Keycloak resources; the bootstrap assignment step is purely additive.
- **Token exchange can be disabled** without removing the clients themselves. We'd just stop minting exchange tokens and route everything through direct-client tokens with broader `aud` lists. That regresses the audit story (no nested `act`), but it's a clean fallback.

---

## Alternatives considered

### Single-client model

A single `contact-ops` client with all flows enabled (public + confidential + service accounts + direct grants) and a fat scope list.

**Rejected** because:
- Audience enforcement collapses — you can't have `aud=contact-ops-mcp` vs `aud=contact-ops-carddav` separation if there's one client. RFC 8707 resource indicators have nothing to discriminate on, so a token minted for the browser UI is the same shape as a token for inbound federation. Confused-deputy attacks become trivial.
- Token-exchange `azp` discrimination is impossible. The MCP server can't tell whether a token was minted via Data Intel's inbound channel vs. directly by the browser, so it can't gate inbound-federation paths to `propose_only`.
- Auditing in the Keycloak event log becomes noise — all events show `client_id=contact-ops`, with no way to filter "show me only outbound publishes".

### JWT-only, no OBO

Skip RFC 8693 token exchange entirely. Every service-to-service call uses a service-account token from `client_credentials`, with the upstream identity injected as a custom HTTP header.

**Rejected** because:
- We lose `human_authority` propagation. The downstream system has no cryptographic way to verify the upstream's claim about "this is on behalf of Aaron". The trust collapses to "do I trust this caller, full stop". This breaks the Contact-Ops requirement that `action_event.actor` faithfully records the originating user.
- HIPAA tenant fencing becomes harder. We rely on the token's nested `act` to decide whether a call originated from a HIPAA-marked tenant; with no OBO, the publisher has to make that determination itself based on internal state, which is fragile.
- Audit-trail traversal across systems (Contact-Ops → Data Intel) loses provenance. You can't reconstruct "who did this" without correlating multiple HTTP headers across multiple services.

### OAuth2-proxy fronting everything

Continue with the centerdeep-data-intel pattern: OAuth2-proxy validates tokens at the edge and injects `X-Forwarded-User` + scope headers, the backend trusts the headers.

**Rejected** because:
- Design doc §3.5 mandates that the service validates `aud` AND scope AND actor chain inside itself, not at the edge. OAuth2-proxy can validate `aud` and scope, but it strips the nested `act` claim by the time it forwards. We lose actor-chain audit.
- The MCP server is the product. Putting OAuth2-proxy in front of every MCP call adds a hop and latency to what's already a low-latency tool surface.
- We already have one painful integration with OAuth2-proxy in the centerdeep-data-intel codebase that we're moving away from (note the recent commits `1c943d8 fix: Forward Authorization header in nginx proxy to enable API key auth` and `9652dea fix: Disable header stripping on OAuth2 proxy skipped routes` — these are the kind of glue-tax we're trying to retire).

### Coarse-grained scopes (catalogue:submit / catalogue:verify only)

Reuse the centerdeep-data-intel two-scope split. Don't introduce 20 granular scopes.

**Rejected** because:
- Phase 0 has 16 agents (design doc §6) each with distinct scope requirements per agent specification. Putting all of them on one `catalogue:submit` scope means every agent can do everything, which breaks the propose-only trust model from day one.
- Scope-based gating is how Keycloak event logs become useful — you can filter "show me every token that requested `person:propose_merge`" and see which agents are operating in that envelope.
- Adding granularity later means rewriting every agent's OAuth client config + re-issuing tokens — much more expensive than getting the scopes right now while the surface is small.

---

## Implementation pointers

- Bootstrap: `bash scripts/keycloak_bootstrap.sh` with `KC_SERVER_URL`, `KC_REALM=uchub`, `KC_ADMIN_USER`, `KC_ADMIN_PASS` set. Use `--dry-run` first.
- Verify a token: `curl -s $KC_SERVER_URL/realms/uchub/.well-known/openid-configuration | jq .` for endpoints, then introspect via `/protocol/openid-connect/token/introspect`.
- Trace token exchange in real time: `docker logs uchub-keycloak --since 10s | grep token-exchange`.
- Roll back: delete the five clients (`kcadm.sh delete clients/$id -r uchub`). Scopes are realm-wide and shared with future clients, so leave them in place. Roles too.
