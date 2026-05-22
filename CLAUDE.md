# CLAUDE.md: Contact-Ops AI agent orientation

**Date**: 2026-05-21
**Status**: Phase 0 (scaffold)
**Read this first.** Every Claude / Cursor / opencode / Codex agent that opens this repo should treat this file as the canonical map of what Contact-Ops is, what the quality bar is, and what NOT to do.

---

## What Contact-Ops is

Contact-Ops is an **agent-first, MCP-native canonical contact + organization registry**. The MCP server IS the product. Human UI, REST API, CardDAV, and the 3D graph viewer are all downstream consumers of the same MCP tool surface. There is no privileged path. If a human in the UI can do it, an agent with the right scopes can do it. If an agent can do it, a human can audit, approve, edit, or revert it via the same surface.

Contact-Ops is **federated with Data Intel** (`verify.centerdeep.online`). Contact-Ops manages contacts you actively curate. Data Intel is the passive verification / B2B intelligence DB. Same infrastructure, separate codebases, separate databases. RFC 8693 token-exchange OBO connects them. Neither owns the other.

The full design lives at `/Users/aaronstransky/Documents/Contact-Ops-MCP-Design.md` (5,685 lines). **It is the source of truth for every architectural fact.** When this file or any sibling doc disagrees with the design doc, the design doc wins. When the design doc itself has open questions, they go in `/Users/aaronstransky/Documents/Contact-Ops-Open-Questions.md` for Aaron to resolve.

The original brief (`/Users/aaronstransky/Documents/Contact-Ops-Build-Brief.md`) and the Codex build prompt (`/Users/aaronstransky/Documents/Contact-Ops-Codex-Prompt.md`) are historical context only. Treat the MCP-Design doc as current truth.

---

## The quality bar

Aaron's standing memory (`feedback_quality_standard.md`): **no MVP work, everything top-tier**. Applied to Contact-Ops:

- Every agent ships with calibration histograms in Grafana from day one.
- Every mutation writes `action_event` from day one.
- Every read of a tenant-scoped row checks RLS from day one.
- pgvector hnsw + Qdrant ANN indexes from day one.
- Optimistic concurrency etags from day one.
- Bulk operations with batch events from day one.
- Confidence-driven propose-vs-apply from day one.
- HIPAA fencing (tenant flag + RLS policy + merge trigger) from day one, even if no HIPAA tenant exists yet.
- 90-day unmerge from day one.
- Field-level provenance from day one.

**Verify execution after every change.** Do not deliver "looks right on skim" code. The Phase 0 build was rejected because none of it was actually run end-to-end before delivery. Migration syntax errors prevented Alembic from even importing the chain; audit middleware wrote to a non-existent table; tests referenced columns that don't exist. None of that would have survived a single `alembic upgrade head` + `pytest` run. **Run the thing. Read the output. Fix the failures. Repeat until clean.**

---

## Tech stack

| Layer | Tech | Notes |
|-------|------|-------|
| Web framework | FastAPI | Async, OAuth 2.1 + RFC 8707 resource indicators, RFC 8693 token-exchange for OBO |
| ORM | SQLAlchemy 2.0 (async) | `asyncpg` driver, declarative `Mapped` columns, real Python enums bound to PG ENUMs (never `Text`) |
| Validation | Pydantic v2 | Schema-validated everywhere; JSON-only inputs at the MCP boundary |
| DB | Postgres 16 + pgvector + pg_trgm + pg_uuidv7 + citext + unaccent + btree_gin + btree_gist | One DB per service: `contact_ops_db`. Separate from `dataintel_db`. Both on `unicorn-postgresql` on centerdeep. |
| Graph | FalkorDB v4+ | New instance `contactops-falkordb` on centerdeep. DR mirror on bigboy's `unicorn-falkordb`. |
| Vectors | Qdrant | Existing `unicorn-qdrant` on centerdeep, six new `contact_ops_*` collections |
| Object storage | Garage | Existing `unicorn-garage` on bigboy. Per-tenant bucket prefix `contact-ops-<slug>-*`. SSE-KMS via `tenant_keys.kms_key_arn`. |
| Auth | Keycloak `uchub` realm | On commander VPS. Two-instance ecosystem federated via OIDC broker (see `~/.claude/projects/-Users-aaronstransky/memory/reference_keycloak_topology.md`). |
| Logging | structlog | JSON; trace_id propagation through OBO chain |
| HTTP client | httpx (async) | |
| Migrations | Alembic | Migrations 0001-0015 land in Phase 0. Hand-managed SQL via `op.execute`; autogenerate disabled for now. |
| CI | Woodpecker | `.woodpecker/pipeline.yml`. Postgres service, mypy `--strict`, pytest against real DB. |
| Container | Docker / Docker Compose | `docker-compose.yml` for local dev, `docker-compose.prod.yml` for centerdeep deploy |
| Forgejo | `git.unicorncommander.ai` | Remote configured. Nothing pushed yet, wait for Track A + B + C green. |

