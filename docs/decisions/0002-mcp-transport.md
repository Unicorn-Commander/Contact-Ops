# ADR-0002: JSON-RPC over HTTP POST for the MCP transport

**Status**: Accepted
**Date**: 2026-05-21
**Deciders**: Aaron Stransky, Claude (orchestrator)

## Context

The Model Context Protocol spec (2025-11-25) supports two transports:

- **stdio** — process spawning, stdin/stdout JSON-RPC. The default for local MCP servers spawned by a host like Claude Desktop.
- **HTTP / Streamable HTTP** — long-lived HTTP connection, JSON-RPC envelopes, optional SSE for server-to-client notifications.

Contact-Ops is a multi-tenant SaaS-shaped service that needs to be reachable by:
- Claude Code (Aaron's local CLI) — could use either stdio or HTTP.
- Other ecosystem services (Listing-Ops, Crisis-Ops, Project-Ops, Meeting-Ops, Stable, Brigade) — must be HTTP because they live in containers and call across the network.
- Future hosted users (Phase 6+ white-label) — must be HTTP (per-customer instances reachable via vanity domain `mcp.contacts.<customer-domain>`).
- AI agents (Contact-Ops Dedup, Voice Match, etc.) running in their own containers — must be HTTP for the same reason.

stdio is the lighter weight protocol but it requires the host to spawn the server process. Contact-Ops's federation pattern (RFC 8693 OBO token exchange, audit log writes, FalkorDB graph sync writes) requires long-running server state — re-spawning per request is operationally wrong.

## Decision

Contact-Ops MCP server uses **JSON-RPC 2.0 over HTTP POST** at `mcp.contacts.magicunicorn.dev/mcp` (and per-tenant white-label aliases).

- Single POST endpoint, request body is JSON-RPC envelope (single request or batch array).
- Standard JSON-RPC response: result envelope on success, `{error: {code, message}}` envelope on application errors, all at HTTP 200.
- HTTP 4xx/5xx reserved for transport-layer failures (malformed JSON, auth failure, server crashed).
- Notifications (requests with no `id`) return HTTP 204 with no body.
- Streamable HTTP (SSE) deferred to Phase 1 when streaming MCP tools (e.g., long-running enrichment, batch import) need it. The Phase 0 scaffold uses plain POST.

## Consequences

**Enables:**
- Same endpoint works for Claude Code (over Tailscale to mcp.contacts.magicunicorn.dev), for ecosystem services (internal docker network), and for future white-label customers (public TLS).
- JWT-bearer auth via standard `REMOVED` header — sits naturally on HTTP, awkward over stdio.
- Standard observability (Traefik access logs, Loki ingestion, latency histograms via Prometheus) without extra plumbing.
- Long-running server state (JWKS cache, FalkorDB connection pool, audit pool) lives in the FastAPI process across requests, not per-spawn.

**Costs:**
- Slightly more overhead per request than stdio (TCP roundtrip + TLS handshake on cold connections). Mitigated by HTTP/1.1 keepalive + HTTP/2 if the proxy supports it.
- Notification semantics are awkward on plain POST — clients that want server-pushed events (e.g., "your merge proposal was approved") need polling or, in Phase 1, SSE upgrade.
- Standalone-mode (no Keycloak) testing requires temporary STANDALONE_MODE env var rather than just running the server in a different parent process.

**Reversibility:**
- Easy to add stdio later if a local-spawn use case emerges. The protocol is the same; only the transport differs.
- Easy to add Streamable HTTP / SSE in Phase 1 when streaming tools need it.

## Alternatives considered

**1. stdio only.** Rejected: doesn't work for the cross-service call pattern, doesn't support multi-tenant TLS endpoints, doesn't let JWKS cache survive across requests.

**2. Streamable HTTP from day one.** Rejected for Phase 0 — adds complexity (SSE upgrade negotiation, keep-alive ping handling, client disconnect tracking) we don't need until Phase 1 tools ship. Promoted to Phase 1 when the first long-running tool lands (likely the bulk-import or web-research enrichment tools).

**3. gRPC.** Rejected: not an MCP-spec-supported transport. Would require a non-standard bridge layer for MCP clients (Claude Code, etc.) that only speak JSON-RPC.

**4. WebSocket.** Rejected: 2025-11-25 MCP spec deprecated WebSocket in favor of Streamable HTTP. Don't ship a deprecated transport.

## Implementation notes

- Endpoint path: `/mcp` (POST). The JWT middleware does NOT bypass `/mcp` — tenant comes from the validated JWT claim, never from request headers.
- JSON-RPC version: `"2.0"` exact match required (rejected with `-32600 Invalid Request` if anything else).
- Batch support: arrays of requests are dispatched serially in Phase 0. Notifications inside a batch don't produce response entries. Empty batch returns `-32600`.
- Tenant resolution: `request.state.jwt_claims["tenant_id"]` is the only trusted source. Trying to read `X-Tenant-Id` header explicitly is a code smell — flag in review.
- Per-request server instance: `MCPServerInstance(tenant_id=, user_id=)` is constructed per request and discarded. State that needs to live longer (JWKS cache, audit pool) lives on the FastAPI app singleton, not the MCP server instance.
- Protocol version: server advertises `2025-11-25`. Clients sending an older `protocolVersion` in `initialize` get echoed our version (we accept anything in Phase 0; Phase 1 may need negotiation).
- Capabilities advertised: `{"tools": {"listChanged": false}}`. Resources, prompts, and logging capabilities are not advertised in Phase 0 (no tools yet).
