# Contact-Ops — Operations Runbook

Operational reference for running Contact-Ops in production (the bigboy
single-operator deployment). Captures the deploy loop, verification patterns,
migrations, the membership gate, and the gotchas that have actually bitten.

## Topology

- **Host:** bigboy (Tailscale `100.126.207.157`, `ssh magicunicorn`, user `muut`).
  Code at `/home/muut/contact-ops` (git clone of
  `git.unicorncommander.ai/aaron/Contact-Ops.git`, deployed branch **`main`**
  — repointed from `feat/brigade-multi-issuer-verifier` at the v2.7.0 publish;
  commit + deploy directly on `main` now).
- **Services (`docker-compose.prod.yml` + `docker-compose.prod.override.yml`):**
  `contact-ops-backend` (uvicorn, internal :8501; hosts both the MCP server at
  `mcp.contacts.magicunicorn.dev` and CardDAV at
  `carddav.contacts.magicunicorn.dev`) and `contact-ops-frontend` (nginx). The
  prod Celery overlay adds a **background worker and beat scheduler**:
  `contact-ops-celery-worker` (queues `agents.batch.high`, `agents.batch.low`,
  `agents.calibration`; `--concurrency=4`) and `contact-ops-celery-beat` run the
  scheduled agent fleet — inbox snooze re-surfacing, retention sweep, graph-sync
  outbox drain, and the batch + calibration agents — over the Redis broker
  (`unicorn-redis`, Celery db 9). Postgres + FalkorDB + Redis are external on the
  `unicorn-network`. The graph is written best-effort in-request; the
  `graph_sync_outbox` is drained by the worker as a backstop (no longer dead code).
- **Data:** Postgres (multi-tenant via RLS) is the source of truth; FalkorDB
  holds a per-tenant relationship graph (`contact_ops__<slug>`), best-effort
  synced. Dogfood tenant `019e50a3-d995-723f-ab66-0f765f92c0f4`
  (uc_uid `REMOVED`).

## Deploy

The working tree on bigboy IS the build context. Two ways code arrives there:

1. **Local edit → rsync** (the iterative loop): edit the mirror at
   `/Volumes/Studio Storage/Development/_co-bigboy`, then
   `rsync -az <file> magicunicorn:/home/muut/contact-ops/<path>`. macOS rsync —
   plain flags only. NOTE: `_co-bigboy` is **not a git repo**; commits happen on
   bigboy. After any merge/pull on bigboy, **re-sync the mirror FROM bigboy** or
   the next rsync up will clobber it.
2. **git on bigboy** (for merging branches): `git fetch && git merge …` directly
   in `/home/muut/contact-ops`.

Then build + recreate:
```
cd /home/muut/contact-ops
docker compose -f docker-compose.prod.yml build <contact-ops-backend|contact-ops-frontend>
docker compose -f docker-compose.prod.yml up -d --force-recreate <svc>
```
- Use `up -d` (NOT `restart`) so `.env` is re-read.
- Frontend is a multi-stage `node:20-alpine` → nginx build (`npm ci` runs inside
  Docker; the host `node_modules` is empty). `index.html` is no-cache; assets are
  content-hashed — confirm the new bundle:
  `docker exec contact-ops-frontend sh -lc 'grep -oE /assets/index-[A-Za-z0-9_-]+\.js /usr/share/nginx/html/index.html'`.
- **Migrations now run automatically on backend start** (see below).
- Commit + push when a change is verified — work committed only on bigboy is a
  single point of failure (it has been stranded before).

## Migrations (auto-run on boot)

The backend Dockerfile CMD is `python -m contact_ops.ops.prestart`, which runs
`alembic upgrade head` → asserts the schema version → `os.execv`s into uvicorn.
So a deploy that ships new migrations applies them before serving; a migration
failure stops the container (it will NOT boot healthy against a wrong schema).