---

## Repo layout

```
Contact-Ops/
├── README.md                    # Top-level entry point
├── CLAUDE.md                    # This file
├── ARCHITECTURE.md              # System architecture (navigable summary of the design doc)
├── INTEGRATION_GUIDE.md         # For ecosystem app developers
├── docker-compose.yml           # Local dev (Postgres, Redis, Qdrant on unicorn-network)
├── docker-compose.prod.yml      # Production deploy on centerdeep
├── .env.example                 # No real secrets. Required env vars listed.
├── docs/
│   ├── DEVELOPER_GUIDE.md       # Day-to-day developer workflow
│   ├── USER_GUIDE.md            # End-user guide (sparse in Phase 0)
│   └── decisions/               # ADRs (one per significant decision, dated)
├── backend/
│   ├── alembic/                 # Migrations 0001-0015 (Phase 0 set)
│   ├── alembic.ini              # No hardcoded secrets. DB URL from env.
│   ├── contact_ops/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI app entrypoint
│   │   ├── core/                # config, database, security, RBAC, helpers
│   │   ├── mcp/                 # MCP server (JSON-RPC + tool registry)
│   │   ├── middleware/          # JWT validation, audit, tenant context
│   │   ├── models/              # SQLAlchemy ORM models (one Base, imported everywhere)
│   │   ├── routers/             # REST routers (auto-generated from MCP tools in Phase 1+)
│   │   ├── schemas/             # Pydantic schemas
│   │   └── services/            # Domain services (no tools yet in Phase 0)
│   ├── tests/                   # pytest, asyncio_mode = auto
│   └── requirements.txt         # Pinned versions, no `>=`
├── frontend/                    # Next.js human UI (Phase 1+)
└── .woodpecker/
    └── pipeline.yml             # CI
```

---

## Core conventions

Read these once, internalize them, never deviate.

### MCP tool conventions
- **snake_case verb_noun** for tool names: `create_person`, `upsert_org`, `link_relationship`, `find_person_by_identifier`. Verbs first: `get_*`, `list_*`, `search_*`, `create_*`, `update_*`, `archive_*`, `delete_*`, `link_*`, `unlink_*`, `bulk_*`, `merge_*`, `unmerge_*`, `revert_action`.
- **Idempotency on every mutation.** Every `create_*` and `bulk_*` tool accepts an optional `idempotency_key: UUID`. Replays within 24h return the original result. Natural-key uniqueness also dedupes silently.
- **Etag on every PATCH.** Every `update_*` tool requires the current `etag` returned by the prior read. Lost-update races between humans and agents return `STALE_ETAG`, never silently overwrite.
- **Confidence as a first-class input.** Every agent-callable mutation accepts `confidence: 0-1` (default 1.0 for humans). Below the per-tenant per-event-type threshold, the tool returns `{status: "proposed", proposal_id}`. Above, returns `{status: "applied", event_id}`.
- **Bulk operations have batch events.** Every `bulk_*` tool writes one `action_event` per item PLUS a `batch_event` parent. Whole batch reverts via `revert_action(batch_event_id)`.
- **Cursor pagination.** Default `limit` 25, max 100. Opaque `next_cursor` string. Optional `total_count` only when cheap.
- **Tool annotations.** Every tool exposes MCP annotations: `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`. Agent frameworks use these to gate destructive calls.
- **Structured error envelope.** `{isError: true, code, message, retryable, hint?, retry_after_ms?, details?}`. Common codes (`UNAUTHORIZED`, `FORBIDDEN_ROLE`, `FORBIDDEN_SCOPE`, `STALE_ETAG`, `RATE_LIMITED`, `VALIDATION_ERROR`, `INTERNAL`) are global; tool-specific codes are documented per tool.

