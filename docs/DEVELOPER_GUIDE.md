# Contact-Ops Developer Guide

**Date**: 2026-05-21
**Audience**: Engineers (human or agent) working on the Contact-Ops codebase itself.

For the canonical design doc see `/Users/aaronstransky/Documents/Contact-Ops-MCP-Design.md`.
For AI agent orientation see `CLAUDE.md`.
For ecosystem app integration see `INTEGRATION_GUIDE.md`.

---

## 1. Repo layout

```
Contact-Ops/
├── README.md
├── CLAUDE.md                    # AI agent orientation, read first
├── ARCHITECTURE.md              # System architecture summary
├── INTEGRATION_GUIDE.md         # For ecosystem app developers
├── docker-compose.yml           # Local dev (Postgres + Redis + Qdrant + backend)
├── docker-compose.prod.yml      # Production deploy on centerdeep
├── docker-compose.simple-prod.yml  # Cut-down prod variant (TODO: clarify or delete)
├── .env.example                 # Required env vars; no real secrets
├── .gitignore
├── .woodpecker/
│   └── pipeline.yml             # CI: lint, type, security, migrations, tests
├── docs/
│   ├── DEVELOPER_GUIDE.md       # This file
│   ├── USER_GUIDE.md            # End-user guide
│   └── decisions/               # ADRs (one per significant decision)
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt         # Pinned versions, no `>=`
│   ├── alembic.ini              # No hardcoded secrets; DB URL from env
│   ├── alembic/
│   │   ├── env.py
│   │   ├── versions/
│   │   │   ├── 0001_extensions_and_helpers.py
│   │   │   ├── 0002_tenants.py
│   │   │   ├── 0003_persons_and_orgs.py
│   │   │   ├── 0004_multi_cardinality.py    # emails, phones, addresses, identifiers, im_handles, urls
│   │   │   ├── 0005_media.py                 # media_assets, photos, voice_fingerprints, voice_samples
│   │   │   ├── 0006_relationships.py         # person_org_role, person_person_relation, org_org_relation
│   │   │   ├── 0007_facts_and_provenance.py
│   │   │   ├── 0008_interactions_topics.py
│   │   │   ├── 0009_tags_and_consent.py
│   │   │   ├── 0010_action_event.py          # append-only; revoke privileges
│   │   │   ├── 0011_merge_and_aliases.py
│   │   │   ├── 0012_agents.py
│   │   │   ├── 0013_data_intel_link.py
│   │   │   ├── 0014_graph_sync_outbox.py
│   │   │   ├── 0015_enable_rls_and_policies.py
│   │   │   └── 0016_seed_tenants.py
│   │   └── README.md
│   ├── contact_ops/
│   │   ├── __init__.py
│   │   ├── main.py                # FastAPI entrypoint
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py          # Settings (env-driven, Pydantic)
│   │   │   ├── database.py        # Async SQLAlchemy engines (app + audit)
│   │   │   ├── security.py        # Keep minimal; legacy paths quarantined
│   │   │   ├── rbac.py            # assertCan helpers
│   │   │   └── tenant_context.py  # Sets app.tenant_id GUC per session
│   │   ├── mcp/
│   │   │   ├── __init__.py
│   │   │   ├── server.py          # JSON-RPC server + tool registry
│   │   │   ├── annotations.py     # readOnlyHint, destructiveHint, etc.
│   │   │   └── tools/             # One module per tool, Phase 1+
│   │   ├── middleware/
│   │   │   ├── __init__.py
│   │   │   ├── jwt_validation.py  # OAuth 2.1 + RFC 8707, fail-CLOSED
│   │   │   ├── audit.py           # Writes action_event via contact_ops_audit role
│   │   │   └── tenant_context.py  # Reads tenant_id from JWT claim
│   │   ├── models/                # SQLAlchemy ORM models; one Base
│   │   │   ├── __init__.py        # exports Base + all models
│   │   │   ├── enums.py           # Python enums bound to PG ENUMs
│   │   │   ├── tenant.py
│   │   │   ├── person.py
│   │   │   ├── organization.py
│   │   │   ├── action_event.py
│   │   │   └── ...                # one per table; Phase 0 lands ~10
│   │   ├── routers/               # REST routers (auto-generated from MCP in Phase 1+)
│   │   │   ├── __init__.py
│   │   │   └── health.py
│   │   ├── schemas/               # Pydantic v2 schemas
│   │   └── services/              # Domain services; no MCP tools yet
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py            # Real Postgres testcontainer + mocked JWKS
│       ├── test_migrations.py
│       ├── test_action_event_append_only.py  # SET ROLE contact_ops_app + UPDATE -> expect deny
│       ├── test_hipaa_fence.py
│       ├── test_rls_isolation.py
│       ├── test_alias_rewrite.py
│       ├── test_jwt_middleware.py
│       └── test_mcp_handshake.py
├── frontend/                      # Next.js (Phase 1+)
└── ops-center-integration/        # Legacy: Ops-Center settings page; TBD relocate
```

