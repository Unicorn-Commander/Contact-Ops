# Contact-Ops Architecture

**Date**: 2026-07-18
**Status**: v2.19.x (shipped through `phase-6-complete`; MCP tool surface live, ~95 tools)

This document is a navigable summary of the Contact-Ops architecture. When it disagrees with the `backend/` implementation, the code wins.

---

## 1. Product positioning

Contact-Ops is the **active contact management layer** in a three-tier pattern:

```
+--------------------+     +----------------------+     +----------------------+
| Contact-Ops        |     | Data Intel           |     | CRM-Ops (future,     |
| (active mgmt)      |     | (passive intel DB)   |     |  not built)          |
|--------------------|     |----------------------|     |----------------------|
| Your contacts      |     | B2B catalogue        |     | Lead pipeline        |
| Your tenants       |     | SMTP verification    |     | Deal stage           |
| Private relations  |     | Public records       |     | Sales workflow       |
| Lifetime registry  |     | Cross-app dedup      |     | Touchpoints          |
| Agent inbox        |     | Enrichment vendors   |     | Quota tracking       |
| Approval ceremony  |     | Source citations     |     |                      |
| 3D graph nav       |     |                      |     |                      |
| CardDAV sync       |     |                      |     |                      |
+--------------------+     +----------------------+     +----------------------+
        ^                            ^                             ^
        |                            |                             |
        +----- RFC 8693 OBO ---------+                             |
        |                                                          |
        +-------- consumes Contact-Ops MCP --------------------- --+
```

LinkedIn maps to the Contact-Ops half (you curate your network). Apollo maps to the Data Intel half (passive B2B catalogue). The original Contact-Ops brief explicitly excludes CRM features. If they're ever needed, they go in CRM-Ops, a separate downstream consumer.

**Key design commitments** (from §2 of the design doc):
- **The MCP server IS the product.** Human UI, CardDAV, REST (when needed), and the 3D viewer all consume the same MCP tool surface.
- **Agent-first.** Every tool is designed to be called by an agent first and a human second. Confidence, idempotency, etags, batch events, and structured proposals are baseline.
- **Federation, not absorption.** Contact-Ops federates with Data Intel, Meeting-Ops, Crisis-Ops, Project-Ops, Brigade, Stable. It does not absorb them.
- **"Rest of my life" durability.** No hard deletes by default. Bitemporal validity. 90-day unmerge. Field-level provenance forever.
- **White-label from the schema up.** Tenancy is not a feature layer bolted on; it's in the schema from migration 002.
- **World-class, not MVP.** Calibration, audit, RLS, HIPAA fencing, pgvector + Qdrant ANN, etag-guarded PATCH, all from day one.
- **Voice fingerprint canonical** via Meeting-Ops Parakeet on midboy2. A person can exist with no name and no email, only a 256-d voice embedding.
- **3D graph navigation** via FalkorDB + react-force-graph-3d is the canonical way to ask "who knows whom" and "who connects me to X."

---

## 2. System topology

### 2.1 ASCII map

```
                                  +-------------------------------+
                                  |  contacts.magicunicorn.dev    |
                                  |  (Next.js human UI)           |
                                  +---------------+---------------+
                                                  |
                                                  v
+----------------------+        +-----------------+-----------------+
| iOS/macOS Contacts   |        |   mcp.contacts.magicunicorn.dev   |
| Thunderbird          |--CardDAV->|  (FastAPI MCP server, OAuth 2.1, |
| Google Contacts      |        |   RFC 8707 resource indicators)   |
+----------------------+        +-----------------+-----------------+
                                                  |
                                                  v
+----------------------+                +---------+--------+
| Listing-Ops / Crisis | <--MCP-OBO---> | contact-ops-     |
| Project-Ops / Meeting|                | backend          |
| Stable / Brigade     |                | (FastAPI, agents,|
+----------------------+                | workers, jobs)   |
                                        +-------+----------+
                                                |
      +-----------+-------------+---------------+-------------+----------+
      v           v             v               v             v          v
+----------+ +----------+ +-----------+ +------------+ +---------+ +--------------+
| unicorn- | | contact- | | unicorn-  | | unicorn-   | | uchub-  | | dataintel-   |
| postgres-| | ops-     | | qdrant    | | garage     | | keycloak| | backend      |
| ql       | | falkordb | | (center-  | | (bigboy)   | | (cmdr)  | | (verify.     |
|(centerdp)| |(centerdp)| |  deep)    | | bucket-    | | uchub   | |  centerdeep) |
| contact- | | per-     | | 6 col-    | | prefix     | | realm   | | RFC 8693     |
| ops_db   | | tenant   | | lections  | | per-tenant | | OIDC +  | | inbound /    |
| pgvector | | graphs + | | per-      | | SSE-KMS    | | RFC 8693| | outbound     |
| RLS +    | | bigboy DR| | tenant    | | DEK        | | OBO     | |              |
| HIPAA    | | mirror   | | filter    | |            | |         | |              |
+----------+ +----------+ +-----------+ +------------+ +---------+ +--------------+
```