### Tenant resolution
- **Tenant is read from the JWT claim, never from a tool argument.** The `X-Tenant-Id` HTTP header is untrusted. Any tool that takes a `tenant_id` parameter is buggy (the exception is admin cross-tenant tools, which use `scope` or `include_tenants` arrays, never a single `tenant_id`).
- The JWT middleware reads `tenant_id` from the token's `tenant_id` claim (Keycloak mapper). It sets `request.state.jwt_claims["tenant_id"]`, and the database dependency sets the Postgres GUC `app.tenant_id` per session via `SELECT set_config('app.tenant_id', :t, true)`. RLS policies then use `current_tenant_id()`.

### Append-only audit
- The `action_event` table is **append-only at the Postgres role level**. UPDATE and DELETE are revoked from `contact_ops_app`. The audit middleware writes via a separate connection bound to the `contact_ops_audit` role.
- Status changes are recorded as new events that supersede prior ones via `supersedes_event_id`. Never UPDATE an existing event. Reverts write compensating events via `revert_action`.
- Every event includes `actor` JSONB with the full OBO actor chain: `{sub: user, act: {sub: agent, act: {sub: source-system}}}`. `human_authority` is the denormalized root user for fast inbox filtering.
- Every event includes `evidence` JSONB: `{sources[], trace_id, prompt_hash, model, tool_calls[]}`. Humans see this on hover before approving.
- Every event has `content_hash` (SHA-256 of payload) and `prev_event_hash` (link to prior event in the chain).

### RLS on every tenant-scoped table
- Tables that carry their own `tenant_id` or `canonical_owner_tenant_id` (persons, organizations, action_event, interactions, facts, ...) get a direct policy: `USING (canonical_owner_tenant_id = current_tenant_id())`.
- Tables that hang off persons/orgs without their own tenant column (emails, phones, postal_addresses, identifiers, im_handles, urls) get a policy that joins through the parent: `USING (EXISTS (SELECT 1 FROM persons p WHERE p.id = emails.person_id AND p.canonical_owner_tenant_id = current_tenant_id()))`.
- Tables that carry `tenant_visibility` (person_person_relation, org_org_relation) use that column directly.
- **Always `FORCE ROW LEVEL SECURITY`.** Plain `ENABLE` does not apply to the table owner. The migration superuser is the owner, so without FORCE, the app user (which uses the same role today, fix planned) silently bypasses every policy.

### ENUM discipline
- Every PG ENUM has a paired Python `enum.Enum` subclass in `models/`.
- ORM columns bind via `mapped_column(Enum(MyEnum, name="my_enum", create_type=False))`, **never** `mapped_column(Text)`.
- The design doc explicitly forbids ENUM-as-Text. Constraint violations surface as obscure errors and Alembic autogenerate will continually try to "fix" the mismatch.

### Parameterized SQL / Cypher only
- No f-string SQL. No f-string Cypher. Ever. The same rule that Brigade enforces with a pre-commit grep linter applies here.
- Use SQLAlchemy text parameters, asyncpg `$1, $2` placeholders, or FalkorDB `GRAPH.QUERY <graph> "<cypher>" PARAMS {...}`.
- Cypher specifically: FalkorDB does not support `CALL{}` or `EXISTS{}` subqueries. Stick to MERGE / MATCH / WHERE / RETURN.