---

## 2. Local development

### 2.1 Prerequisites

- Docker + Docker Compose v2.
- Python 3.12 (or use the backend container).
- `unicorn-network` Docker network must exist:
  ```bash
  docker network create unicorn-network || true
  ```

### 2.2 Bring up the stack

```bash
cd /Volumes/Studio\ Storage/Development/Contact-Ops/

# 1. Copy env template + fill in local values
cp .env.example .env
$EDITOR .env

# 2. Bring up Postgres, Redis, Qdrant, FalkorDB, and the backend
docker compose up -d

# 3. Wait for services to be healthy
docker compose ps

# 4. Run migrations
docker compose exec contact-ops-backend alembic upgrade head

# 5. Run tests
docker compose exec contact-ops-backend pytest -v

# 6. Tail logs
docker compose logs -f contact-ops-backend
```

Default ports:
| Service | Port |
|---------|------|
| contact-ops-backend (FastAPI + MCP) | 8501 |
| contact-ops-frontend (Next.js) | 8502 |
| OAuth2 proxy (prod only) | 8503 |

The MCP endpoint is at `http://localhost:8501/mcp` in dev, `https://mcp.contacts.magicunicorn.dev/mcp` in prod.

### 2.3 Database access

```bash
# Connect to Postgres via the unicorn-postgresql container
docker exec -it unicorn-postgresql psql -U unicorn -d contact_ops_db

# Connect as the app role (for RLS testing)
docker exec -it unicorn-postgresql psql -U unicorn -d contact_ops_db
SET ROLE contact_ops_app;
SELECT set_config('app.tenant_id', '<a-tenant-uuid>', false);
SELECT count(*) FROM persons;  -- now RLS-filtered
```

### 2.4 FalkorDB console

```bash
# FalkorDB speaks Redis protocol
docker exec -it contactops-falkordb redis-cli
> GRAPH.QUERY contact_ops__aaron-personal "MATCH (p:Person) RETURN p.display_name LIMIT 10"
```

Per Aaron's `feedback_brigade_canonical_agent_runtime.md` and the Brigade/Meeting-Ops pre-commit linter: **never write f-string Cypher**. Use `GRAPH.QUERY <graph> "<cypher>" PARAMS {...}` with parameter substitution.

### 2.5 Qdrant console

```bash
# Qdrant has a UI at http://localhost:6333/dashboard in dev
open http://localhost:6333/dashboard

# Or curl:
curl http://localhost:6333/collections | jq
```

---

## 3. Migration workflow

### 3.1 Running migrations

```bash
# Apply all pending migrations
docker compose exec contact-ops-backend alembic upgrade head

# Apply one migration at a time
docker compose exec contact-ops-backend alembic upgrade +1

# Roll back one
docker compose exec contact-ops-backend alembic downgrade -1

# Show current revision
docker compose exec contact-ops-backend alembic current

# Show full history
docker compose exec contact-ops-backend alembic history
```

### 3.2 Writing a new migration

```bash
# Generate a stub
docker compose exec contact-ops-backend alembic revision -m "add_my_table"

# Edit the generated file in backend/alembic/versions/
# Use op.execute() with raw SQL for now (autogenerate is disabled until
# ORM models cover all tables)
```

