<p align="center">
  <img src="frontend/public/brand/officer.png" width="128" alt="Contact-Ops officer mascot">
</p>

<h1 align="center">Contact-Ops</h1>

<p align="center">
  <strong>A self-hosted contact and identity graph where agents propose changes and humans approve them.</strong>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="Apache-2.0 license"></a>
  <a href="frontend/package.json"><img src="https://img.shields.io/badge/frontend-v2.18.5-2ea44f.svg" alt="Frontend version 2.18.5"></a>
  <img src="https://img.shields.io/badge/frontend-React%20%2B%20TypeScript-61dafb.svg" alt="React and TypeScript frontend">
  <img src="https://img.shields.io/badge/backend-FastAPI-009688.svg" alt="FastAPI backend">
  <img src="https://img.shields.io/badge/deployment-self--hosted-6f42c1.svg" alt="Self-hosted deployment">
  <img src="https://img.shields.io/badge/agents-MCP--native-ff6b35.svg" alt="MCP-native agent surface">
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#highlights">Features</a> ·
  <a href="#built-for-agents">Agents</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#part-of-unicorn-commander">Suite</a>
</p>

<p align="center">
  <img src="docs/screenshots/people.webp" width="1200" alt="Contact-Ops people directory with demo contacts">
</p>

## What is Contact-Ops?

Contact-Ops is the shared contact and identity graph for teams and the agents that work with them. It brings people, organizations, contact points, identifiers, employment, tags, and relationships into tenant-scoped canonical records. Agents can search, import, enrich, and propose changes; people review the evidence and approve what becomes canonical. It is self-hostable and designed to share identity, scoped data, and agent interfaces with the Unicorn Commander suite.

## Highlights

- **Canonical people and organizations** — keep contact points, names, identifiers, roles, and tags attached to tenant-scoped records instead of scattered source copies.
- **Relationship intelligence** — explore a FalkorDB graph projection with paths, mutual connections, clusters, introductions, and duplicate discovery.
- **Human-controlled change management** — route agent suggestions, imports, and connector pulls into a review queue with evidence, confidence, approve/reject/snooze actions, and reversible auto-applies.
- **Source-aware imports** — bring in vCards and contact CSVs, or pull from iCloud, Microsoft 365, and Google People; proposed changes stay reviewable by default.
- **Agent-native operations** — expose the contact graph through an authenticated MCP server, a discovery document, scoped tools, and optional Brigade registration/federation.
- **AI Contacts Analyst** — stream an optional chat-based analyst through an OpenAI-compatible LLM endpoint, with tools that propose changes rather than silently applying them.
- **Tenant and access boundaries** — use Keycloak/OIDC, personal access tokens, role and scope checks, Postgres row-level security, and audit events.
- **Familiar contact access** — serve CardDAV with per-device app passwords so contact clients can read and write through a controlled interface.

## Feature tour

### Canonical profiles

<p align="center">
  <img src="docs/screenshots/person.webp" width="1100" alt="Contact-Ops person profile merged from multiple sources">
</p>

A person profile combines source observations into one record with identity fields, contact points, organization context, notes, tags, and relationship history. The same tenant-scoped model is available to the web UI, REST API, and MCP tools.

### Relationship graph

<p align="center">
  <img src="docs/screenshots/graph.webp" width="1100" alt="Contact-Ops relationship graph showing connected people and organizations">
</p>

The graph view supports tenant-wide overviews and person-centered ego graphs, with filters and path-oriented queries such as `shortest_path`, `who_knows`, `mutual_connections`, and `suggest_intro`. FalkorDB holds the graph projection while Postgres remains the canonical record store.

### Review Queue

<p align="center">
  <img src="docs/screenshots/review.webp" width="1100" alt="Contact-Ops Review Queue with proposed merges and fixes awaiting approval">
</p>

Imports, connector pulls, deduplication, and agent workflows emit proposals instead of bypassing review. The Review Queue exposes evidence and supports individual or bulk decisions, snoozing, conflict resolution, and reverting an auto-applied proposal where the workflow permits it.

### Connectors and imports

<p align="center">
  <img src="docs/screenshots/connectors.webp" width="1100" alt="Contact-Ops connectors and contact import controls">