### Provenance
- Every materialized field on every person/org has a row in `field_provenance` recording which `action_event` set it, which actor chain, which source, when, with what confidence. The `history` JSONB array preserves all prior values.
- `get_field_provenance(entity_id, field_path)` is a first-class MCP tool. "Click any field, see why" is a platform promise, not a nice-to-have.

### Reversibility
- **No hard deletes by default.** Every `delete_*` is a 90-day tombstone with audit retention. GDPR Article 17 erasure is a separate glass-break tool.
- **Bitemporal validity windows.** Most relationship tables carry `valid_from` / `valid_until`. "What did we know about X on 2024-06-12" is a real query.
- **90-day unmerge.** Every merge is reversible for 90 days via `unmerge`, restoring the loser and redistributing fields per `field_provenance_map`.
- **Per-tenant retention class** (`ephemeral_30d`, `operational_2y`, `indefinite`, `hipaa_6y`, `legal_hold`) drives lifecycle daemons.

### Confidence tiers (Aaron-approved defaults)
- `≥ 0.95` auto-apply
- `0.75 - 0.95` surface in approval inbox
- `0.50 - 0.75` proposed-only, lower priority
- `< 0.50` discard

Configurable per-tenant and per-event-type via `tenants.retention_policy.auto_apply_thresholds`. Legal-class relationships (`counsel_for`, `witness_for`, `party_to`, `family_of`) and `is_deceased` are **forever** `propose_only`, regardless of confidence. Aaron's `feedback_confidence_tags_legal_work.md` memory is the rule.

---

## Phase plan (high level)

Full plan in design doc §9. Summary:

| Phase | Goal |
|-------|------|
| **0 (now)** | Fork from centerdeep-data-intel. Rename `app/` → `contact_ops/`. Add migrations 0001-0015. Land MCP server scaffold that answers JSON-RPC handshake (no tools registered yet). Set up Keycloak OIDC clients (`contact-ops-app`, `contact-ops-mcp`, `contact-ops-carddav`, `contact-ops-publisher`, `contact-ops-bridge-inbound`). DNS + Traefik routing for `contacts.magicunicorn.dev`, `mcp.contacts.magicunicorn.dev`, `carddav.contacts.magicunicorn.dev`. |
| **1** | Core MCP tools for People, Orgs, Employment, Identifiers, Emails, Phones, Addresses, Tags, Search. Confidence-driven propose-vs-apply baseline. action_event writes. field_provenance projection. |
| **2** | Tenancy + Visibility/ACL + CardDAV + Photos. iOS/macOS Contacts sync end-to-end. HIPAA mode enforced at all three layers. |
| **3** | Agent fleet: Dedup, Enrichment, Voice Match, Tag, Lifecycle + Approval Inbox UI. Calibration loop. |
| **4** | FalkorDB graph sync + 3D viewer (react-force-graph-3d) + Relationship tools. |
| **5** | Ecosystem migrations: Listing-Ops → Crisis-Ops → Project-Ops → Meeting-Ops → Stable → Brigade. |
| **6** | White-label productization (vanity domains, self-serve tenant signup, billing hooks). |

---

## Federation with Data Intel

Contact-Ops and Data Intel are peers, not parent/child. The LinkedIn-and-Apollo pattern: Contact-Ops is the active management layer (your contacts, your tags, your tenants, your private relationships), Data Intel is the passive intelligence DB (B2B catalogue, SMTP verify, public-record enrichment). A CRM (if Aaron ever needs one) is a third, separate downstream consumer (CRM-Ops, not a feature of Contact-Ops).