- Check the live schema version:
  `SELECT version_num FROM alembic_version;` (head is the highest `NNNN_…` under
  `backend/alembic/versions/`).
- **Rollback a migration:** `alembic downgrade -1` (each migration ships a real
  `downgrade()`; e.g. 0039 deletes only the rows it seeded).
- The boot also refuses to start if Postgres is unreachable (a refused/timed-out
  connection raises `ConnectionRefusedError`/`TimeoutError`, caught at
  `main.py` lifespan) — so "DB down" is a clear failed boot, not a healthy box
  that 500s every request.

## Connector encryption key rotation

Connector payload encryption supports two configuration forms:

- `CONNECTOR_ENCRYPTION_KEY=<single-fernet-key>` is the backwards-compatible
  legacy form.
- `CONNECTOR_ENCRYPTION_KEYS=["<primary-fernet-key>","<fallback-fernet-key>"]`
  is an ordered JSON ring. The first key encrypts every new/updated payload;
  later keys are decrypt-only fallbacks. When present, the ring takes
  precedence over the legacy setting.

The API, Celery worker, and Celery beat all validate the same `Settings`
contract at process start and all three consume the same Compose env file. Do
not advance the primary until every process is running a ring-aware release.
The safe no-outage sequence is:

1. Take and verify a database backup. Generate the replacement Fernet key in
   the operator secret store; never pass a key on this CLI or put it in shell
   history.
2. Stage `CONNECTOR_ENCRYPTION_KEYS=["<old>","<new>"]`. The old key remains
   primary, so legacy ciphertext and concurrent writes remain readable while
   the ring-aware release is rolled through backend, worker, and beat. Confirm
   every old process has exited.
3. Reorder the shared setting to
   `CONNECTOR_ENCRYPTION_KEYS=["<new>","<old>"]` and roll all three processes
   again. Both the old-primary and new-primary ring-aware processes can read
   both generations, so the rollout order is not data-dependent. Do not roll
   back to a pre-keyring release after this point.
4. Run the authenticated read-only preflight inside the backend image:

   ```bash
   python -m contact_ops.connectors.key_rotation --dry-run --actor <operator-id>
   ```

5. Rewrap in one transaction, then prove no fallback key is needed:

   ```bash
   python -m contact_ops.connectors.key_rotation --rewrap --actor <operator-id>
   python -m contact_ops.connectors.key_rotation --verify-new-only --actor <operator-id>
   ```

6. Only after `verify-new-only` succeeds, set the ring to `["<new>"]`, update
   the legacy single-key value to the same new key (for configuration
   compatibility), roll backend/worker/beat, and retire the old key from the
   secret store.

The command accepts no key or database arguments. It reads the shared Settings
contract and refuses to use the runtime DSN: `MIGRATION_DATABASE_URL` must
authenticate as a PostgreSQL `SUPERUSER` or `BYPASSRLS` role. Rewrap and the
primary-only verification gate briefly block connector writes while reads
continue. Rewrap preflights every ciphertext before its first update, verifies
semantic JSON equality, compare-and-swaps each row, and primary-only verifies
the locked table before commit. A corrupt row, unauthorized role, concurrent
mismatch, or failed verification aborts and rolls back the whole operation.
Output contains counts only, never keys, ciphertext, or connector plaintext.

## Verifying changes WITHOUT a JWT (the in-process pattern)

Build an `MCPContext` and call handlers directly, exactly like
`tests/test_mcp_tools_emails.py`. Pattern:
```
docker exec -i contact-ops-backend python - < /tmp/verify.py
# verify.py:
#   bind_session_context(db, TENANT, UC_UID, settings)   # sets the RLS GUC
#   ctx = MCPContext(tenant_id=…, claims={"realm_access":{"roles":["ADMIN","STAFF"]},
#                    "scope":"person:read person:write …"}, db=db, audit_db=db, …)
#   await some_handler(ctx, SomeInput(...))
```
**⚠️ The GUC trap (has bitten repeatedly):** `SET LOCAL app.tenant_id` is
*transaction-scoped*. Any `db.commit()` / `db.rollback()` CLEARS it, so the next
read returns 0 rows ("not found" / RLS hides everything). In verification scripts:
flush + `rollback()` to inspect, or read on a FRESH bound session after a commit.
In app code: re-bind both sessions after any mid-operation commit.