### 2.2 Service inventory (centerdeep Docker Compose)

```yaml
services:
  contact-ops-backend:     # FastAPI + MCP server (uvicorn workers)
  contact-ops-worker:      # arq-style background jobs: graph_sync_outbox drainer,
                           # calibration daemon, signal recomputers
  contact-ops-agents:      # supervised agent process (Dedup, Enrichment, etc.)
  contact-ops-carddav:     # radicale-style CardDAV adapter -> Contact-Ops MCP
  contactops-falkordb:     # FalkorDB v4+
  # uses existing: unicorn-postgresql, unicorn-qdrant, unicorn-garage (bigboy),
  #                uchub-keycloak (commander)
```

### 2.3 Endpoint inventory

| Endpoint | Purpose | Protocol | Auth |
|----------|---------|----------|------|
| `contacts.magicunicorn.dev` | Human Next.js UI | HTTPS | OIDC code+PKCE (`contact-ops-app` client) |
| `mcp.contacts.magicunicorn.dev` | MCP HTTP+SSE | HTTPS+SSE | OAuth 2.1 + RFC 8707 (`contact-ops-mcp` client) |
| `carddav.contacts.magicunicorn.dev` | CardDAV | HTTPS+WebDAV | HTTP Basic over TLS (`contact-ops-carddav` client) |
| White-label vanity domains | Per-tenant branded UI | HTTPS | Tenant routing via host header in `tenants.branding.host` |

### 2.4 Network placement

