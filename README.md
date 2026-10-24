<p align="center">
  <img src="frontend/public/brand/officer.png" width="128" alt="Contact-Ops officer mascot">
</p>

<h1 align="center">Contact-Ops</h1>

<p align="center">
  <strong>A self-hosted contact and identity graph where agents propose changes for people to approve.</strong>
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

Contact-Ops is the shared contact and identity graph for teams and the agents that work with them. It brings people, organizations, contact points, identifiers, employment, tags, and relationships into tenant-scoped canonical records. Agents can search, import, and propose changes, and can infer organization links from email domains. People review proposals and approve what becomes canonical. When the confidence approver runs, it auto-applies a proposal at confidence 0.95 or higher when the reversibility class is `reversible` or `reversible_24h`, the proposal is not marked HIPAA, is not cross-tenant, has no parent proposal, and has no open conflict, unless that tenant has `auto_approve_disabled` set. It is self-hostable and can share identity, scoped data, and agent calls with other Unicorn Commander services through Keycloak and optional Brigade registration.

## Highlights

- **Canonical people and organizations** — keep contact points, names, identifiers, roles, and tags attached to tenant-scoped records instead of scattered source copies.
- **Relationship intelligence** — MCP tools query a FalkorDB graph projection for paths, mutual connections, clusters, introductions, and duplicate discovery. The graph page is the overview and the ego view.
- **Human-controlled change management** — route agent suggestions, imports, and connector pulls into a review queue with evidence, confidence, and approve, reject, or snooze actions. The confidence approver can auto-apply a proposal at confidence 0.95 or higher when it is `reversible` or `reversible_24h`, not HIPAA, not cross-tenant, has no parent proposal, and has no open conflict, unless the tenant sets `auto_approve_disabled`. `revert_auto_applied` undoes that within five minutes.
- **Source-aware imports** — bring in vCards and contact CSVs, or pull from iCloud, Microsoft 365, and Google People. Proposals stay in the review queue unless the import sets `auto_approve`.
- **Agent-native operations** — expose the contact graph through an authenticated MCP server, a discovery document, scoped tools, and optional Brigade registration/federation.
- **AI Contacts Analyst** — stream an optional chat analyst through `POST /chat` and an OpenAI-compatible chat endpoint. The curated tools include proposal creators and direct writes such as `append_note`.
- **Tenant and access boundaries** — use Keycloak/OIDC, personal access tokens, role and scope checks, Postgres row-level security, and audit events.
- **Familiar contact access** — serve CardDAV with per-device app passwords so contact clients can read and write through a controlled interface.

## Feature tour

### Canonical profiles

<p align="center">
  <img src="docs/screenshots/organizations.webp" width="1100" alt="Organizations, canonical like people">
</p>

A person record stores identity fields, contact points, organization and employment, identifiers, notes, tags, and relationships. The person page shows the name, pronouns, occupation, headline, relationship category, emails, phones, identifiers, instant-message handles, URLs, names, and photos when the person has them. It does not load addresses, the employment timeline, or the relationship list. The tags tab edits the bio text and does not list tags or notes. MCP tools are the full read and write surface. REST routes under `/api/people` cover a subset of those contact-detail handlers.

### Relationship graph

<p align="center">
  <img src="docs/screenshots/graph.webp" width="1100" alt="Contact-Ops relationship graph showing connected people and organizations">
</p>

The graph page supports a tenant-wide overview and a person-centered ego graph (`graph_overview`, `extract_ego_graph`), with hop, confidence, and relationship-type filters. `shortest_path`, `who_knows`, `mutual_connections`, and `suggest_intro` are MCP tools, not controls on that page. FalkorDB holds the graph projection while Postgres remains the canonical record store.

### Review Queue

<p align="center">
  <img src="docs/screenshots/review.webp" width="1100" alt="Contact-Ops Review Queue with proposed merges and fixes awaiting approval">
</p>

Imports and connector pulls emit proposals, then run `dedup-agent`, the quality filter, and the confidence approver. A proposal emitted for a human actor stores no confidence. An agent-run connector pull stores 0.9, which is under the approver's bar, so those proposals stay for review. A vCard or CSV import with `auto_approve=true` applies the proposals that are still pending. The separate batch dedup agent (slug `dedup`) emits `dedup.propose_merge` proposals and stays off unless `DEDUP_AGENT_ENABLED=true`. The Review Queue shows evidence and supports individual or bulk approve and reject, snoozing, a conflicts-only filter, and undo of a same-session approval (`reject_proposal` with mode `undo`, 30-second server window). `revert_auto_applied` undoes an agent auto-apply within five minutes, and `resolve_conflict_keep_both` keeps both sides of a conflict. The review page does not call those two tools. The quality filter archives a pending `person.create` proposal that has no name, email, or phone, or a generic placeholder name without a human source. The confidence approver auto-applies a proposal at confidence 0.95 or higher when it is `reversible` or `reversible_24h`, not HIPAA, not cross-tenant, has no parent proposal, and has no open conflict, unless the tenant sets `auto_approve_disabled`.