## Membership gate (owner action — flip with care)

`MEMBERSHIP_GATE_ENFORCED` is currently **true** (enforced in prod). When `true`,
every request's authenticated `uc_uid` must have an *active* row in
`user_tenant_membership` for its tenant, or it is rejected (at the JWT
middleware, the MCP server, and `get_tenant_db`). Memberships are seeded for
existing tenants and **new users self-provision a personal workspace + admin
membership on first login** via `POST /api/auth/onboard` (migration 0040's
`provision_personal_workspace`), so a fresh interactive login is NOT locked out —
verified end-to-end 2026-06-11 (fresh KC token → onboard → re-minted
tenant-bearing token → authenticated call passes the gate). The PAT and Brigade
paths also honor the gate (a revoked membership disables a user's PATs/federated
access for that tenant immediately). **Before flipping the flag on a NEW
deployment, confirm YOUR real Keycloak `sub` has an `active` membership row**
(`SELECT * FROM user_tenant_membership WHERE tenant_id=…`), or you lock yourself
out. Codex's `tests/test_membership_gate_smoke.py` exercises the predicate. Flip
via the backend `.env` + `up -d` (not restart).

## Entitlement gate (plan-tier enforcement — owner action)

Per-tool plan-tier gating at MCP dispatch (P-00075 §4). The free/paid line is
**"gate on compute cost"**: free = reads + MCP/UI CRUD + deterministic dedup +
graph + CSV/vCard import (cheap, their compute); **pro** = our-GPU autonomous
agents (ML dedup `propose_merge`, enrichment, voice fingerprint/match);
**enterprise** = compliance/SSO/federation (no tools yet). The policy is the
single source of truth in `contact_ops/mcp/entitlement.py` (`_PRO_TOOLS` /
`_ENTERPRISE_TOOLS`); the Brigade manifest reads the same `tier_for_tool()` so
they can't drift. Per-tenant plan lives in `tenants.plan_tier` (migration 0041;
existing tenants grandfathered to `enterprise`, new self-served tenants default
`free`).

Two flags, both in the backend `.env`:
- `ENTITLEMENT_ENFORCED` (default **false** = shadow): the gate only **logs**
  what it would deny (`entitlement_would_deny`) and allows the call. Flip `true`
  to actually deny (`ENTITLEMENT_REQUIRED`). **Shadow-first like the membership
  gate** — review `entitlement_would_deny` logs for any *free* tenant hitting a
  tool you didn't mean to gate BEFORE flipping, or a mis-tag hard-locks a paying
  tenant out of work they expect.
- `DEPLOYMENT_MODE` (default `hosted`): set `self_host` to **bypass gating
  entirely** — an open-source operator on their own cell gets everything.

Currently **shadow** in prod (flag unset). Enforced + verified in-process
2026-06-11 (free tenant → pro tool denied, free tool allowed, import allowed,
self_host bypasses). Flip via `.env` + `up -d` (not restart).

## Known gotchas (hard-won)

- **Deploy from the right place:** the project root has symlinks; the live source
  is the bigboy clone (a git repo); the Mac mirror `_co-bigboy` is NOT a git repo.
  Don't let a stale mirror rsync over merged code.
