# Stand up a fresh Contact-Ops cell

Deploy-template runbook for the Contact-Ops SaaS-readiness work
(P-00075, deploy-templates bucket). Same code, different config — a "cell" is
one self-contained Contact-Ops deployment behind one set of domains.

---

## (a) Topology recap

Domains are **roles**, not forks. One image, three configs:

| Cell | Front-end host | MCP host | Keycloak realm | Notes |
|---|---|---|---|---|
| **PRODUCT** | `contacts.unicorncommander.ai` | `mcp.contacts.unicorncommander.ai` | `auth.unicorncommander.ai/realms/uchub` (public) | `DEPLOYMENT_MODE=hosted`, tiers enforceable |
| **DOGFOOD** | `contacts.magicunicorn.dev` | `mcp.contacts.magicunicorn.dev` | same public `uchub` realm | internal; `DEPLOYMENT_MODE=hosted` |
| **SELF-HOST** | customer's own host | customer's own host | customer's own Keycloak/realm | `DEPLOYMENT_MODE=self_host` — entitlement/billing/quota gating **bypassed** |

**Federation is broker-based and domain-agnostic.** Cells don't trust each
other by hostname. They trust a **Brigade broker** (`BRIGADE_TRUSTED_ISSUER(S)`)
that mints RFC-8693 exchanged tokens carrying a per-org key. A cell verifies
each issuer against its **own** JWKS (the iss-key-mismatch guard stops one
broker impersonating another), so adding a sovereign customer broker is a CSV
edit (`BRIGADE_TRUSTED_ISSUERS`), not a code change.

---


## (b0) Pick your infra topology

Two ways to stand the data stack a cell needs. **Same image, same `prestart`
auto-migrate (asserts `schema_guard` head `0043`), same dormant-flag contract** —
they differ ONLY in where postgres/redis/garage/qdrant/falkordb come from.

| | **Shared-infra** | **Self-contained-infra** |
|---|---|---|
| Compose file | `docker-compose.prod.yml` | `docker-compose.selfcontained.yml` |
| Data stack | reuses the box's shared `unicorn-*` infra (postgres/redis/qdrant/falkordb/garage) | **bundles its own** `contact-ops-*` postgres/redis/qdrant/falkordb/garage on a private `contact-ops-internal` network |
| Networks | `unicorn-network` + `web` | `contact-ops-internal` + `web` |
| Env template | `deploy/cell/.env.cell.template` | `deploy/selfcontained/.env.selfcontained.template` |
| Postgres DB + roles | **hand-create** `contact-ops-postgres` + the `contact_ops_runtime` LOGIN role (step 2 above / `bootstrap_cell.sh`) | **auto**: the bundled postgres makes `contact_ops_db` + the `contact_ops_admin` superuser; `deploy/selfcontained/initdb/00-init.sh` makes `contact_ops_runtime` **and grants it into `contact_ops_app`** so RLS enforces — **skip the manual role step** |
| Extra secrets | DSN passwords only | + compose-only `PG_ADMIN_PASSWORD`, `CO_RUNTIME_PASSWORD`, `GARAGE_RPC_SECRET`, `GARAGE_ADMIN_TOKEN`, `QDRANT_API_KEY`, `CERT_RESOLVER` |
| Best for | our product/dogfood cells co-located with suite infra | **self-host customers** + standalone boxes with no suite infra to borrow |

**Self-host customers usually want self-contained** — one `docker compose -f
docker-compose.selfcontained.yml up -d` brings up the whole cell (data stack +
app) with no manual infra overlay. Bring-up then follows the SAME ordered steps
below, except step 2 (create DB + roles) is done for you by the bundled
postgres's init — go straight to "up".

> RLS note: the bundled `initdb/00-init.sh` makes `contact_ops_runtime` a MEMBER
> of `contact_ops_app` (the policy role the RLS policies target). That membership
> is what makes RLS apply to the app login role; the shared-infra path relies on
> the same grant being done out-of-band (see step 2 / the P4 RLS tests).

---

## (b) Prerequisites

A cell needs, reachable from the backend container:

- **Postgres with pgvector** (`pgvector/pgvector:pg16`). The shared
  `unicorn-postgresql` is alpine and has **no pgvector** — Contact-Ops uses a
  dedicated `contact-ops-postgres`. The container's `POSTGRES_USER` is the
  **`contact_ops_admin`** bootstrap superuser (BYPASSRLS); the `contact_ops_runtime`
  app login role (NOBYPASSRLS) is created in the init step.
- **Redis** (`unicorn-redis`, db 5 by default).
- **Garage** object storage + admin endpoint (for photo/voice buckets). Optional
  until you enable per-tenant auto-provisioning.
- **A Keycloak realm** with the `user-attributes` client scope already present
  (the bootstrap script hard-fails exit 2 without it). Product/dogfood reuse the
  public `uchub` realm; self-host points at the customer's realm.
- **DNS + Traefik** on the `web` Docker network with a cert resolver. Traefik
  only discovers the containers when they're attached to `web` (compose attaches
  both `unicorn-network` and `web`).
- **Qdrant** and **FalkorDB** (suite-shared) for embeddings + knowledge graph.

---

## (c) Ordered stand-up steps

> Order matters: the DB + login roles must exist **before** the stack boots,
> because the backend's `prestart` auto-runs Alembic on startup (it does **not**
> create roles or the database).

### 1. Copy + fill the env contract
```bash
cp deploy/cell/.env.cell.template .env
# Fill every CHANGE_ME / <generate>. At minimum:
#   DATABASE_URL / MIGRATION_DATABASE_URL passwords, CONNECTOR_ENCRYPTION_KEY,
#   KEYCLOAK_ISSUER, CORS_ORIGINS, FRONTEND_URL.
# CONNECTOR_ENCRYPTION_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Required-or-boot-fails (per `core/config.py` validators): `DATABASE_URL`,
`CONNECTOR_ENCRYPTION_KEY` (unless STANDALONE/test), and `KEYCLOAK_ISSUER`
(unless `STANDALONE_MODE=true`). `STANDALONE_MODE=true` is **rejected** when
`ENV=prod`.

### 2. Create the database + LOGIN roles FIRST
The Postgres container is created with `POSTGRES_USER=contact_ops_admin`,
`POSTGRES_DB=contact_ops_db`, so the admin (BYPASSRLS) login role + DB already
exist. Then create the **runtime** (NOBYPASSRLS) login role the app DSN uses:
```bash
docker volume create contact-ops-pgdata
docker run -d --name contact-ops-postgres --restart unless-stopped \
  --network unicorn-network \
  -e POSTGRES_USER=contact_ops_admin \
  -e POSTGRES_PASSWORD='<MIGRATION password from .env>' \
  -e POSTGRES_DB=contact_ops_db \
  -v contact-ops-pgdata:/var/lib/postgresql/data \
  pgvector/pgvector:pg16

# Create the runtime login role (idempotent). bootstrap_cell.sh does this for you;
# manual equivalent:
docker exec -i contact-ops-postgres psql -U contact_ops_admin -d contact_ops_db <<'SQL'
DO $$ BEGIN
  CREATE ROLE contact_ops_runtime LOGIN NOBYPASSRLS PASSWORD '<RUNTIME password from .env>';
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
SQL
```
The **NOLOGIN policy roles** (`contact_ops_app`, `contact_ops_ro`,
`contact_ops_audit`) are NOT created here — Alembic `0001` creates them
idempotently on first migrate (step 3). Note: alembic `0001` does **not** grant
the runtime LOGIN role membership in them. That membership is established by the
INIT step — `bootstrap_cell.sh` here for shared-infra, or
`deploy/selfcontained/initdb/00-init.sh` for the self-contained cell — which
runs `CREATE ROLE <runtime> ... IN ROLE contact_ops_app` (plus a
`GRANT contact_ops_app TO <runtime>`). This membership is **REQUIRED**: the RLS
policies are written `... TO contact_ops_app` and table privileges are GRANTed to
`contact_ops_app`, so the runtime role only inherits those privileges and has the
RLS policies apply to it when it is a member of `contact_ops_app`.

### 3. Bring up the stack — prestart AUTO-MIGRATES
```bash
docker compose -f docker-compose.prod.yml up -d --build
```
The backend image's `CMD` is `python -m contact_ops.ops.prestart`, which:
1. runs `alembic upgrade head` (using `MIGRATION_DATABASE_URL`, the admin role),
2. asserts `schema_guard.EXPECTED_ALEMBIC_REVISION` (**currently `0043`**) — if
   the head and the guard disagree the boot **fails loudly**,
3. then `os.execv`'s into uvicorn on `:8501`.

So there is **no separate migration command** in normal operation — migrations
are an automatic, guarded part of boot. (A standalone
`docker compose run --rm contact-ops-backend alembic upgrade head` is only for
controlled/manual migration windows.)

### 4. Bootstrap Keycloak (realm clients + mappers + switch SA)
Run from wherever the realm's Keycloak admin CLI / container is reachable:
```bash
KC_SERVER_URL=https://auth.unicorncommander.ai \
KC_REALM=uchub \
KC_ADMIN_USER=admin \
KC_ADMIN_PASS='<keycloak-admin>' \
APP_HOST=contacts.unicorncommander.ai \
MCP_HOST=mcp.contacts.unicorncommander.ai \
CARDDAV_HOST=carddav.contacts.unicorncommander.ai \
  bash scripts/keycloak_bootstrap.sh