### Connectors and imports

<p align="center">
  <img src="docs/screenshots/connectors.webp" width="1100" alt="Contact-Ops connectors and contact import controls">
</p>

Configure iCloud with a CardDAV app-specific password, or use OAuth for Microsoft 365 and Google People. Pulls are recorded as connector runs and emit proposals. The connector passes confidence 0.9, and a human actor's audit row does not store it. vCard and CSV imports return a preview and create proposals unless `dry_run` is set. `auto_approve` on those imports defaults to false.

## Built for agents

Contact-Ops exposes an HTTP MCP endpoint at `/mcp`. It accepts JSON-RPC 2.0 `initialize`, `tools/list`, and `tools/call` requests. The current source registers **168 MCP tools**, including:

- people and organization search and retrieval: `search_people`, `get_person`, `search_organizations`
- graph exploration: `graph_overview`, `shortest_path`, `find_duplicates_graph`
- imports and connector pulls: `propose_vcard_records`, `propose_csv_records`, `pull_connector_now`
- human approval: `list_pending_proposals`, `get_proposal_evidence`, `approve_proposal`, `reject_proposal`
- access and integration administration: `list_connectors`, `issue_personal_access_token`

Requests are tenant-bound from verified authentication claims. The middleware supports Keycloak/OIDC bearer JWTs, scoped personal access tokens, and optional Brigade federation JWTs; `/mcp` is not an unauthenticated development bypass. `STANDALONE_MODE=true` skips JWT checks for the whole process, including `/mcp`, and refuses to start when `ENV=prod`. The public discovery route is `/.well-known/mcps.json`. The optional AI Contacts Analyst is `POST /chat` (`text/event-stream`) against an OpenAI-compatible `chat/completions` endpoint. Its curated tools include `propose_merge` and `propose_vcard_records`, which create proposals, and `append_note`, which writes a note. `run_confidence_approver_now` can auto-apply. The Brigade descriptor declares these agent roles:

| Agent role | Purpose |
|---|---|
| `ai-contacts-analyst` | Chat orchestrator for contact questions and the curated tool list |
| `dedup-agent` | Collapses duplicate pending `person.create` proposals into one proposal |
| `confidence-approver` | Auto-approves a proposal at confidence 0.95 or higher when it is `reversible` or `reversible_24h`, not HIPAA, not cross-tenant, has no parent proposal, and has no open conflict, unless the tenant sets `auto_approve_disabled` |
| `quality-filter-agent` | Archives a pending `person.create` proposal with no name, email, or phone, or a generic placeholder name without a human source |

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

Before the API starts, `contact-ops-db-bootstrap` runs `alembic upgrade head` and `python -m contact_ops.ops.seed_rls_probes`. That creates the two strict probe tenants named by `RLS_SELFTEST_TENANT_IDS` in the env template. Leave that line as the template ships it.

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

# A ready body contains "status": "ready". A dependency that is still starting returns HTTP 503.

# Load the generated values into this shell without printing them.
set -a
. ./.env
set +a

admin_token() {
  curl -fsS -X POST "$KEYCLOAK_ISSUER/protocol/openid-connect/token" -H 'Content-Type: application/x-www-form-urlencoded' --data-urlencode grant_type=password --data-urlencode client_id=contact-ops-app --data-urlencode username="$KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME" --data-urlencode password="$KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD" --data-urlencode scope='openid profile email' | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
}

# The bootstrap admin username and the generated password also work in a browser at the UI URL.
access_token="$(admin_token)"
onboard_response="$(curl -fsS -X POST http://127.0.0.1:8501/api/auth/onboard -H "Authorization: Bearer $access_token")"
printf '%s\n' "$onboard_response" | python3 -c 'import json,sys; body=json.load(sys.stdin); assert body["provisioned"] is True; print("admin workspace: " + body["tenant_slug"])'

# Re-mint after onboarding so the token contains the new tenant claim.
access_token="$(admin_token)"
pat_response="$(curl -fsS http://127.0.0.1:8501/mcp -H "Authorization: Bearer $access_token" -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"issue_personal_access_token","arguments":{"display_name":"local-agent"}},"id":"pat-request"}')"
agent_pat="$(printf '%s' "$pat_response" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["structuredContent"]["token_plaintext"])')"
case "$agent_pat" in
  co_pat_*) ;;
  *) printf '%s\n' 'agent token was not issued' >&2; exit 1 ;;