- **`rsync --delete` from a stale mirror clobbers committed files:** the mirror
  drifts from the bigboy clone (tests added on bigboy, Dockerfile changed there).
  A `--delete` sync deleted two test files and reverted `backend/Dockerfile`
  (committed = `CMD prestart` which auto-migrates; the mirror still had the old
  `CMD uvicorn` → no auto-migration, DB stuck a rev behind). ALWAYS `git status`
  on bigboy after an rsync and `git checkout --` the collateral, then
  **re-baseline the mirror FROM bigboy** (`rsync bigboy:…/backend/ → mirror`)
  after committing so it stops drifting.
- **rsync `..` paths:** `…/mcp/tools/../services/` resolves to `mcp/services/`,
  not `contact_ops/services/` — a bad `..` once created a stray dupe dir. Use
  explicit destination paths.
- **Heredocs over ssh:** a nested `<<EOF` inside `ssh '…'` quoting silently
  fails. Write the script to a file, `rsync` it, and `docker exec -i … python - < file`.
- **Bulk vs single approve:** both `approve_proposal` and `bulk_approve` now
  EFFECT `person.create` (create the contact + children + graph). If you ever add
  a new proposal type that needs side effects, wire BOTH paths.
- **FalkorDB client reuse:** the per-contact graph write opens a Redis connection
  + bootstrap; in any batch loop, create ONE shared client and pass it down
  (see `bulk_approve`).

## Backups

- **DB backup is wired and optionally off-host** via `scripts/contact-ops-backup.sh` (cron
  `10 3 * * *`): a superuser `pg_dump` of `contact_ops_db` (the `contact_ops_admin`
  superuser bypasses RLS — the `contact_ops_runtime` user is RLS-subject and a
  pg_dump as it fails "query would be affected by row-level security policy" with
  only schema/0 rows), gzipped to `~/backups/contact-ops/` (30-day local
  retention) and, when configured, uploaded to an operator-controlled
  S3-compatible destination. Log: `~/logs/contact-ops-backup.log`.
  The local backup directory is forced to mode `0700`; the log and published
  database/encrypted-environment artifacts are forced to mode `0600`, including
  when a same-day filename already exists.
- **Off-host credentials are operator-owned and untracked.** Copy
  `deploy/contact-ops-backup.env.example` to
  `~/.config/contact-ops/backup.env`, set mode `0600`, and configure the AWS
  credential chain outside Git. Both backup and `--from-lambda` restore use
  this file by default; override it with `CONTACT_OPS_BACKUP_CONFIG`.
- **DB restore:** use `scripts/contact-ops-restore.sh --latest`, pass an explicit
  local dump, or use `--from-lambda YYYYMMDD` with the protected off-host config.
  The driver validates the gzip before prompting, requires typed consent, stops
  the backend plus live Celery worker/beat from Docker Compose labels (or every
  explicit container in `CONTACT_OPS_WRITER_CONTAINERS`), recreates the database,
  and restores in one transaction with `ON_ERROR_STOP`. It stops writers in
  reverse startup order and restarts the API before workers/scheduler. If a stop
  or SQL step fails, it exits with writers left stopped for operator inspection
  rather than reopening a partially restored service.
- **`.env` (server secrets) backup — age public-key encrypted, off-host.** The
  same backup also uploads `contact_ops_env_YYYYMMDD.env.age`. **Key model: ONE
  keypair per DEPLOYMENT, held by the operator (NOT per end-user — users back up
  their contacts via the app; `.env` is server secrets).** Asymmetric on purpose:
  - **Public key (recipient)** lives on the box at
    `~/.config/contact-ops/backup-age-recipient.txt` — it only *encrypts*, so it
    is safe and open-source-friendly (ship it / let each operator drop in their own).
  - **Private key (identity)** at `~/.config/contact-ops/backup-age-identity.txt`
    (chmod 600) — it *decrypts*, and its authoritative copy lives **off-box in the
    operator's password manager (Vaultwarden)** so a box loss is recoverable.
  - **`.env` restore:** `age -d -i <identity-from-Vaultwarden> contact_ops_env_YYYYMMDD.env.age > .env`.