```
This is **idempotent** and creates: the realm role ladder
(`CONTACT_OPS_CLIENT/STAFF/MANAGER/ADMIN`), the Phase-0 client scopes, and five
clients — `contact-ops-mcp` (bearer-only), `contact-ops-app` (public PKCE),
`contact-ops-carddav`, `contact-ops-publisher`, `contact-ops-bridge-inbound` —
each wired with the **`tenant_id` / `tenant_slug` / `tenant_hipaa` user-attribute
mappers** and a self **audience** mapper. It requires the realm's
`user-attributes` scope to pre-exist (exit 2 otherwise).

> The **switch client** (`contact-ops-switch`, manage-users SA for silent
> workspace re-auth) is created by `scripts/keycloak/setup_contact_ops_switch.sh`.
> Fetch its secret and put it in `KEYCLOAK_SWITCH_CLIENT_SECRET`; until then the
> `/switch` endpoint is inert (503), which is safe.

Fetch confidential client secrets into `.env`:
```bash
docker exec -i uchub-keycloak /opt/keycloak/bin/kcadm.sh get clients/<id>/client-secret -r uchub
```

### 5. Garage per-tenant buckets (only when storage auto-provision goes on)
With `GARAGE_AUTO_PROVISION=true` + a real `GARAGE_ADMIN_TOKEN` + real
`GARAGE_ACCESS_KEY` set, provision the per-tenant photo/voice buckets:
```bash
docker exec contact-ops-backend python -m contact_ops.services._bootstrap_cli --force
```
(`scripts/garage_bootstrap.sh` is a thin wrapper over the same CLI.) This is a
**no-op** when `DEPLOYMENT_MODE=self_host`, in standalone mode, when the admin
token is absent, or while `GARAGE_ACCESS_KEY` is the placeholder sentinel.

### 6. Build the frontend with VITE_* baked
The SPA compiles analytics + Keycloak + MCP URLs at **build** time
(`frontend/Dockerfile.prod`). Pass the `VITE_*` values as build args/env before
`vite build`. Changing any `VITE_*` later requires a **frontend rebuild +
redeploy** — except signup, which is backend-driven (`/api/auth/signup-config`).

---

## (d) Going live = flipping the dormant flags

Everything ships OFF. The cell works without any of these; turning a feature on
is a config flip + its prerequisite. Guards that can block are **shadow-first**
(observe `*_would_*` logs, then enforce).

| Feature | Flip | Prerequisite |
|---|---|---|
| **Membership gate** | `MEMBERSHIP_GATE_ENFORCED=true` | seed `user_tenant_membership` rows FIRST or you lock everyone out |
| **Entitlement** (per-tool tiers) | `ENTITLEMENT_ENFORCED=true` | hosted only (self_host bypasses); confirm tier tags in `mcp/entitlement.py`; watch `entitlement_would_deny` first |
| **Billing / Lago** | `BILLING_PROVIDER=lago` + `LAGO_API_URL` + `LAGO_API_KEY`, then `BILLING_QUOTA_ENFORCED=true` | a reachable suite Lago + CO-scoped key; quota is shadow until enforced; self_host bypasses |
| **Compliance engine** | `COMPLIANCE_ENGINE_ENABLED=true` | turns erase into two-phase tombstone (`ERASURE_GRACE_DAYS` undo window becomes real) |
| **Retention sweep** | `RETENTION_SWEEP_ENABLED=true`, then `RETENTION_SWEEP_SHADOW=false` | Celery beat running; observe `retention_would_purge` counts before hard-purge; never touches legal_hold/non-expiring classes |
| **Signup** | `SIGNUP_ENABLED=true` | suite mode → set `SUITE_SIGNUP_URL`; standalone → enable self-registration on the realm. **No frontend rebuild** |
| **Email (Postmark)** | `POSTMARK_SERVER_TOKEN=<token>` + `EMAIL_SENDING_ENABLED=true` | `EMAIL_FROM` must be a **confirmed** Postmark Sender Signature or every send 422s; reuse the suite's one shared token |
| **Hardening — headers** | `SECURITY_HEADERS_ENABLED=true`, then `SECURITY_HEADERS_SHADOW_MODE=false` | HSTS (`SECURITY_HSTS_ENABLED=true`) only behind real TLS |
| **Hardening — body cap** | `BODY_SIZE_LIMIT_ENABLED=true`, then `BODY_SIZE_SHADOW_MODE=false` | set `BODY_SIZE_PATH_OVERRIDES` for bulk-import paths |
| **Hardening — rate limit** | `RATE_LIMIT_ENABLED=true`, then `RATE_LIMIT_SHADOW=false` | set `TRUSTED_PROXY_IPS` (Traefik) so X-Forwarded-For is trusted; Redis reachable |
| **Hardening — SSRF** | `SSRF_GUARD_ENABLED=true`, then `SSRF_SHADOW=false` | populate `SSRF_ALLOWED_HOSTS` (federation/CardDAV internal targets) before enforcing |
| **Storage auto-provision** | `GARAGE_AUTO_PROVISION=true` | real `GARAGE_ADMIN_TOKEN` + real `GARAGE_ACCESS_KEY`, then run `_bootstrap_cli` (step 5) |
| **Analytics** (Umami/PostHog) | set `VITE_UMAMI_*` / `VITE_POSTHOG_*` | **frontend REBUILD** required (VITE_* bake at build time) |
| **Observability** (Sentry) | `SENTRY_DSN=<dsn>` | backend-runtime only; PII never sent (`send_default_pii` hardcoded False) |

---

## (e) Self-host note

For `DEPLOYMENT_MODE=self_host`:

- Entitlement, billing metering, and the billing quota gate are **bypassed
  entirely** — it's the operator's hardware and data; gating it is pointless.
- Leave `LAGO_*`, `BILLING_PROVIDER=manual`, and `SENTRY_DSN` **unset**, and
  leave the `VITE_UMAMI_*` / `VITE_POSTHOG_*` analytics vars **empty**, so the
  cell does **not phone home**.
- Garage auto-provision no-ops; the operator owns their Garage.
- Point `KEYCLOAK_ISSUER` / `VITE_KEYCLOAK_ISSUER` at the operator's own realm.

---

## (f) Verification checklist

```bash
# 1. Health (shows version)
curl -fsS https://mcp.contacts.<domain>/health
#   -> {"status":"healthy","service":"Contact-Ops","version":"...","timestamp":"..."}

# 2. Signup surface (backend-driven; proves SIGNUP_* config is live)
curl -fsS https://mcp.contacts.<domain>/api/auth/signup-config

# 3. Unauthenticated MCP must 401
curl -i -X POST https://mcp.contacts.<domain>/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
#   -> 401, WWW-Authenticate: Bearer error="invalid_token"

# 4. Authenticated MCP call — mint a service-account token then list tools
TOKEN=$(curl -s -X POST https://auth.<domain>/realms/<realm>/protocol/openid-connect/token \
  -d grant_type=client_credentials \
  -d client_id=contact-ops-publisher \
  -d client_secret="$PUBLISHER_SECRET" \
  -d 'scope=person:read org:read' | jq -r .access_token)
curl -s -X POST https://mcp.contacts.<domain>/mcp \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```
Also confirm in the backend logs at boot: `alembic_upgrade_completed` then
`schema_version_current expected=0043 actual=0043`.