</p>

Configure iCloud with a CardDAV app-specific password, or use OAuth for Microsoft 365 and Google People. Pulls are recorded as connector runs and flow into the same proposal path; vCard and CSV imports provide preview/propose behavior for files.

## Built for agents

Contact-Ops exposes an HTTP MCP endpoint at `/mcp`. It accepts JSON-RPC 2.0 `initialize`, `tools/list`, and `tools/call` requests. The current source registers **168 MCP tools**, including:

- people and organization search and retrieval: `search_people`, `get_person`, `search_organizations`
- graph exploration: `graph_overview`, `shortest_path`, `find_duplicates_graph`
- safe ingestion: `propose_vcard_records`, `propose_csv_records`, `pull_connector_now`
- human approval: `list_pending_proposals`, `get_proposal_evidence`, `approve_proposal`, `reject_proposal`
- access and integration administration: `list_connectors`, `issue_personal_access_token`

Requests are tenant-bound from verified authentication claims. The middleware supports Keycloak/OIDC bearer JWTs, scoped personal access tokens, and optional Brigade federation JWTs; `/mcp` is not an unauthenticated development bypass. The public discovery route is `/.well-known/mcps.json`. The optional AI Contacts Analyst is available through the streaming `/chat` endpoint, and the Brigade descriptor declares these agent roles:

| Agent role | Purpose |
|---|---|
| `ai-contacts-analyst` | Interactive contact research and proposed actions |
| `dedup-agent` | Background duplicate detection and merge proposals |
| `confidence-approver` | Confidence-based proposal handling |
| `quality-filter-agent` | Contact-quality filtering and cleanup |

An HTTP-capable MCP client can use a configuration shaped like this:

```json
{
  "mcpServers": {
    "contact-ops": {
      "url": "https://<contact-ops-host>/mcp",
      "headers": {
        "Authorization": "Bearer <contact-ops-access-token>"
      }
    }
  }
}
```

Keep the access token in the client’s secret store. Use placeholders in shared configuration, tickets, and examples.

## Quick start

### Docker self-host cell

The supported stranger install is [`docker-compose.selfhost.yml`](docker-compose.selfhost.yml). It bundles Postgres with pgvector, Redis, FalkorDB, Qdrant, Garage, Keycloak, the API/MCP server, and the web UI. The generated `.env` stays local and the compose file binds the browser-facing services to loopback.

Requirements: Docker Engine with the Compose plugin, `curl`, `openssl`, and Python 3. The first build downloads container images and application dependencies.

Run this from the repository root:

```bash
set -euo pipefail

cp deploy/selfcontained/.env.selfcontained.template .env

pg_admin_password="$(openssl rand -hex 24)"
co_runtime_password="$(openssl rand -hex 24)"
kc_admin_password="$(openssl rand -hex 24)"
bootstrap_password="$(openssl rand -hex 24)"
switch_secret="$(openssl rand -hex 32)"
qdrant_api_key="$(openssl rand -hex 32)"
garage_rpc_secret="$(openssl rand -hex 32)"
garage_admin_token="$(openssl rand -hex 32)"
connector_key="$(python3 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"

python3 - "$pg_admin_password" "$co_runtime_password" "$kc_admin_password" "$bootstrap_password" "$switch_secret" "$qdrant_api_key" "$garage_rpc_secret" "$garage_admin_token" "$connector_key" <<'PY'
from pathlib import Path
import re
import sys

path = Path(".env")
text = path.read_text(encoding="utf-8")
values = {
    "PG_ADMIN_PASSWORD": sys.argv[1],
    "CO_RUNTIME_PASSWORD": sys.argv[2],
    "KC_ADMIN_PASS": sys.argv[3],
    "KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD": sys.argv[4],
    "KEYCLOAK_SWITCH_CLIENT_SECRET": sys.argv[5],
    "QDRANT_API_KEY": sys.argv[6],
    "GARAGE_RPC_SECRET": sys.argv[7],
    "GARAGE_ADMIN_TOKEN": sys.argv[8],
    "CONNECTOR_ENCRYPTION_KEY": sys.argv[9],
    "DATABASE_URL": f"postgresql+asyncpg://contact_ops_runtime:{sys.argv[2]}@contact-ops-postgres:5432/contact_ops_db",
    "AUDIT_DATABASE_URL": f"postgresql+asyncpg://contact_ops_runtime:{sys.argv[2]}@contact-ops-postgres:5432/contact_ops_db",
    "MIGRATION_DATABASE_URL": f"${CONTACT_OPS_DATABASE_URL}",
}
for key, value in values.items():
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    text, count = pattern.subn(lambda _: f"{key}={value}", text, count=1)
    if count != 1:
        raise SystemExit(f"missing template key: {key}")
path.write_text(text, encoding="utf-8")
path.chmod(0o600)
PY

docker compose -f docker-compose.selfhost.yml up -d --build

ready=0
for attempt in $(seq 1 60); do
  if curl -fsS -o /dev/null http://127.0.0.1:8501/health/ready; then
    ready=1
    break
  fi
  sleep 2
done
test "$ready" -eq 1
curl -fsS http://127.0.0.1:8501/health/ready

# Load the generated values into this shell without printing them.
set -a
. ./.env
set +a

admin_token() {
  curl -fsS -X POST "$KEYCLOAK_ISSUER/protocol/openid-connect/token" -H 'Content-Type: application/x-www-form-urlencoded' --data-urlencode grant_type=password --data-urlencode client_id=contact-ops-app --data-urlencode username="$KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME" --data-urlencode password="$KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD" --data-urlencode scope='openid profile email' | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
}

# The generated username and password also work in a browser at the UI URL.
access_token="$(admin_token)"
onboard_response="$(curl -fsS -X POST http://127.0.0.1:8501/api/auth/onboard -H "Authorization: Bearer $access_token")"
printf '%s\n' "$onboard_response" | python3 -c 'import json,sys; body=json.load(sys.stdin); assert body["provisioned"] is True; print("admin workspace: " + body["tenant_slug"])'

# Re-mint after onboarding so the token contains the new tenant claim.
access_token="$(admin_token)"
pat_response="$(curl -fsS http://127.0.0.1:8501/mcp -H "Authorization: Bearer $access_token" -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"issue_personal_access_token","arguments":{"display_name":"local-agent"}},"id":"pat-request"}')"
agent_pat="$(printf '%s' "$pat_response" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["structuredContent"]["token_plaintext"])')"
test -n "$agent_pat"
export CONTACT_OPS_PAT="$agent_pat"
printf '%s\n' 'Agent access token issued and kept in CONTACT_OPS_PAT.'
```

Open `http://127.0.0.1:3000/` and sign in with the generated `admin` account from `.env`. The default `SIGNUP_ENABLED=false` keeps self-registration off; set it to `true` in `.env` before the first `up` if this local Keycloak realm should show its registration screen. No external account service is needed for this cell.

The API readiness URL is `http://127.0.0.1:8501/health/ready`. The local OIDC issuer is `http://contact-ops.localhost:8080/realms/contact-ops`; the browser resolves the `.localhost` hostname to loopback and the compose network gives Keycloak the same alias for server-side onboarding.

To inspect startup failures or stop the cell:

```bash
docker compose -f docker-compose.selfhost.yml logs --tail=100 contact-ops-backend
docker compose -f docker-compose.selfhost.yml down
```

### Run from source

For backend work, the development compose file supplies Postgres and Redis. It enables the backend’s development-only standalone authentication mode when the backend is run by Compose; a source-run process should set the same environment deliberately.

```bash
docker compose up -d postgres redis

python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt

# Set these to the database and Redis values used by your local services.
export DATABASE_URL='postgresql+asyncpg://<database-user>:<database-password>@127.0.0.1:5432/<database-name>'
export MIGRATION_DATABASE_URL='postgresql://<database-user>:<database-password>@127.0.0.1:5432/<database-name>'
export REDIS_URL='redis://127.0.0.1:6379/5'
export STANDALONE_MODE=true
export ENV=dev

cd backend
alembic upgrade head
uvicorn contact_ops.main:app --reload --host 0.0.0.0 --port 8501
```

The frontend has its own Vite development server:

```bash
cd frontend
npm ci
VITE_MCP_BASE_URL='http://127.0.0.1:8501' VITE_KEYCLOAK_ISSUER='https://<keycloak-issuer>' VITE_KEYCLOAK_CLIENT_ID='<client-id>' npm run dev
```

Use the self-host compose path above when you need the complete Keycloak bootstrap and bundled data services. Use [`docker-compose.yml`](docker-compose.yml) when developing against the source-mounted development services.

### Configuration

The root [`.env.example`](.env.example) documents shared deployments; [`deploy/selfcontained/.env.selfcontained.template`](deploy/selfcontained/.env.selfcontained.template) is the contract for the bundled self-host cell. Values are intentionally omitted here.

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | Async PostgreSQL DSN for canonical application data. |
| `AUDIT_DATABASE_URL` | PostgreSQL DSN used for audit writes; can share the database in development. |
| `MIGRATION_DATABASE_URL` | Synchronous PostgreSQL DSN used by Alembic migrations. |
| `REDIS_URL` | Redis connection for cache, rate limits, and task-broker functions. |
| `STANDALONE_MODE` | Development-only authentication bypass switch; keep it disabled for authenticated deployments. |
| `KEYCLOAK_ISSUER` | OIDC issuer used to validate browser and service JWTs. |
| `KEYCLOAK_REALM` | Keycloak realm used by the deployment. |
| `KEYCLOAK_AUDIENCE` | Expected audience for Contact-Ops access tokens. |
| `CONNECTOR_ENCRYPTION_KEY` / `CONNECTOR_ENCRYPTION_KEYS` | Fernet key or ordered key ring for encrypted connector credentials. |
| `M365_CLIENT_ID` / `M365_CLIENT_SECRET` / `M365_REDIRECT_URI` | Microsoft 365 OAuth application settings. |
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` / `GMAIL_REDIRECT_URI` | Google People OAuth application settings. |
| `FRONTEND_URL` | Browser origin used by backend redirects and connector callbacks. |
| `LLM_BASE_URL` / `LLM_API_KEY` | Optional OpenAI-compatible endpoint and credential for the AI Contacts Analyst. |
| `QDRANT_URL` | Qdrant endpoint for voice-embedding workflows. |
| `FALKORDB_URL` | FalkorDB endpoint for the relationship graph. |
| `GARAGE_ENDPOINT` | S3-compatible Garage endpoint for media and export storage. |
| `BRIGADE_REGISTRY_URL` / `BRIGADE_SERVICE_TOKEN` | Optional agent registry registration and federation settings. |
| `LOG_LEVEL` | Application logging level. |

## Architecture

```mermaid
flowchart LR
    browser["React + TypeScript SPA<br/>Vite + nginx"]
    clients["MCP clients<br/>suite agents"]
    oidc["Keycloak OIDC"]
    api["FastAPI<br/>REST + JSON-RPC MCP"]
    pg[("Postgres + pgvector<br/>canonical data + RLS")]
    redis[("Redis<br/>cache + limits + broker")]
    graph[("FalkorDB<br/>relationship graph")]
    qdrant[("Qdrant<br/>voice embeddings")]
    garage[("Garage<br/>S3-compatible storage")]
    workers["Celery worker + beat<br/>compose overlays"]
    brigade["Brigade registry<br/>optional federation"]
    llm["OpenAI-compatible LLM<br/>optional Analyst"]
    sources["iCloud / Microsoft 365 / Google People"]
    carddav["CardDAV clients"]

    browser -->|OIDC + HTTPS| oidc
    browser -->|REST + SSE| api
    clients -->|POST /mcp| api
    carddav -->|HTTP Basic CardDAV| api
    api -->|JWT / JWKS| oidc
    api --> pg
    api --> redis
    api --> graph
    api --> qdrant
    api --> garage
    api -->|OAuth callbacks + pulls| sources
    api -->|optional registration + trust| brigade
    api -->|optional streaming chat| llm
    workers --> pg
    workers --> redis
    workers --> graph
    workers --> qdrant
    workers --> garage