| Direction | Trigger | Payload | Behavior |
|-----------|---------|---------|----------|
| **Outbound** (Contact-Ops → Data Intel) | `person.applied` or `org.applied` where `tenants.data_intel_publish_consent = true` AND person/org `consent_records` permits | Subset of canonical record + Contact-Ops event_id | `contact-ops-publisher` service client POSTs to `verify.centerdeep.online/catalogue/submit`. Returns Data Intel's canonical ID. Bridge agent upserts `data_intel_link`. |
| **Inbound** (Data Intel → Contact-Ops) | 6-hourly cron via `data_intel_bridge` agent | Field-level enrichment proposals | Lands as `action_event` with `actor.act.sub = 'data_intel'`, source = `'data_intel'`, status = `proposed`. Always `propose_only` unless tenant explicitly opts in per-event-type. |

**HIPAA tenants are structurally publish-disabled.** The publisher checks `tenant_is_hipaa()` before issuing every outbound; no flag override at runtime.

**Identity reconciliation** lives in `data_intel_link` (one-to-one mapping `contact_ops_id ↔ data_intel_id`). Merges in Contact-Ops cascade to Data Intel via `POST /catalogue/merge` from the Bridge agent.

---

## Communication protocol

### Escalation
**Open questions go to Aaron via `/Users/aaronstransky/Documents/Contact-Ops-Open-Questions.md`.** Append, do not overwrite. Each entry: date, category (architecture / schema / policy / vendor), question, why it matters, options considered, your recommendation. Aaron reads in batches.

If you don't see the file, create it. Don't make assumptions about anything the design doc doesn't explicitly cover.

### Status reports
When you finish a track of work, write a status report to `/Users/aaronstransky/Documents/Contact-Ops-Track-<X>-<topic>-Report.md`:
- Files touched (with line counts)
- Commit hashes
- Time spent (rough estimate)
- Verification done (what you ran, what passed, what failed)
- Open questions raised
- Factual decisions made beyond the design doc that need ratification

### ADRs
Significant decisions (vendor choice, schema deviation, security trade-off, infrastructure swap) get an ADR at `docs/decisions/NNNN-<slug>.md` using the standard template:
```
# NNNN. <Title>

**Date**: YYYY-MM-DD
**Status**: Proposed | Accepted | Superseded by NNNN
**Deciders**: <names>

## Context
<why this decision is happening>

## Decision
<what we chose>

## Consequences
<what changes; what we accept>

## Alternatives considered
<what we rejected and why>
```

---

## Don'ts

- **Don't push to `main`.** All work lands on a track branch first. Pushes happen only after Aaron's orchestrator (Claude Opus 4.7 1M context) green-lights the full review pass.
- **Don't add real secrets to any file in the repo.** No exceptions. `.env` stays out of git history. `.env.example` is example-only, with placeholder values. If you find a secret in any committed file, treat it as a P0 incident: rotate the secret, scrub history, then carry on.
- **Don't bypass idempotency.** Every mutation tool has it. Adding a "fast path" without it is a bug.
- **Don't trust `X-Tenant-Id` header.** Use the JWT claim. The header exists only as a debugging affordance in STANDALONE_MODE for local dev, and STANDALONE_MODE must refuse to enable when `ENV=production`.
- **Don't allow `verify_signature=False` JWT decoding anywhere.** Not in middleware, not in the legacy security helper, not in tests outside an explicitly fenced JWKS mock. The Phase 0 review caught this; do not regress.
- **Don't UPDATE/DELETE `action_event`.** Append-only enforced at the role level. If you find yourself reaching for an UPDATE, you want to write a superseding event instead.
- **Don't mix Contact-Ops and Data Intel data in one DB.** They're separate databases on purpose (HIPAA fence, RLS posture, forward portability for white-label customers).
- **Don't write f-string SQL or Cypher.** Parameterized only.
- **Don't introduce a single-table-per-feature pattern.** The schema is normalized and intentional. New attributes go on existing tables or into `facts` if they're truly freeform.
- **Don't auto-apply legal-class or `is_deceased` events.** Forever `propose_only`. Aaron's `feedback_confidence_tags_legal_work.md` memory is non-negotiable.
- **Don't strip emojis** from this file or any other doc unless Aaron asks. But also don't add emojis to code or new docs, Aaron's writing style is plain.
- **Don't add em-dashes** (`--`). Aaron specifically dislikes them. Use commas, semicolons, or two hyphens (`--`) when you must.