esac
export CONTACT_OPS_PAT="$agent_pat"
printf '%s\n' 'Agent access token issued and kept in CONTACT_OPS_PAT. The API returns the plaintext once.'
```

Open `http://127.0.0.1:3000/` and sign in as `admin` (`KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME`) with `KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD` from `.env`. The default `SIGNUP_ENABLED=false` keeps self-registration off; set it to `true` in `.env` before the first `up` if this local Keycloak realm should show its registration screen. No external account service is needed for this cell.

The API readiness URL is `http://127.0.0.1:8501/health/ready`. The local OIDC issuer is `http://contact-ops.localhost:8080/realms/contact-ops`; the browser resolves the `.localhost` hostname to loopback and the compose network gives Keycloak the same alias for server-side onboarding.

To inspect startup failures or stop the cell:

```bash
docker compose -f docker-compose.selfhost.yml logs --tail=100 contact-ops-backend
docker compose -f docker-compose.selfhost.yml down
```

### Run from source

For backend work, [`docker-compose.yml`](docker-compose.yml) publishes Postgres on port 5432 and Redis on port 6379. The API service in that file sets `STANDALONE_MODE=true` when Compose runs it. A process you start yourself needs the same switch. `contact_ops_dev` is the Postgres superuser in that file, so the RLS boot self-test warns and continues. That run does not prove row-level security.

```bash
docker compose up -d postgres redis

python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt

# These match docker-compose.yml, with 127.0.0.1 instead of the Compose DNS names.
export DATABASE_URL='postgresql+asyncpg://contact_ops_dev:dev@127.0.0.1:5432/contact_ops_db_dev'
export MIGRATION_DATABASE_URL='${CONTACT_OPS_DATABASE_URL}'
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
VITE_MCP_BASE_URL='http://127.0.0.1:8501' VITE_KEYCLOAK_ISSUER='<issuer-url>' VITE_KEYCLOAK_CLIENT_ID='contact-ops-app' npm run dev
```

Use the self-host compose path above when you need the complete Keycloak bootstrap and bundled data services. In `docker-compose.yml`, only the one-shot migration service mounts `backend/`. The API image is built, not a live source mount. The API, frontend, and Celery services join an external network named `unicorn-network`; `docker compose up -d postgres redis` does not.

### Configuration

Most of these variables are fields on `Settings` in [`backend/contact_ops/core/config.py`](backend/contact_ops/core/config.py). `KEYCLOAK_REALM` is read by the Keycloak bootstrap, not by `Settings`. `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` are read from the environment by the chat orchestrator. [`deploy/selfcontained/.env.selfcontained.template`](deploy/selfcontained/.env.selfcontained.template) is the contract for the bundled self-host cell. The root [`.env.example`](.env.example) is a shorter example and omits several of these settings. Values are intentionally omitted here.

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | Async PostgreSQL DSN for canonical application data. |
| `AUDIT_DATABASE_URL` | PostgreSQL DSN used for audit writes; can share the database in development. |
| `MIGRATION_DATABASE_URL` | Synchronous PostgreSQL DSN used by Alembic migrations and by `python -m contact_ops.ops.seed_rls_probes`. The admin role must be superuser or `BYPASSRLS`. |
| `RLS_SELFTEST_TENANT_IDS` | Exactly two strict tenant UUIDs, each with at least one person. The self-host template ships the ids the seed command creates. A non-privileged runtime role will not migrate an existing database until this is set. |
| `REDIS_URL` | Redis for cache and rate limits. The default database index is 5. The Celery broker is that URL rewritten to database 9, unless `CONTACT_OPS_AGENTS_BROKER_URL` is set. |
| `STANDALONE_MODE` | Development-only authentication bypass switch; keep it disabled for authenticated deployments. |
| `KEYCLOAK_ISSUER` | OIDC issuer used to validate browser and service JWTs. |
| `KEYCLOAK_REALM` | Realm name for the Keycloak bootstrap. The API reads the realm from `KEYCLOAK_ISSUER`, not from this variable. |
| `KEYCLOAK_AUDIENCE` | Expected audience for Contact-Ops access tokens. |
| `CONNECTOR_ENCRYPTION_KEY` / `CONNECTOR_ENCRYPTION_KEYS` | Fernet key or ordered key ring for encrypted connector credentials. |
| `M365_CLIENT_ID` / `M365_CLIENT_SECRET` / `M365_REDIRECT_URI` | Microsoft 365 OAuth application settings. |
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` / `GMAIL_REDIRECT_URI` | Google People OAuth application settings. |
| `FRONTEND_URL` | Browser origin used by backend redirects and connector callbacks. |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | Optional OpenAI-compatible chat endpoint, credential, and model for the AI Contacts Analyst. `LLM_MODEL` defaults to `gpt-4.1-mini`. |
| `QDRANT_URL` | Qdrant endpoint for voice-embedding workflows. |
| `FALKORDB_URL` | FalkorDB endpoint for the relationship graph. |
| `GARAGE_ENDPOINT` | S3-compatible Garage endpoint for media and export storage. |
| `BRIGADE_REGISTRY_URL` / `BRIGADE_SERVICE_TOKEN` | Optional Brigade registry URL and the bearer token used to register this service. |
| `BRIGADE_TRUSTED_ISSUER` / `BRIGADE_TRUSTED_ISSUERS` / `BRIGADE_JWKS_URL` / `BRIGADE_EXPECTED_AUDIENCE` | Optional checks for Brigade-issued federation JWTs. |
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
    workers["Celery worker + beat<br/>dev compose and overlays"]
    brigade["Brigade registry<br/>optional federation"]
    llm["OpenAI-compatible LLM<br/>optional Analyst"]
    sources["iCloud / Microsoft 365 / Google People"]
    carddav["CardDAV clients"]

    browser -->|OIDC| oidc
    browser -->|MCP + REST + SSE| api
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
```

