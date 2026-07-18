# Contact-Ops

**Status**: v2.19.x — shipped through `phase-6-complete`. MCP tool surface live (~95 tools across 23 domains). License: Apache-2.0.
**Date**: 2026-07-18

Contact-Ops is Magic Unicorn's canonical person and organization registry. It is an agent-first, MCP-native platform: the MCP server is the product, and every consumer (human UI, CardDAV server, iOS/macOS sync, agent inboxes, ecosystem apps like Listing-Ops, Crisis-Ops, Project-Ops, Meeting-Ops, Stable, Brigade) composes from a single tool surface. Forked from `centerdeep-data-intel`, which contributes the FastAPI + SQLAlchemy 2.0 async + Postgres + Qdrant + Keycloak `uchub` foundation.

Contact-Ops manages the contacts you actively curate. Data Intel (`verify.centerdeep.online`) stays as the verification / intelligence database. They are peer systems federated via RFC 8693 token-exchange OBO; neither owns the other.

## Where things live

| What | Where |
|------|-------|
| AI agent orientation | `CLAUDE.md` |
| Architecture overview | `ARCHITECTURE.md` |
| Ecosystem integration guide | `INTEGRATION_GUIDE.md` |
| Developer workflow | `docs/DEVELOPER_GUIDE.md` |
| End-user guide | `docs/USER_GUIDE.md` |

## Quickstart (local dev)

Local dev workflow:

```bash
# 1. Bring up Postgres + Redis + Qdrant on the unicorn-network
docker compose up -d

# 2. Run migrations
cd backend && alembic upgrade head

# 3. Run tests against the real DB
pytest

# 4. Start the FastAPI + MCP server
uvicorn contact_ops.main:app --reload --port 8501

# 5. Call MCP tools via Claude Code or any MCP client
#    (~95 tools are registered across 23 domains)
```

Production endpoints:

| Endpoint | Purpose |
|----------|---------|
| `contacts.magicunicorn.dev` | Human UI (React/Vite) |
| `mcp.contacts.magicunicorn.dev` | MCP HTTP+SSE endpoint (OAuth 2.1 + RFC 8707) |
| `carddav.contacts.magicunicorn.dev` | CardDAV endpoint for iOS / macOS / Thunderbird |

## Phase plan (from design doc §9)

| Phase | Goal | Duration |
|-------|------|----------|
| 0 | Fork + rename + additive migrations + MCP scaffold | 1-2 weeks |
| 1 | Core MCP tools (people, orgs, employment, identifiers, emails, phones, addresses, tags, search) | 3-4 weeks |
| 2 | Tenancy + ACL + CardDAV + photos | 2-3 weeks |
| 3 | Agents (Dedup, Enrichment, Voice Match, Tag, Lifecycle) + Approval Inbox | 3-4 weeks |
| 4 | FalkorDB graph sync + 3D viewer + relationship tools | 2-3 weeks |
| 5 | Ecosystem migrations (Listing-Ops → Crisis-Ops → Project-Ops → Meeting-Ops → Stable → Brigade) | 4-5 weeks |
| 6 | White-label productization | 3-4 weeks |

## Status

Shipped through `phase-6-complete` (v2.19.x). The Postgres schema (RLS, HIPAA fence, append-only `action_event`) is in `backend/alembic/` (migrations 0001-0045). The MCP server in `backend/contact_ops/mcp/server.py` registers the full tool surface (~95 tools across 23 domains) over the JSON-RPC handshake. The human UI, CardDAV, agent fleet, and FalkorDB graph sync are all live.

## License

Apache-2.0 (see `LICENSE`).