---

## Reference: Aaron's memory notes that matter for Contact-Ops

These live in `/Users/aaronstransky/.claude/projects/-Users-aaronstransky/memory/`. Read the ones you need before touching the related area.

| File | Why it matters |
|------|---------------|
| `project_centerdeep_data_intel.md` | The fork origin. Confirms ~70% of Contact-Ops scaffolding exists already (catalogue_contacts, PhoneIntel, source tracking, etc.) and what's missing. |
| `project_meeting_ops_brigade_integration.md` | The FalkorDB pattern Contact-Ops mirrors. Tenancy modes `shared` / `per_org_graph` / `per_org_instance`. Best-effort + queued writes. Cross-org leak tests. F-string Cypher pre-commit linter. |
| `reference_keycloak_topology.md` | Two Keycloak instances (bigboy `unicorn-keycloak` + commander `uchub-keycloak`), peer-federated. Sessions 30d idle / 1yr max. Contact-Ops OIDC clients land in the `uchub` realm on commander. |
| `reference_keycloak_user_attributes.md` | Where `uc_uid` and other claims live as mappers. Assign the user-attributes scope to any new client to get `uc_uid` for free. |
| `reference_keycloak_auto_ucuid.md` | New users on either Keycloak get a clean `uc_uid` auto-set at creation via the deployed SPI. |
| `feedback_oidc_user_id_canary.md` | Authenticate by `sub`, identify users by `uc_uid`. `preferred_username` is the trap. |
| `feedback_confidence_tags_legal_work.md` | The discipline for legal / adversarial / investigative work. Tag every fact ✅/📋/💬/❓/⚠️. Never mix case files across clients. Legal-class relations forever propose_only. |
| `feedback_agent_scoping_pattern.md` | Agents bind tenant/workspace + private/team/org/shared visibility. Default new agents to private. |
| `feedback_quality_standard.md` | No MVP work. |
| `feedback_garage_object_storage.md` | Garage (Rust, S3-compatible) is the default object store for new services. Don't add more MinIO. |
| `feedback_backup_all_stateful_surfaces.md` | DB-only backup is not enough. Every Docker named volume needs its own backup. action_event archive goes to a write-once Garage bucket. |
| `project_uc_mcp_federation.md` | Federated MCP via Unicorn Commander. RFC 8693 token-exchange OBO. Agent token mint. The pattern Contact-Ops follows. |
| `project_uc_cloud_postgres_no_ssl.md` | `unicorn-postgresql` runs without TLS internally; clients use `sslmode=disable`. |
| `project_traefik_cleanup_2026_05_20.md` | DNS-01 ACME issuance. Cloudflare for centerdeep / magicunicorn.dev domains. |
| `reference_traefik_version_per_node.md` | Traefik v3.6+ everywhere for Docker 29 compat. |
| `reference_brightdata_serp.md`, `reference_serper_dev.md` | Aaron's SERP API access for the Enrichment Agent. |
| `feedback_media_cutover_smoke_test.md` | HTTP 200 does NOT prove cutover worked. Verify real downstream side effects: file bytes, analytics events, webhook deliveries, env after restart (force-recreate, not restart). Apply when cutting over services. |

---

## When in doubt

1. **Check the design doc** (`/Users/aaronstransky/Documents/Contact-Ops-MCP-Design.md`). It is 5,685 lines because every important question is answered somewhere in it.
2. **Check the relevant Aaron memory note** in the table above.
3. **Append to Contact-Ops-Open-Questions.md** if you still don't have an answer. Do not guess.
4. **Run the thing.** Don't ship code you haven't seen work end-to-end against a real Postgres.

Quality bar repeated for emphasis: world-class, not MVP. No half-measures. Verify execution after every change.
