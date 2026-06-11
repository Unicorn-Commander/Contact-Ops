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
- **Services (`docker-compose.prod.yml`):** `contact-ops-backend` (uvicorn,
  internal :8501; hosts both the MCP server at `mcp.contacts.magicunicorn.dev`
  and CardDAV at `carddav.contacts.magicunicorn.dev`) and
  `contact-ops-frontend` (nginx). Postgres + FalkorDB are external on the
  `unicorn-network`. **There is intentionally NO background worker** — the graph
  is written synchronously in-request; the async `graph_sync_outbox` is dead code.
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

`MEMBERSHIP_GATE_ENFORCED` is currently **false**. When `true`, every request's
authenticated `uc_uid` must have an *active* row in `user_tenant_membership` for
its tenant, or it is rejected (at the JWT middleware, the MCP server, and
`get_tenant_db`). Migration 0039 seeded the tenant's `owner_user_id`, but that
may be a placeholder id — **before flipping, confirm YOUR real Keycloak `sub`
has an `active` membership row** (`SELECT * FROM user_tenant_membership WHERE
tenant_id=…`), or you lock yourself out. Codex's `tests/test_membership_gate_smoke.py`
exercises the predicate. Flip via the backend `.env` + `up -d` (not restart).

## Known gotchas (hard-won)

- **Deploy from the right place:** the project root has symlinks; the live source
  is the bigboy clone. Don't let a stale mirror rsync over merged code.
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

- **DB backup is wired and off-host** via `scripts/contact-ops-backup.sh` (cron
  `10 3 * * *`): a superuser `pg_dump` of `contact_ops_db` (the `contact_ops_admin`
  superuser bypasses RLS — the `contact_ops_runtime` user is RLS-subject and a
  pg_dump as it fails "query would be affected by row-level security policy" with
  only schema/0 rows), gzipped to `~/backups/contact-ops/` (30-day local
  retention) AND **uploaded off-host to Lambda S3**. Log: `~/logs/contact-ops-backup.log`.
- **DB restore:** `zcat ~/backups/contact-ops/contact_ops_db_YYYYMMDD.sql.gz | docker
  run --rm -i --network unicorn-network -e PGPASSWORD=$(docker exec
  contact-ops-postgres printenv POSTGRES_PASSWORD) postgres:16 psql -h
  contact-ops-postgres -U contact_ops_admin -d contact_ops_db` (into a fresh DB).
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

- **⚠️ `scripts/contact-ops-backup.sh` has the Lambda S3 access key/secret
  hard-coded and committed** (and they are in git history). Before open-sourcing:
  rotate those S3 credentials, move them to an un-committed config the script
  sources (or `.env`), and scrub history (`git filter-repo`) — the new repo
  should pull in cleaned files, not the history with the secret.
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