## Open-source readiness (before publishing the repo)

- **Credential incident:** older revisions of both backup and restore scripts
  contained a Lambda S3 Adapter access-key pair. Current scripts load only a
  protected untracked operator config, but removing the values from `HEAD` does
  not revoke them or erase them from Git history. Rotate/revoke the old pair,
  update every consumer, verify an off-host backup and restore, then perform an
  approved coordinated history rewrite before publishing or cloning the repo.
- FalkorDB is intentionally not backed up — the graph rebuilds from Postgres via
  `backend/scripts/graph_backfill.py`.
- **Connector scheduling:** pulls are manual (`pull_connector_now`) / owner-
  triggered; there is no scheduler. OAuth tokens now refresh-before-pull.
- **Metrics surface:** `audit_write_failures_total` is wired; alerting on it
  (and on schema-version drift) still needs a Prometheus scrape + alert rule.

## Known follow-ups (tracked, non-blocking — flagged by adversarial review)

Surfaced during the v2.7.0/v2.7.1 reviews; all latent / fail-closed, deferred
deliberately rather than rushed (the security-adjacent ones want their own
careful change + re-review):

- **Brigade verifier — RS256 pinned + multi-issuer trust now live** (was the
  "EdDSA capability gap"). **RESOLVED in v2.7.2.** python-jose has no EdDSA/OKP
  support (verified live: `jwk.construct(okp, "EdDSA")` raises `JWKError`), so the
  old `ALLOWED_BRIGADE_ALGORITHMS = ["EdDSA", "RS256"]` advertised a capability
  the verifier could not deliver, and the test signed EdDSA tokens with PyJWT
  that the jose decode path can never validate (the jwt-import trap) — meaning the
  REAL algorithm Brigade signs with (RS256) was untested. Now: `ALLOWED_BRIGADE_ALGORITHMS`
  and `brigade_registration.signing_algorithm` are **RS256 only**; the rewritten
  `tests/test_brigade_jwt_verifier.py` exercises the real RS256 path, asserts an
  EdDSA token is refused (`alg-rejected`), and covers the cross-broker
  `iss-key-mismatch` guard. **Also un-broke multi-issuer trust:** `config.py` now
  DECLARES `BRIGADE_TRUSTED_ISSUERS` — it was read via `getattr` but never a
  declared field, so pydantic's `extra="ignore"` dropped the operator's `.env`
  value and only the singular issuer was ever trusted. Contact-Ops now trusts
  BOTH `brigade.unicorncommander.ai` (customer) and `brigade.magicunicorn.dev`
  (dogfood/sovereign), each verified against its OWN JWKS (both serve RS256), with
  the impersonation guard preventing one broker from signing as the other.
  Smoke without pytest: `docker exec -i contact-ops-backend python - < scripts/verify_brigade_rs256.py`.
  **Forward item:** python-jose will NOT gain EdDSA; if Brigade ever migrates off
  RS256, switch this module's decode path to PyJWT (already a dependency) and add
  a real EdDSA round-trip test — do NOT just re-add "EdDSA" to the allow-list.
- **Brigade verifier — trailing-slash issuer.** `_trusted_issuers` rstrips `/`
  on config but compares the raw `claims.iss`, so a broker whose issuer URL ends
  in `/` is false-rejected (availability, fails closed). No current broker does.
  Fix = normalize `claims.iss` consistently (interacts with `jwt.decode`'s own
  issuer check — re-run the kid→issuer adversarial test after).
- **merge_people / unmerge_people nice-to-haves.** (a) Map a commit-time
  `IntegrityError` to a *retryable* `ToolError` rather than opaque JSON-RPC
  `-32603` (the FOR UPDATE lock makes the unique-violation rare, so low value).
  (b) `unmerge` regenerates `loser.etag` rather than restoring the pre-merge
  value — cosmetic (etag is an opaque concurrency token, no business data).