The main components are:

- **Web UI** — React and TypeScript, built with Vite and served by nginx in the self-host image.
- **API and agent gateway** — FastAPI serves the REST API, streaming analyst chat, CardDAV, health probes, discovery, and the authenticated JSON-RPC MCP endpoint.
- **Canonical data layer** — Postgres with pgvector stores tenant-scoped people, organizations, contact points, proposals, memberships, and audit data under row-level security.
- **Supporting data services** — Redis handles cache and rate limits on database 5, and the Celery broker on database 9. FalkorDB holds the relationship projection. Qdrant supports voice embeddings. Garage provides S3-compatible media and export storage. Postgres also stores pgvector columns on people, organizations, photos, voice fingerprints, voice samples, and topics.
- **Identity and federation** — Keycloak supplies the bundled OIDC realm; optional Brigade registration and JWT federation connect the agent surface to the suite.
- **Connectors and workers** — source connectors pull iCloud, Microsoft 365, and Google People data. Celery worker and beat are defined in `docker-compose.yml` and in the production and self-contained overlays. `docker-compose.selfhost.yml` does not start them. The beat schedule includes the graph-sync drain, which writes the outbox into FalkorDB and is on by default (`GRAPH_SYNC_DRAIN_ENABLED`), the inbox snooze flipper, and the batch dedup, retention sweep, and calibration tasks. Those last three no-op while `DEDUP_AGENT_ENABLED`, `RETENTION_SWEEP_ENABLED`, and `CALIBRATION_ENABLED` are false. `RETENTION_SWEEP_SHADOW` defaults to true, so an enabled sweep logs the purge and does not delete. `CALIBRATION_SHADOW` defaults to true, so an enabled calibration updates drift observations and logs tier changes without changing the stored trust tier.

For a deeper implementation map, see [`ARCHITECTURE.md`](ARCHITECTURE.md) and [`INTEGRATION_GUIDE.md`](INTEGRATION_GUIDE.md).

## Part of Unicorn Commander

| App | What it does |
|---|---|
| [Accounting-Ops](https://github.com/Unicorn-Commander/Accounting-Ops) | Append-only, hash-chained double-entry books |
| **Contact-Ops** (this repository) | The shared contact and identity graph |
| [Customer-Ops](https://github.com/Unicorn-Commander/Customer-Ops) | Customer relationship and pipeline layer |
| [Email-Ops](https://github.com/Unicorn-Commander/Email-Ops) | Apache James JMAP mail surface and a human approval inbox |
| [Project-Ops](https://github.com/Unicorn-Commander/Project-Ops) | Projects and tasks: agreed work becomes owned tasks with status and context |
| [Meeting-Ops](https://github.com/Unicorn-Commander/Meeting-Ops) | Record meetings, transcribe in the browser, and search them |
| [Unicorn Stable](https://github.com/Unicorn-Commander/Unicorn-Stable) | Humans and agents in the same room |

Contact-Ops connects to the suite through scoped tenant identity, MCP discovery and calls, and optional Brigade registry registration and JWT federation. Imports and connector pulls create proposals that wait for review unless an import sets `auto_approve`. The confidence approver can auto-apply other proposals when they meet its gates.

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

Contact-Ops is licensed under the [Apache License 2.0](LICENSE). Our names and logos are covered by [TRADEMARKS.md](TRADEMARKS.md).

Built by [Magic Unicorn](https://magicunicorn.tech) · part of [Unicorn Commander](https://unicorncommander.ai)