Rules:
- **Idempotent**: use `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, etc.
- **Reversible**: write the matching `down_revision` logic. Even if you never roll back in production, a working `downgrade` lets test suites tear down cleanly.
- **No data corrections in schema migrations**: if you need to backfill data, do it in a separate migration that runs after the schema change lands and is reviewed independently.
- **No real secrets**: migrations are public.

### 3.3 RLS migration pattern

When you add a new tenant-scoped table:

```sql
-- 1. Create the table with canonical_owner_tenant_id or tenant_id (or join key)
CREATE TABLE my_new_table (...);

-- 2. Enable AND force RLS
ALTER TABLE my_new_table ENABLE ROW LEVEL SECURITY;
ALTER TABLE my_new_table FORCE ROW LEVEL SECURITY;

-- 3. Add the policy
CREATE POLICY my_new_table_select ON my_new_table FOR SELECT TO contact_ops_app
  USING (tenant_id = current_tenant_id());

CREATE POLICY my_new_table_modify ON my_new_table FOR UPDATE TO contact_ops_app
  USING (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

CREATE POLICY my_new_table_insert ON my_new_table FOR INSERT TO contact_ops_app
  WITH CHECK (tenant_id = current_tenant_id());

-- 4. GRANT to the app role
GRANT SELECT, INSERT, UPDATE, DELETE ON my_new_table TO contact_ops_app;
GRANT USAGE, SELECT ON SEQUENCE my_new_table_id_seq TO contact_ops_app;
```

Always test RLS by connecting as `contact_ops_app` and confirming you can't read another tenant's rows.

---

## 4. Adding a new MCP tool

Phase 0 has zero tools. Phase 1 adds them in batches. The pattern:

### 4.1 Where it lives

```
backend/contact_ops/mcp/tools/
├── __init__.py        # imports all tools, registers with the server
├── people/
│   ├── __init__.py
│   ├── create_person.py
│   ├── upsert_person.py
│   ├── get_person.py
│   └── ...
├── orgs/
├── employment/
├── identifiers/
├── ...
```

### 4.2 Tool module shape

```python
# backend/contact_ops/mcp/tools/people/get_person.py
from typing import Annotated
from pydantic import BaseModel, Field
from contact_ops.mcp.annotations import tool
from contact_ops.mcp.server import register_tool


class GetPersonInput(BaseModel):
    person_id: Annotated[str, Field(pattern=r"^[0-9a-f-]{36}$")]
    include: list[
        Literal["emails", "phones", "addresses", "current_employments", ...]
    ] = []
    as_of: str | None = None  # ISO8601 timestamp


class GetPersonOutput(BaseModel):
    person_id: str
    etag: str
    display_name: str
    kind: str
    status: Literal["active", "archived", "merged"]
    merged_into_id: str | None = None
    # ...
    # Each include adds its keyed sub-array.


@tool(
    name="get_person",
    description="Fetches a single person record by system ID.",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    required_role="CLIENT",
    required_scopes=["person:read"],
)
async def get_person(
    args: GetPersonInput,
    ctx: ToolContext,
) -> GetPersonOutput:
    # 1. Verify ctx.tenant_id is set (from JWT claim).
    # 2. Set Postgres GUC: SELECT set_config('app.tenant_id', ctx.tenant_id, true)
    # 3. Query persons + include expansions.
    # 4. Update person_tenant_membership.last_accessed_at.
    # 5. If tenant_is_hipaa, write read_access_logged action_event.
    # 6. Return canonical record.
    ...


register_tool(get_person)
```

### 4.3 Conventions

- **One file per tool.** Big tools (merge_persons) can spread across sibling files but the top-level entry is one module.
- **Pydantic v2 input/output models.** Inputs validated at the boundary. Outputs typed to support introspection / docs.
- **`@tool` decorator** wires up annotations + required role + required scopes + auto-registers.
- **ctx.tenant_id** is read from the JWT claim by middleware; tools never accept a `tenant_id` argument.
- **Confidence**: mutations accept `confidence: 0-1` (default 1.0 for humans). Tools that auto-apply/propose use a shared helper `apply_or_propose(event, threshold)`.
- **Idempotency**: every `create_*` / `bulk_*` tool accepts `idempotency_key: UUID`. Helper `await idempotency_check(key, ttl=24h)`.
- **Etag**: every PATCH tool requires the current etag. Helper `await check_etag(entity_id, supplied_etag)`.
- **action_event**: every mutation writes one via the audit middleware. The middleware reads the OBO actor chain from the JWT and writes via the `contact_ops_audit` role.
- **field_provenance**: mutations that touch materialized fields also update `field_provenance` (in the same transaction).
- **graph_sync_outbox**: mutations that affect graph nodes write to the outbox (in the same transaction).

### 4.4 Testing a new tool

```python
# backend/tests/test_people_get_person.py
import pytest
from contact_ops.mcp.tools.people.get_person import get_person, GetPersonInput

@pytest.mark.asyncio
async def test_get_person_returns_canonical(real_postgres, seeded_tenant, seeded_person):
    args = GetPersonInput(
        person_id=seeded_person.id,
        include=["emails", "current_employments"],
    )
    ctx = make_ctx(tenant_id=seeded_tenant.id, role="STAFF", scopes=["person:read"])
    result = await get_person(args, ctx)
    assert result.display_name == "Jane Doe"
    assert result.etag == seeded_person.etag

@pytest.mark.asyncio
async def test_get_person_rls_denies_other_tenant(real_postgres, two_tenants_two_persons):
    args = GetPersonInput(person_id=two_tenants_two_persons.person_b.id)
    ctx = make_ctx(tenant_id=two_tenants_two_persons.tenant_a.id, role="STAFF", scopes=["person:read"])
    with pytest.raises(PersonNotFoundError):  # RLS filters it out
        await get_person(args, ctx)
```

Every tool ships with at least:
1. Happy path test.
2. RLS isolation test (other tenant can't see).
3. Permission test (missing scope returns FORBIDDEN_SCOPE).
4. (For mutations) idempotency test (replay returns same result).
5. (For PATCH) etag test (stale etag returns STALE_ETAG).

---

## 5. Adding a new agent

Per the design doc §6 pattern and Aaron's `feedback_agent_scoping_pattern.md` memory.

### 5.1 Where it lives

```
backend/contact_ops/agents/
├── __init__.py
├── base.py            # Agent base class, registration, trust tier helpers
├── dedup_agent.py
├── enrichment_agent.py
├── voice_match_agent.py
└── ...
```

### 5.2 Agent shape

```python
# backend/contact_ops/agents/dedup_agent.py
from contact_ops.agents.base import Agent, register_agent
from contact_ops.models.enums import ScopeMode, VisibilityScope, TrustTier

class DedupAgent(Agent):
    name = "dedup_agent"
    version = "1.0.0"
    owning_system = "contact-ops"
    scope_mode = ScopeMode.SHARED
    visibility = VisibilityScope.SHARED
    declared_capabilities = [
        "dedup_find_candidates",
        "dedup_score",
        "dedup_propose_merge",
        "field_propose_set",
    ]
    initial_trust_tier = TrustTier.PROPOSE_ONLY
    oauth_scopes = [
        "contact-ops.person:read",
        "contact-ops.person:propose_merge",
        "contact-ops.identifier:read",
        "contact-ops.embedding:read",
    ]
    # Per-event-type trust tier overrides; legal/family events stay propose_only forever
    trust_tier_overrides = {
        "person.proposed_merge:cross_tenant": TrustTier.PROPOSE_ONLY,
    }

    async def handle(self, event: AgentEvent) -> list[ActionEvent]:
        # Read inputs (persons, identifiers, embeddings).
        # Score candidates.
        # Emit one or more proposed action_events.
        ...

register_agent(DedupAgent)
```

### 5.3 Calibration loop

Every agent starts at `propose_only` for the first 100 actions. The Calibration Daemon runs nightly and:

1. Reads `agent_calibration` deltas (proposed / applied / approved / rejected / reverted counts).
2. Computes Brier score + ECE per event_type.
3. Proposes promotion to `auto_apply_low` if revert rate <2-3% on 50 approvals for that event_type.
4. Promotions above `auto_apply_low` require human approval flag on the promotion record.
5. Demotion happens automatically: >5% recent revert rate -> back to `propose_only`.

The daemon writes proposed promotions to `agent_registry_history`, not `action_event` (it's infra-level configuration with its own audit table).

### 5.4 Forever-propose-only rules (non-negotiable)

Per Aaron's `feedback_confidence_tags_legal_work.md`:
- Legal relations: `counsel_for`, `client_of_counsel`, `witness_for`, `party_to`, `opposing_party_to`, `expert_for`, etc.
- Family relations: `parent_of`, `child_of`, `spouse_of`, `sibling_of`, all family edges.
- Status: `is_deceased`, `death_date`.

These are enforced by the Calibration Daemon's promotion ceiling. Even an agent with `current_trust_tier = authoritative` cannot auto-apply these event_types.

---

## 6. Adding a new tenant

### 6.1 Manual SQL (Phase 0 / 1)

```sql
-- Connect as superuser (migration role)
\c contact_ops_db

INSERT INTO tenants (
    slug, kind, display_name, owner_user_id,
    qdrant_namespace, garage_bucket_prefix, graph_mode, graph_name,
    branding, retention_policy
) VALUES (
    'new-tenant-slug',
    'magic_unicorn_internal',
    'New Tenant Display Name',
    '<uc_uid of owner>',
    'contact_ops__new-tenant-slug',
    'contact-ops-new-tenant-slug',
    'per_org_graph',
    'contact_ops__new-tenant-slug',
    '{}'::jsonb,
    '{"default":"operational_2y"}'::jsonb
);

-- Provision the FalkorDB graph (constraints + indexes)
-- See design doc §4.2 for the bootstrap script.

-- Provision the Garage bucket prefixes (or let lazy creation handle on first PUT).

-- Provision Qdrant payload-filter shard (one-time per tenant; namespace is just a filter).
```

### 6.2 Admin tool (Phase 1+)

The `provision_tenant` MCP tool (admin role only) wraps all of the above:

```python
await mcp_call(
    access_token=admin_token,
    tool="provision_tenant",
    arguments={
        "slug": "new-tenant-slug",
        "kind": "magic_unicorn_internal",
        "display_name": "New Tenant Display Name",
        "owner_user_id": "<uc_uid>",
        "graph_mode": "per_org_graph",
        "hipaa_mode": False,
        "data_intel_publish_consent": False,
    },
)
```

### 6.3 White-label customer (Phase 6)

Self-serve signup at `contacts.magicunicorn.dev`. Customer registers a DNS CNAME -> `contacts.magicunicorn.dev`, Aaron approves via admin UI, Traefik picks up the host header, branding kicks in.

---

## 7. Debugging tips

### 7.1 Structured logs

Every log line is JSON. Filter by trace_id:

```bash
docker compose logs contact-ops-backend | jq 'select(.trace_id == "01985-...")'
```

### 7.2 action_event timeline

For "why does this record look weird":

```sql
-- All events for a person, newest first
SELECT event_id, event_type, status, actor, evidence->'sources', proposed_at, applied_at
FROM action_event
WHERE aggregate_type = 'person' AND aggregate_id = '<person_id>'
ORDER BY proposed_at DESC
LIMIT 50;

-- Specifically the chain of supersedences
WITH RECURSIVE chain AS (
  SELECT * FROM action_event WHERE event_id = '<latest_event_id>'
  UNION ALL
  SELECT ae.* FROM action_event ae JOIN chain c ON ae.event_id = c.supersedes_event_id
)
SELECT event_type, status, actor->'sub', applied_at FROM chain ORDER BY proposed_at;
```

### 7.3 field_provenance

For "who set this field":

```sql
SELECT field_path, current_value, set_by_actor, source, confidence, established_at, last_verified_at
FROM field_provenance
WHERE entity_type = 'person' AND entity_id = '<person_id>'
ORDER BY field_path;
```

Or via MCP:

```python
await mcp_call(access_token, "get_field_provenance", {
    "entity_type": "person",
    "entity_id": "<person_id>",
})
```

### 7.4 Cypher console for graph queries

```bash
docker exec -it contactops-falkordb redis-cli

# Who does Aaron know directly?
> GRAPH.QUERY contact_ops__aaron-personal \
    "MATCH (a:Person {id: $aaron_id})-[:KNOWS]->(o) RETURN o.display_name LIMIT 20" \
    PARAMS {aaron_id: '<aaron's person_id>'}

# Shortest path between two persons
> GRAPH.QUERY contact_ops__aaron-personal \
    "MATCH (a:Person {id: $a_id}), (b:Person {id: $b_id}), p = shortestPath((a)-[*..6]-(b)) RETURN p" \
    PARAMS {a_id: '...', b_id: '...'}
```

### 7.5 Common gotchas

- **Empty result set when you expect data**: check that `app.tenant_id` GUC is set. RLS silently filters.
- **`permission denied` on UPDATE action_event**: that's correct. Append-only. Write a superseding event instead.
- **STALE_ETAG on PATCH**: someone (or some agent) updated the row between your read and write. Re-read, retry.
- **`relation "X" does not exist`**: you forgot to run `alembic upgrade head` after pulling.
- **Tests pass locally but fail in CI**: check that `asyncio_mode = auto` is set in `pyproject.toml` (the Phase 0 review caught this).

---

## 8. Running CI locally before push

The Woodpecker pipeline runs the same suite in CI. Run it locally before pushing:

```bash
# Lint
docker compose exec contact-ops-backend ruff check .
docker compose exec contact-ops-backend black --check .

# Type check (strict)
docker compose exec contact-ops-backend mypy --strict contact_ops/

# Security scan
docker compose exec contact-ops-backend bandit -r contact_ops/
docker compose exec contact-ops-backend safety check

# Migrations against a fresh DB
docker compose exec contact-ops-backend alembic upgrade head

# Full test suite
docker compose exec contact-ops-backend pytest -v --cov=contact_ops --cov-fail-under=80
```

All five must pass before push.

---

## 9. Coding standards

Repeated from `CLAUDE.md` because they're load-bearing:

### 9.1 ENUM discipline
- Every PG ENUM has a paired Python `enum.Enum` subclass in `models/enums.py`.
- ORM columns: `mapped_column(Enum(MyEnum, name="my_enum", create_type=False))`. Never `mapped_column(Text)`.

### 9.2 Single Base
- One `declarative_base()` in `models/__init__.py`. Every model imports `from contact_ops.models import Base`. No second Base in `core/database.py`.

### 9.3 Tenant resolution
- From the JWT claim, never from a tool argument or HTTP header.
- The DB dependency sets `app.tenant_id` GUC per session.

### 9.4 Append-only `action_event`
- UPDATE/DELETE revoked from `contact_ops_app`.
- Audit middleware connects as `contact_ops_audit` via a separate engine.
- Status changes are new events with `supersedes_event_id`.

### 9.5 Parameterized SQL / Cypher only
- No f-string SQL anywhere.
- No f-string Cypher anywhere. Use `PARAMS {...}`.
- Pre-commit linter (planned, mirrors the Brigade pattern) will catch f-string templates.

### 9.6 Pinned dependencies
- `requirements.txt` uses `==X.Y.Z`, never `>=`.
- Lockfile committed.
- `greenlet`, `qdrant-client`, `python-jose` (or `jwcrypto`) explicit.

### 9.7 No real secrets
- `.env` is in `.gitignore`.
- `.env.example` shows required keys with placeholder values.
- `alembic.ini` reads DB URL from env, never hardcoded.
- If you find a secret in a committed file: rotate the secret, scrub with `git filter-repo`, then carry on.

### 9.8 No em-dashes
- Aaron's writing style. Use commas, semicolons, or two hyphens (`--`).

### 9.9 Verify execution
- Don't ship code you haven't seen work end-to-end.
- `alembic upgrade head` + `pytest` against a real Postgres before every PR.

---

## 10. Where to ask questions

- **Open questions for Aaron**: append to `/Users/aaronstransky/Documents/Contact-Ops-Open-Questions.md`.
- **Design questions**: re-read the design doc at `/Users/aaronstransky/Documents/Contact-Ops-MCP-Design.md`.
- **Memory notes**: check `/Users/aaronstransky/.claude/projects/-Users-aaronstransky/memory/` for the relevant Aaron note (see CLAUDE.md table).
- **Status reports**: write to `/Users/aaronstransky/Documents/Contact-Ops-Track-<X>-<topic>-Report.md` when you finish a track.
- **ADRs**: significant decisions go in `docs/decisions/NNNN-<slug>.md`.