- **centerdeep** (Tailscale `centerdeep.unicorncommander.net`, 100.87.46.87): primary backend + Postgres + FalkorDB + Qdrant + MCP endpoint + human UI.
- **bigboy** (`magicunicorn.unicorncommander.net`): Garage canonical, FalkorDB DR mirror. Hosts midboy2 voice embedding service (Parakeet on midboy2 GPU 0, bge-m3 on midboy2 GPU 1).
- **midboy1** (Qwen 35B-A3B): inference for Email Signature Parser and Business Card OCR (Qwen3-VL).
- **midboy2**: embeddings + reranking. Voice embeddings via Parakeet.
- **commander**: `uchub-keycloak` realm canonical SSO.
- **lilguy1**: primary DR (per Aaron's `feedback_no_aws_lambda_backups.md`).

### 2.5 Keycloak OIDC clients (in the `uchub` realm on commander)

| Client | Type | Use |
|--------|------|-----|
| `contact-ops-app` | Confidential, code+PKCE | Human UI at `contacts.magicunicorn.dev` |
| `contact-ops-mcp` | Confidential, OAuth 2.1 + RFC 8707 resource indicators | MCP server |
| `contact-ops-carddav` | Confidential, HTTP Basic | Legacy CardDAV clients |
| `contact-ops-publisher` | Confidential, client_credentials | Service-to-service for Data Intel Bridge outbound |
| `contact-ops-bridge-inbound` | Confidential, service client | Data Intel pushing enrichments inbound |

Trusted MCP peers for RFC 8693 token-exchange OBO: Data Intel MCP, Meeting-Ops MCP, Project-Ops MCP, Crisis-Ops MCP.

User-attributes mapper: `uc_uid` and subscription/api_calls claims (per Aaron's `reference_keycloak_user_attributes.md`).

---

## 3. Database layout

### 3.1 `contact_ops_db` is separate from `dataintel_db`

Both live on `unicorn-postgresql` on centerdeep. They are deliberately separate databases. Reasons:

1. **Schema-shape gap.** Contact-Ops adds 35+ tables and 200+ indexes. Comingling makes migration risk asymmetric.
2. **RLS posture.** Contact-Ops needs per-tenant RLS on every row. Data Intel's verification-catalogue model is largely "global" data with `source_apps` filtering. Mixing them complicates the RLS policy surface.
3. **HIPAA tenants.** HIPAA tenant data must never reach the shared Data Intel catalogue. Separate DBs make that fence physically real (different roles, different network ACLs even if same instance).
4. **Forward portability.** White-label customers may want their Contact-Ops on a per-customer Postgres. Keeping `contact_ops_db` isolated makes that flip mechanical (`pg_dump | pg_restore` to a new instance).

### 3.2 Postgres roles

| Role | Purpose | Privileges |
|------|---------|-----------|
| `contact_ops_app` | Application connections | SELECT/INSERT/UPDATE/DELETE on regular tables; INSERT/SELECT on `action_event` (UPDATE/DELETE revoked) |
| `contact_ops_audit` | Audit middleware connection | SELECT/INSERT on `action_event` only |
| `contact_ops_ro` | Read-only operators | SELECT on everything |

Connection string: `postgresql+asyncpg://contact_ops_app@unicorn-postgresql:5432/contact_ops_db?ssl=disable` (per Aaron's `project_uc_cloud_postgres_no_ssl.md`, no SSL internally).

### 3.3 Extensions

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS vector;       -- pgvector
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_uuidv7;
```

### 3.4 Table groups

Full DDL in design doc §4.1 (lines 361-1568). Summary:

| Group | Tables |
|-------|--------|
| **Tenancy** | `tenants`, `tenant_keys` |
| **Identity** | `persons`, `organizations`, `person_tenant_membership`, `organization_tenant_membership` |
| **Contact attributes** | `emails`, `phones`, `postal_addresses`, `identifiers`, `im_handles`, `urls` |
| **Media** | `media_assets`, `photos`, `voice_fingerprints`, `voice_samples` |
| **Relationships** | `person_org_role`, `person_person_relation`, `org_org_relation` |
| **Facts + provenance** | `sources`, `facts`, `field_provenance` |
| **Interactions + signals** | `interactions`, `topics` |
| **Tags + consent** | `tags`, `consent_records` |
| **Audit** | `action_event` (append-only at the Postgres role level) |
| **Merge** | `merge_history`, `person_alias`, `organization_alias` |
| **Agents** | `agent_registry`, `agent_calibration` |
| **Federation** | `data_intel_link` |
| **Graph sync** | `graph_sync_outbox` |

### 3.5 Key invariants

- **`persons.canonical_owner_tenant_id` is NOT NULL** on every row from creation. The tenancy is in the schema.
- **`vcard_uid` is UNIQUE** per person, enables CardDAV round-trips.
- **Email + phone owner XOR** check: every email/phone has exactly one owner (person OR org, never both).
- **Time-bounded relationships**: every relationship table carries `valid_from` / `valid_until` for bitemporal queries.
- **Append-only `action_event`**: UPDATE and DELETE are REVOKEd at the role level. Status changes become new events with `supersedes_event_id` linkage.
- **`content_hash` + `prev_event_hash`** on every action_event for tamper detection.
- **`field_provenance` is the projection** of "who set what." Every materialized field has a row recording the event_id, actor chain, source, confidence, and a `history` JSONB array of prior values.

### 3.6 Row-Level Security + HIPAA fence

RLS is enabled (and `FORCE`d) on every tenant-scoped table. Policy patterns:

- **Tables with their own tenant column** (`canonical_owner_tenant_id` or `tenant_id`): direct policy.
  ```sql
  CREATE POLICY persons_select ON persons FOR SELECT TO contact_ops_app
    USING (canonical_owner_tenant_id = current_tenant_id()
           OR EXISTS (SELECT 1 FROM person_tenant_membership m
                      WHERE m.person_id = persons.id
                        AND m.tenant_id = current_tenant_id()
                        AND m.visibility <> 'archived'));
  ```
- **Child tables** (emails, phones, addresses, identifiers, im_handles, urls, person_org_role, photos, voice_samples): policy joins through parent.
- **Cross-entity edges** (person_person_relation, org_org_relation): use `tenant_visibility` column directly.

`current_tenant_id()` reads the Postgres GUC `app.tenant_id`, which is set per-session from the JWT claim by the database dependency. Never trust an HTTP header.

**HIPAA fence has three layers**:
1. **Tenant flag** (`tenants.hipaa_mode = true`).
2. **RLS policy** prevents cross-tenant reads on HIPAA-flagged rows.
3. **Merge trigger** (`enforce_hipaa_merge`) on `merge_history` rejects cross-tenant merges where either side is HIPAA.

```sql
CREATE OR REPLACE FUNCTION enforce_hipaa_merge() RETURNS trigger ... AS $$
  -- If kept_owner <> removed_owner AND either is HIPAA:
  RAISE EXCEPTION 'HIPAA fence: cross-tenant merge involving HIPAA tenant requires human-approved override.';
$$;
CREATE TRIGGER trg_enforce_hipaa_merge BEFORE INSERT OR UPDATE ON merge_history
  FOR EACH ROW EXECUTE FUNCTION enforce_hipaa_merge();
```

Enabling HIPAA on a tenant is forward-only without admin platform-level intervention.

---

## 4. Multi-tenancy

### 4.1 Four tenant kinds

`tenants.kind` is one of:
- `personal`, an individual's tenant (Aaron's personal address book lives here).
- `magic_unicorn_internal`, Magic Unicorn LLC tenants (work contacts).
- `brand`, Brand-owned tenants under MU (Center Deep, Majiks, GFL).
- `white_label_customer`, Independent customer tenants on the platform.

### 4.2 Graph mode

`tenants.graph_mode` is one of:
- `shared`, single global FalkorDB graph, filtered by org_id property. Dev only.
- `per_org_graph` (default), one FalkorDB graph per tenant in the shared FalkorDB instance, named `contact_ops__<tenant_slug>`.
- `per_org_instance`, dedicated FalkorDB container under Docker Compose. Premium isolation for HIPAA / sensitive tenants.

Mirrors the pattern Aaron approved in `project_meeting_ops_brigade_integration.md`.

### 4.3 Per-tenant isolation

| Layer | Mechanism |
|-------|-----------|
| Postgres | `canonical_owner_tenant_id` column + RLS policy + FORCE on every table |
| FalkorDB | Per-tenant graph (`contact_ops__<slug>`) or dedicated instance |
| Qdrant | Payload filter on `tenant_id` (every collection sharded by HASH on `tenant_id`) |
| Garage | Bucket prefix `contact-ops-<slug>-*` with per-tenant SSE-KMS DEK from `tenant_keys.kms_key_arn` |
| Traefik | Per-host routing rule resolves to tenant via `tenants.branding.host` lookup |

### 4.4 Cross-tenant sharing

A canonical person can be membership-shared into other tenants via `person_tenant_membership`. Each membership carries per-tenant `notes`, `tags`, `custom_attrs`, `visibility`, and `consent_basis`. The canonical row's `canonical_owner_tenant_id` does not change; the receiving tenant gets a per-tenant shadow.

Visibility scopes (`visibility_scope` ENUM): `private`, `team`, `org`, `shared`. Default for new memberships is `private`.

---

## 5. FalkorDB graph layer

### 5.1 Per-tenant graph schema

Each tenant gets `contact_ops__<slug>` with constraints, range indexes, fulltext indexes, and vector indexes. Node labels:

| Label | Key | Purpose |
|-------|-----|---------|
| `Person` | `id` (UUID) | Person node, with embedding(1024) for semantic queries |
| `Organization` | `id` | Org node, with embedding(1024) |
| `EmailAddress` | `address` | Shared across persons (one email node, many `HAS_EMAIL` edges) |
| `PhoneNumber` | `e164` | Shared across persons |
| `Domain` | `name` | Org membership inference |
| `Address` | `id` | Shared by household/co-residence |
| `Meeting` | `id` | Back-ref to Meeting-Ops |
| `Case` | `id` | Back-ref to Crisis-Ops |
| `Project` | `id` | Back-ref to Project-Ops |
| `Deal` | `id` | Future CRM-Ops back-ref |
| `Topic` | `id` | Interaction-derived topics |
| `Event` | `id` | Calendar events |
| `Document` | `id` | Back-ref to evidence |
| `Tag` | `id` | Per-tenant ontology |

Edge labels: `WORKS_AT`, `HAS_EMAIL`, `HAS_PHONE`, `HAS_ADDRESS`, `KNOWS`, `FAMILY_OF`, `REPORTS_TO`, `COUNSEL_FOR`, `WITNESS_FOR`, `PARTY_TO`, `MENTIONED_IN`, `DUPLICATE_OF`.

### 5.2 Graph sync (outbox pattern)

Mutations write to `graph_sync_outbox` in the same Postgres transaction. The `graph_sync_worker` (continuous poll, 100 rows `FOR UPDATE SKIP LOCKED`) drains the outbox into FalkorDB via parameterized Cypher. Failed writes increment `attempts`; at 10 attempts they move to DLQ. Nightly reconciliation job compares row count + content hash between Postgres and FalkorDB.

This is the same outbox pattern Meeting-Ops uses for Brigade (see Aaron's `project_meeting_ops_brigade_integration.md`). Best-effort + queued + reconciled.

### 5.3 Graph queries

Graph navigation tools are first-class MCP tools (design doc §5.15): `shortest_path`, `who_knows`, `mutual_connections`, `suggest_intro`, `find_clusters`, `extract_ego_graph`, `find_path_through_topic`, `find_duplicates_graph`.

The 3D viewer (Next.js + react-force-graph-3d) consumes `extract_ego_graph` and renders the user's network as an explorable 3D force layout. Same component pattern as Crisis-Ops `Graph3DView.jsx` and Brigade's `KnowledgeGraph.jsx`.

---

## 6. Qdrant collections

All collections live on `unicorn-qdrant` on centerdeep with namespace prefix `contact_ops__<tenant_slug>` enforced via payload filter. Sharded by HASH on `tenant_id`.

| Collection | Vector size | Distance | Purpose |
|------------|-------------|----------|---------|
| `contact_ops_person_voice` | 256 | Cosine | Voice fingerprint centroids + samples. Voice Match Agent ANN. |
| `contact_ops_person_face` | 512 | Cosine | Photo face embeddings. Business-card / meeting-photo OCR. |
| `contact_ops_person_name` | 384 | Cosine | Name variants (display / nickname / file_as / phonetic). Dedup fuzzy retrieval. |
| `contact_ops_person_bio` | 1024 | Cosine | Bio + headline. Semantic person search ("VC partner in Charleston"). bge-m3 on midboy2:8086. |
| `contact_ops_org_description` | 1024 | Cosine | Org description + industry classification. |
| `contact_ops_topic` | 1024 | Cosine | Per-tenant topic ontology. Tag Agent + Relationship Inference Agent. |

Sizing: 150-300 GB on-disk at 1M-person scale. Centerdeep has the capacity (8x NVMe, lz4 on payload). Nightly snapshots to Garage `contact-ops-qdrant-snapshots`, retained 30 days.

---

## 7. Garage object storage

Bucket prefix `contact-ops-<tenant_slug>-*` with per-tenant SSE-KMS DEK. Buckets:

| Bucket | Content | Lifecycle |
|--------|---------|-----------|
| `*-photos` | Person photos, org logos, derived face crops | Tied to `media_assets.retention_class` |
| `*-voice-samples` | Source audio clips (10-90s WAV at 16k mono) | Raw audio TTL 90d default; 6y for HIPAA |
| `*-business-cards` | Card scan JPEGs + OCR JSON | Raw image 30d post-parse; JSON indefinite |
| `*-vcard-archive` | vCard exports/imports, original CardDAV blobs | Keep 12 generations per UID |
| `*-evidence-snapshots` | Agent evidence (HTML pages, JSON API responses, screenshots, transcript fragments) | Tied to parent `action_event.status` and tenant retention |
| `*-exports` | User-requested exports (GDPR DSAR, vCard bulk, CSV) | 14-day TTL |

Shared (non-tenant) buckets: `contact-ops-models` (pinned model artifacts), `contact-ops-public-icons` (generic icons, public-read).

Bigboy `unicorn-garage` is the canonical instance per Aaron's `feedback_garage_object_storage.md` and `project_projectops_garage_storage.md`.

---

## 8. MCP tool surface (~89 tools across 23 domains)

Full list in design doc §5 (lines 1880-4917). Domains:

| Domain | Tools (count) |
|--------|---------------|
| People | 12 |
| People relationships | 5 |
| Organizations | 9 |
| Org membership / employment | 6 |
| Identifiers | 4 |
| Emails / phones | 11 |
| Addresses | 5 |
| Media / photos / voice | 8 |
| Tags + categories | 6 |
| Facts + provenance | 5 |
| Merge / dedup | 8 |
| Tenants | 6 |
| Visibility / ACL | 4 |
| Interactions / signals | 4 |
| Graph queries | 8 |
| Search (cross-domain) | 2 |
| Agent actions / approvals | 8 |
| Audit log | 4 |
| Agent registry | 4 |
| Data Intel federation | 3 |
| CardDAV (note) | 0 (CardDAV is a separate protocol, not MCP) |
| Import / export | 7 |
| System / health | 2 |

**Phase 0 has zero tools registered.** The MCP server in `backend/contact_ops/mcp/server.py` answers `initialize`, `tools/list` (returns empty), and `tools/call` (returns `isError: true` for any name). Phase 1 lights up the core tools.

---

## 9. Agent topology (16 agents)

Full detail in design doc §6 (lines 4919-5153). Each agent declares: name, version, owning_system, scope_mode (`per_tenant` or `shared`), visibility (`private` / `team` / `org` / `shared`), declared capabilities, current trust tier (`propose_only` / `auto_apply_low` / `auto_apply_high` / `authoritative` / `suspended`), OAuth scope set, and calibration history.

| Agent | Scope | Trust tier (initial) | Trigger |
|-------|-------|---------------------|---------|
| `dedup_agent` | shared | propose_only -> auto_apply_low after calibration | person.created/updated_significantly + nightly sweep |
| `enrichment_agent` | shared | propose_only | person.created with thin bio; nightly sweep |
| `relationship_inference_agent` | shared | propose_only (forever for legal/family) | Meeting-Ops session.completed, email signature parse, Project-Ops events, Crisis-Ops doc upload, Stable mention |
| `voice_match_agent` | shared | auto_apply_low for assignments ≥ 0.92 | Meeting-Ops speaker.embedded |
| `lifecycle_agent` | shared | propose_only (forever for is_deceased) | Daily cron |
| `tag_agent` | shared | auto_apply_low for ≥0.85 to existing tenant tags | interaction.created, fact.created; biweekly sweep |
| `carddav_recon_agent` | per_tenant | auto_apply_high for user-originated changes | CardDAV PUT, person.updated, 6h reconcile |
| `calibration_daemon` | shared (infra) | N/A | Daily cron 04:00 UTC |
| `data_intel_bridge` | per_tenant | propose_only (inbound) | person.applied/org.applied (outbound); 6h cron (inbound) |
| `graph_sync_worker` | shared (infra) | N/A | Continuous poll |
| `comm_signal_recomputer` | shared (infra) | N/A | Nightly cron 02:00 tenant-local |
| `provenance_promoter` | shared | auto_apply_high (only promotes already-vetted facts) | Nightly + event-driven on high-confidence facts |
| `consent_watchdog` | shared | authoritative (legally required) | Webhook + daily reconciliation |
| `business_card_ocr` | shared | propose_only always | Garage object create in business-cards bucket |
| `email_signature_parser` | per_tenant | auto_apply_low for stable signatures (≥0.92) | New email ingest, monthly backfill |
| `ecosystem_federation_agent` | shared | auto_apply_high for identifier additions where upstream is system-of-record | Project-Ops / Meeting-Ops / Crisis-Ops / Listing-Ops events |

**Calibration loop**: every agent starts at `propose_only` for the first 100 actions. The Calibration Daemon promotes via `agent_calibration` deltas (proposed / applied / approved / rejected / reverted counts; Brier score; ECE). Promotion to `auto_apply_low` requires <2-3% revert rate on 50 approvals. Promotion above `auto_apply_low` requires human approval flag on the promotion record. **Legal-class relations and `is_deceased` are forever `propose_only`** (Aaron's `feedback_confidence_tags_legal_work.md` memory).

Failure modes: bad actions revert via `revert_action`, increment `agent_calibration.reverted_count`. >5% recent revert rate auto-demotes back to `propose_only`.

---

## 10. Event sourcing + bitemporal validity + field provenance

### 10.1 `action_event` is the audit substrate

Every mutation in Contact-Ops writes to `action_event`. The same shape applies whether the actor is a human, an agent, an automation rule, an external system, or a migration. The shape (full DDL in §4.1.9):

```sql
event_id            uuid PRIMARY KEY              -- UUIDv7, time-ordered
event_type          text NOT NULL                 -- e.g. 'person.proposed_create', 'phone.applied_add'
event_version       integer NOT NULL DEFAULT 1
tenant_id           uuid NOT NULL
aggregate_type      entity_kind NOT NULL
aggregate_id        uuid NOT NULL
affected_ids        uuid[] NOT NULL DEFAULT '{}'
payload             jsonb NOT NULL                -- {before:{...}, after:{...}, fields:[...]}
actor               jsonb NOT NULL                -- {sub:user, act:{sub:agent, act:...}}
actor_type          actor_type NOT NULL
human_authority     uuid                          -- denormalized root user
confidence          numeric(4,3)                  -- null for humans
evidence            jsonb NOT NULL DEFAULT '{}'   -- {sources[], trace_id, prompt_hash, model, tool_calls[]}
rationale           text
status              event_status NOT NULL DEFAULT 'proposed'
proposed_at         timestamptz NOT NULL DEFAULT now()
applied_at          timestamptz
approved_by         uuid
reverted_by_event_id uuid
supersedes_event_id uuid
valid_from          timestamptz NOT NULL DEFAULT now()
valid_to            timestamptz
content_hash        bytea NOT NULL                -- SHA-256 of payload
prev_event_hash     bytea                         -- previous event in the same aggregate's chain
signature           bytea
```

REVOKE UPDATE, DELETE from `contact_ops_app`. GRANT INSERT, SELECT to `contact_ops_app` and `contact_ops_audit`.

### 10.2 Bitemporal validity windows

Most relationship tables (`person_org_role`, `person_person_relation`, `org_org_relation`, `facts`, `emails`, `phones`, `postal_addresses`) carry `valid_from` / `valid_until`. The action_event itself also has `valid_from` / `valid_to`. This lets every tool answer "what did we know about X on 2024-06-12" as a real query.

### 10.3 Field provenance projection

`field_provenance` is the materialized "click any field, see why" projection:

```sql
entity_type        text NOT NULL                  -- 'person' | 'organization'
entity_id          uuid NOT NULL
field_path         text NOT NULL                  -- 'headline', 'emails[0].address', 'current_org_id'
current_value      jsonb
set_by_event_id    uuid NOT NULL                  -- FK to action_event
set_by_actor       jsonb NOT NULL                 -- denormalized actor chain
source             source_type
source_record_id   text
confidence         numeric(4,3)
established_at     timestamptz NOT NULL
last_verified_at   timestamptz
history            jsonb NOT NULL DEFAULT '[]'    -- [{prev_value, prev_event_id, changed_at}, ...]
UNIQUE (entity_type, entity_id, field_path)
```

`get_field_provenance` is a first-class MCP tool that returns the full record for any field path on any subject.

### 10.4 Universal revert

Every event class declares an `inverse()`. `revert_action(event_id)` writes a compensating event. Batch reverts (via `revert_action(batch_event_id)`) replay in reverse order atomically. Merges have `reversible_until = performed_at + 90d`; after 90 days, merges are permanent and `unmerge` returns an error.

---

## 11. Confidence tiers + propose-vs-apply

Every agent-callable mutation accepts an optional `confidence: 0-1`. Defaults: 1.0 for humans, agent-supplied for agents.

| Tier | Default behavior |
|------|------------------|
| `≥ 0.95` | Auto-apply: returns `{status: "applied", event_id}` |
| `0.75 - 0.95` | Surface in approval inbox: returns `{status: "proposed", proposal_id}` |
| `0.50 - 0.75` | Proposed-only, lower inbox priority |
| `< 0.50` | Discarded |

Configurable per-tenant and per-event-type via `tenants.retention_policy.auto_apply_thresholds`.

**Always propose_only regardless of confidence** (Aaron's standing rule):
- Legal relations: `counsel_for`, `client_of_counsel`, `witness_for`, `party_to`, `opposing_party_to`, `expert_for`, `co_plaintiff_with`, etc.
- Family relations: `parent_of`, `child_of`, `spouse_of`, `sibling_of`, all family edges.
- Status: `is_deceased`, `death_date`.

Source: `feedback_confidence_tags_legal_work.md`.

---

## 12. HIPAA fence (three layers)

The HIPAA fence is enforced in three independent places. Defense in depth.

1. **Tenant flag**: `tenants.hipaa_mode = true`. Set on creation; forward-only without admin platform-level intervention.
2. **RLS policy**: every tenant-scoped row has a USING clause that respects the HIPAA flag for cross-tenant reads.
3. **Merge trigger**: `enforce_hipaa_merge` BEFORE INSERT OR UPDATE on `merge_history`. Rejects cross-tenant merges where either side is HIPAA.

Additional structural enforcement:
- Data Intel publisher checks `tenant_is_hipaa()` before issuing every outbound; no flag override at runtime.
- Voice samples and photos in HIPAA tenants stay 6 years (`retention_class = hipaa_6y`).
- HIPAA tenants get `read_access_logged` in `action_event` on every `get_person` / `get_org` call (per HIPAA disclosure log requirements).

---

## 13. Federation with Data Intel

### 13.1 Outbound (Contact-Ops -> Data Intel)

Triggered on `person.applied` / `org.applied` / `identifier.applied` events where the tenant has `data_intel_publish_consent = true` AND the person/org has an active `consent_records` row with `purpose = 'data_intel_share'`.

Auth: service client `contact-ops-publisher` (`uchub` realm) with `data-intel.catalogue:submit` scope. RFC 8693 token-exchange attaches the originating tenant and originating event_id.

Wire shape (subset):
```json
POST https://verify.centerdeep.online/catalogue/submit
{
  "source_app": "contact-ops",
  "source_tenant": "magic-unicorn-llc",
  "source_event_id": "01985-...",
  "kind": "person",
  "contact_ops_id": "01985-...",
  "display_name": "Jane Doe",
  "emails":   [{"address":"REMOVED","confidence":0.95}],
  "phones":   [{"e164":"+15555550100","confidence":0.95}],
  "identifiers": [{"namespace":"linkedin.com","value":"in/jane-doe","confidence":0.98}],
  "owner_tenant": "magic-unicorn-llc",
  "consent_basis": "self_provided"
}
```

Response includes Data Intel's `catalogue_contact_id`. Bridge agent upserts `data_intel_link`.

**HIPAA tenants are structurally publish-disabled.**

### 13.2 Inbound (Data Intel -> Contact-Ops)

Data Intel proposes enrichments. Each lands as an `action_event` from `data_intel_bridge`:
- `event_type = 'field.proposed_set'` (or `identifier.proposed_add`)
- `actor = {sub: 'system', act: {sub: 'data_intel_bridge', act: {sub: 'data_intel'}}}`
- `source = 'data_intel'`
- `evidence.sources = [...]` mirrored from Data Intel's underlying sources

**Always `propose_only`** unless the tenant opts in per-event-type.

### 13.3 Identity reconciliation

`data_intel_link` maintains one-to-one `contact_ops_id ↔ data_intel_id`. When Contact-Ops merges two persons that both have data_intel_links, the Bridge agent issues `POST /catalogue/merge` to Data Intel with both Data Intel IDs.

---

## 14. CardDAV server

CardDAV server endpoint at `carddav.contacts.magicunicorn.dev/<user>/<tenant>/` exposes bi-directional sync to iOS Contacts, macOS Contacts, Thunderbird, and any other CardDAV client.

- Per-tenant address books per user.
- CLIENTPIDMAP for multi-source round-trip fidelity (iCloud / Google / Contact-Ops as sources, no conflicts).
- ETag-based conflict detection. On divergence, both versions land in the Inbox as a conflict pair; never silent overwrite.
- CardDAV Reconciliation Agent (per_tenant scoping, per-user credentials) handles inbound PUTs and outbound updates with `auto_apply_high` for user-originated changes (the user themselves edited their contact on their iPhone, confidence base 0.97).

Phase 2 deliverable.

---

## 15. Voice fingerprint integration

Voice fingerprints reuse Meeting-Ops Parakeet speaker embeddings on midboy2. Per Aaron's `project_meeting_ops_model_split.md`: post-meeting STT/diarize uses midboy2 3060.

| Table | Purpose |
|-------|---------|
| `voice_fingerprints` | One row per person. `embedding` is the centroid of all samples (256-d). `embedding_model` pins the model name + version. |
| `voice_samples` | Individual recordings. `embedding`, `embedding_model`, `duration_seconds`, `quality_score`, `snr_db`, `meeting_id` back-ref, `speaker_label`. |

The Voice Match Agent runs ANN against `contact_ops_person_voice` on Qdrant when Meeting-Ops emits `speaker.embedded`. Matches above 0.85 auto-apply via `voice.proposed_assign`. Matches 0.65-0.85 propose. Below 0.65 creates a tentative person with `display_name = "Speaker <hash>"` for human relabel.

A person can exist in Contact-Ops with no name, no email, just a voice embedding. The Voice Match Agent attaches future samples and enrichments to it.

---

## 16. Deployment + migration plan

Full plan in design doc §8 (lines 5215-5391). Summary by step:

1. **Fork** centerdeep-data-intel at tag `data-intel-pre-contact-ops` into the `Contact-Ops` repo on `git.unicorncommander.ai`.
2. **Rename + compatibility views** so existing dataintel-frontend keeps working: `catalogue_contacts` -> `persons`, `catalogue_companies` -> `organizations`, `catalogue_emails` -> `emails`, `catalogue_sources` -> `sources`. Create backward-compat VIEWs.
3. **Additive migrations** 0002-0016 (tenants, identity extensions, multi-cardinality, media, relationships, facts+provenance, interactions+topics, tags+consent, action_event, merge, agents, data_intel_link, graph_sync_outbox, RLS+policies, triggers).
4. **MCP server scaffolding** with OAuth 2.1 + RFC 8707 + RFC 8693 OBO + RBAC `assertCan` helpers. Read-only tools first.
5. **Write/propose tools** in order: persons -> multi-cardinality -> relationships -> facts/provenance -> tags/consent -> media -> bulk -> dedup search.
6. **Stand up agents** in order: graph_sync_worker -> comm_signal_recomputer -> provenance_promoter -> dedup_agent -> voice_match_agent -> enrichment_agent.
7. **Frontend strategy**: build new `contact-ops-frontend` (Next.js) for management; leave `dataintel-frontend` in place for verification (re-embed verify as a feature view inside contact-ops-frontend post-GA).
8. **Cutover**:
   - T-0: parallel run, new writes mirrored into renamed tables.
   - T+30d: cut Meeting-Ops + Project-Ops + Crisis-Ops + Stable to use `contact-ops-mcp` for contact lookups.
   - T+60d: deprecate compat views.
   - T+90d: archive `dataintel-frontend` repo branch.
9. **Backups** (per Aaron's `feedback_backup_all_stateful_surfaces.md`):
   - Postgres: WAL-G to Garage `contact-ops-backups`, retention 30 days, base backup nightly.
   - FalkorDB: RDB snapshot every 6h to Garage; bigboy DR replica continuously.
   - Qdrant: nightly collection snapshots to Garage.
   - Garage buckets: replicated to bigboy + lilguy1 (primary DR per `feedback_no_aws_lambda_backups.md`); Lambda Cloud as tertiary offsite.
   - `action_event` archive nightly to write-once Garage bucket `contact-ops-actionlog-archive` (object-lock on, retention = `retention_class`).

---

## 17. CI / quality gates

`.woodpecker/pipeline.yml` runs against a real Postgres service:

1. **Lint**: ruff + black --check.
2. **Type check**: mypy `--strict`.
3. **Security scan**: bandit + safety (or CodeQL once Forgejo supports it).
4. **Migrations**: `alembic upgrade head` against fresh Postgres + extensions.
5. **Tests**: pytest with `asyncio_mode = auto`. Includes:
   - Append-only enforcement test that SETs ROLE `contact_ops_app` and attempts UPDATE/DELETE on `action_event` expecting permission-denied.
   - HIPAA fence test that attempts cross-tenant merge with one HIPAA tenant.
   - JWT validation tests with mocked JWKS: expired token, wrong audience, wrong issuer, tampered signature, unsigned (`alg:none`), missing `tenant_id` claim.
   - RLS test that connects as `contact_ops_app` with two different `app.tenant_id` GUC values and verifies isolation.
6. **Build + push**: Docker image to Forgejo registry on green.

---

## 18. Reference reading

See design doc §11 (lines 5615-5679) for the full reference list. Highlights:

- **MCP** spec (Anthropic): annotations, JSONSchema validation, structured errors, cursor pagination, SSE transport.
- **OAuth**: RFC 6749 (OAuth 2.0), OAuth 2.1 draft, RFC 8693 (token exchange / OBO), RFC 8707 (resource indicators).
- **Contact domain**: RFC 6350 (vCard 4.0), RFC 6352 (CardDAV), RFC 4791 (WebDAV), E.164, BCP 47, IANA TZ.
- **Graph**: FalkorDB docs (Cypher with caveats: no `CALL{}` or `EXISTS{}` subqueries), react-force-graph-3d, Label Propagation / Louvain community detection.
- **Vectors**: Qdrant docs, pgvector docs, bge-m3 (midboy2:8086), MiniLM-L6-v2, Pyannote / NeMo / WeSpeaker.
- **Audit / provenance**: event sourcing (Greg Young, Martin Fowler), CRDT OR-Sets, W3C PROV-DM, GDPR Article 17, HIPAA Privacy Rule.
- **Ecosystem memory notes**: see CLAUDE.md.