```

The main components are:

- **Web UI** — React and TypeScript, built with Vite and served by nginx in the self-host image.
- **API and agent gateway** — FastAPI serves the REST API, streaming analyst chat, CardDAV, health probes, discovery, and the authenticated JSON-RPC MCP endpoint.
- **Canonical data layer** — Postgres with pgvector stores tenant-scoped people, organizations, contact points, proposals, memberships, and audit data under row-level security.
- **Supporting data services** — Redis handles cache/rate-limit/task-broker concerns; FalkorDB holds the relationship projection; Qdrant supports voice embeddings; Garage provides S3-compatible media/export storage.
- **Identity and federation** — Keycloak supplies the bundled OIDC realm; optional Brigade registration and JWT federation connect the agent surface to the suite.
- **Connectors and workers** — source connectors pull iCloud, Microsoft 365, and Google People data; Celery worker/beat services are available through the relevant development or deployment overlays.

For a deeper implementation map, see [`ARCHITECTURE.md`](ARCHITECTURE.md) and [`INTEGRATION_GUIDE.md`](INTEGRATION_GUIDE.md).

## Part of Unicorn Commander

| App | What it does |
|---|---|
| [Ops-Center](https://github.com/Unicorn-Commander/Ops-Center) | The control plane: tenants, billing, federation, API keys, model routing |
| [Unicorn-Brigade](https://github.com/Unicorn-Commander/Unicorn-Brigade) | The agent layer: agents, tools, memory, orchestration |
| [Unicorn-Stable](https://github.com/Unicorn-Commander/Unicorn-Stable) | Humans and agents in the same room: chat, voice, video |
| [Accounting-Ops](https://github.com/Unicorn-Commander/Accounting-Ops) | Agent-native accounting and books |
| **[Contact-Ops](https://github.com/Unicorn-Commander/Contact-Ops)** | The shared contact and identity graph |
| [Customer-Ops](https://github.com/Unicorn-Commander/Customer-Ops) | Customers, pipeline, quotes and outreach |
| [Email-Ops](https://github.com/Unicorn-Commander/Email-Ops) | Agent-drafted email with a human approval inbox |
| [Project-Ops](https://github.com/Unicorn-Commander/Project-Ops) | Projects, tasks, time and invoices |
| [Meeting-Ops](https://github.com/Unicorn-Commander/Meeting-Ops) | Recordings, transcripts, summaries and action items |
| [Knowledge-Ops](https://github.com/Unicorn-Commander/Knowledge-Ops) | The wiki and memory your agents ground on |
| [Security-Ops](https://github.com/Unicorn-Commander/Security-Ops) | Authorized security scanning with engagement and attestation gates |
| [Tax-Planning-Ops](https://github.com/Unicorn-Commander/Tax-Planning-Ops) | Tax plans, scenarios and statutory deadlines |
| [Brand-Ops](https://github.com/Unicorn-Commander/Brand-Ops) | Brand kits, marks, voices and rules |
| [Podcast-Ops](https://github.com/Unicorn-Commander/Podcast-Ops) | Episodes, audio QA and publishing |
| [UUDA](https://github.com/Unicorn-Commander/UUDA) | The device agent for your fleet |
| [fleet-map](https://github.com/Unicorn-Commander/fleet-map) | Fleet inventory and topology map |

Contact-Ops connects to the suite through scoped tenant identity, MCP discovery and calls, and optional Brigade registry/JWT federation; its connectors and proposal model keep contact changes reviewable before they become canonical.

## Development

Backend checks run from `backend/`:

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
ruff check contact_ops/
pytest tests/ -v --tb=short
```

Frontend checks run from `frontend/`:

```bash
npm ci
npm run build
npm run lint
npm run test
npm run test:e2e
```

Use synthetic data for local development and keep `.env` files, access tokens, OAuth secrets, and connector credentials out of commits and issue reports.

## Security

Please report vulnerabilities privately using the process in [`SECURITY.md`](SECURITY.md). Do not place credentials, personal contact data, or exploit details in a public issue.

## License

Contact-Ops is licensed under the [Apache License 2.0](LICENSE).

Built by [Magic Unicorn](https://magicunicorn.tech) · part of [Unicorn Commander](https://unicorncommander.ai)
