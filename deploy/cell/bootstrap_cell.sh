#!/usr/bin/env bash
#
# bootstrap_cell.sh — stand up a fresh Contact-Ops cell, in order.
#
# This is a THIN, SAFE orchestrator. It does NOT duplicate the logic in the
# real scripts (scripts/keycloak_bootstrap.sh, scripts/garage_bootstrap.sh) and
# it does NOT invent secrets — it runs the deterministic, idempotent prep
# (DB + runtime login role + stack up + health) and then PRINTS the remaining
# manual steps that need human-supplied secrets.
#
# Order (must not change): create DB + login roles  ->  docker compose up
# (prestart AUTO-RUNS alembic to head + asserts schema_guard)  ->  manual
# keycloak / garage / flag-flip steps.
#
# Idempotent + non-destructive: re-running is safe; it refuses to touch a DB
# that already has Contact-Ops tables.
#
# Usage:
#   cp deploy/cell/.env.cell.template .env   # fill it in first
#   bash deploy/cell/bootstrap_cell.sh [--dry-run]
#
set -euo pipefail

# --- locate repo root (this script lives in deploy/cell/) --------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

DRY_RUN=0
for arg in "${@:-}"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    "") ;;
    *) echo "unknown flag: $arg" >&2; exit 1 ;;
  esac
done

log()  { printf '\033[0;32m[cell]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[cell WARN]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[0;31m[cell ERROR]\033[0m %s\n' "$*" >&2; exit 1; }
run()  { if [[ $DRY_RUN -eq 1 ]]; then printf '+ %s\n' "$*"; else eval "$@"; fi; }

PG_CONTAINER="${PG_CONTAINER:-contact-ops-postgres}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8501/health}"

# --- 0. preflight ------------------------------------------------------------
log "=== 0. Preflight ==="
command -v docker >/dev/null 2>&1 || die "docker not found on PATH"
[[ -f ".env" ]] || die "No .env found. Copy deploy/cell/.env.cell.template -> .env and fill it in first."
[[ -f "$COMPOSE_FILE" ]] || die "Compose file '$COMPOSE_FILE' not found in $REPO_ROOT"

# Required env keys must be present + non-placeholder in .env.
require_env() {
  local key="$1"
  local val
  val="$(grep -E "^${key}=" .env | head -n1 | cut -d= -f2- || true)"
  [[ -n "$val" ]] || die "Required env '$key' is missing/empty in .env"
  case "$val" in
    *CHANGE_ME*|*"<generate"*) die "Required env '$key' still has a placeholder value — fill it in." ;;
  esac
}
for k in DATABASE_URL MIGRATION_DATABASE_URL CONNECTOR_ENCRYPTION_KEY KEYCLOAK_ISSUER; do
  require_env "$k"
done
# self-host sanity: must not run STANDALONE in prod (config.py rejects it anyway)
if grep -qE '^ENV=prod' .env && grep -qiE '^STANDALONE_MODE=true' .env; then
  die "ENV=prod with STANDALONE_MODE=true is rejected by config.py — fix .env."
fi
log "  .env present, required keys set."

# Pull the runtime role + password out of DATABASE_URL for the role-create step.
DB_URL="$(grep -E '^DATABASE_URL=' .env | head -n1 | cut -d= -f2-)"
RUNTIME_ROLE="$(echo "$DB_URL" | sed -E 's|^[a-z+]+://([^:]+):.*|\1|')"
RUNTIME_PASS="$(echo "$DB_URL" | sed -E 's|^[a-z+]+://[^:]+:([^@]+)@.*|\1|')"
[[ -n "$RUNTIME_ROLE" && "$RUNTIME_ROLE" != "$DB_URL" ]] || die "Could not parse runtime role from DATABASE_URL"
log "  runtime login role: $RUNTIME_ROLE"

# --- 1. Postgres reachable + idempotency / non-destructive guard -------------
log "=== 1. Postgres + login role (init_db) ==="
if ! docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
  die "Postgres container '$PG_CONTAINER' is not running. Create it first (see README step 2): \
pgvector/pgvector:pg16 with POSTGRES_USER=contact_ops_admin, POSTGRES_DB=contact_ops_db."
fi

# NON-DESTRUCTIVE guard: refuse to "init" a DB that already has CO tables.
EXISTING="$(docker exec -i "$PG_CONTAINER" psql -U contact_ops_admin -d contact_ops_db -tAc \
  "SELECT to_regclass('public.persons') IS NOT NULL OR to_regclass('public.tenants') IS NOT NULL;" 2>/dev/null || echo "error")"
if [[ "$EXISTING" == "t" ]]; then
  warn "Contact-Ops tables already exist in contact_ops_db — skipping role create + treating as RE-RUN (non-destructive)."
else
  log "  creating runtime LOGIN role (idempotent; NOLOGIN policy roles come from alembic 0001)..."
  run "docker exec -i $PG_CONTAINER psql -U contact_ops_admin -d contact_ops_db <<SQL
-- Pre-create the NOLOGIN policy/group roles idempotently so the runtime LOGIN
-- role can be made a MEMBER of contact_ops_app BEFORE migrations. alembic 0001
-- re-creates these idempotently; creating them here is harmless + required for
-- the membership grant below (RLS policies are '... TO contact_ops_app' and
-- table privileges are GRANTed to contact_ops_app, so the runtime role only
-- inherits them + has the policies apply if it is a member of contact_ops_app).
DO \\\$\\\$ BEGIN CREATE ROLE contact_ops_app   NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END \\\$\\\$;
DO \\\$\\\$ BEGIN CREATE ROLE contact_ops_ro    NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END \\\$\\\$;
DO \\\$\\\$ BEGIN CREATE ROLE contact_ops_audit NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END \\\$\\\$;
-- Runtime LOGIN role (NOBYPASSRLS -> RLS-subject), created IN ROLE contact_ops_app
-- so the group privileges + RLS policies apply. Fallback ALTERs + GRANTs membership
-- if the role already existed.
DO \\\$\\\$ BEGIN
  CREATE ROLE $RUNTIME_ROLE LOGIN NOBYPASSRLS PASSWORD '$RUNTIME_PASS' IN ROLE contact_ops_app;
EXCEPTION WHEN duplicate_object THEN
  ALTER ROLE $RUNTIME_ROLE LOGIN NOBYPASSRLS PASSWORD '$RUNTIME_PASS';
  GRANT contact_ops_app TO $RUNTIME_ROLE;
END \\\$\\\$;
-- Belt-and-suspenders: ensure membership even if the role pre-existed without it.
GRANT contact_ops_app TO $RUNTIME_ROLE;
SQL"
fi

# --- 2. Bring up the stack (prestart auto-migrates) --------------------------
log "=== 2. docker compose up (prestart auto-runs alembic to head + schema_guard) ==="
run "docker compose -f $COMPOSE_FILE up -d --build"

# --- 3. Wait for health ------------------------------------------------------
log "=== 3. Wait for backend health ==="
if [[ $DRY_RUN -eq 1 ]]; then
  log "  (dry-run) would poll $HEALTH_URL"
else
  ok=0
  for i in $(seq 1 60); do
    if docker exec contact-ops-backend curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
      ok=1; break
    fi
    sleep 3
  done
  if [[ $ok -eq 1 ]]; then
    log "  backend healthy."
    docker exec contact-ops-backend curl -fsS "$HEALTH_URL" || true
    echo
  else
    warn "backend did not become healthy in ~3min."
    warn "Check logs for migration/schema_guard failure: docker compose -f $COMPOSE_FILE logs contact-ops-backend"
    warn "(prestart asserts EXPECTED_ALEMBIC_REVISION — a mismatch fails boot on purpose.)"
    exit 1
  fi
fi

# --- 4. Remaining MANUAL steps (need human-supplied secrets) -----------------
cat <<'NEXT'

============================================================================
 Automated cell prep complete. Remaining MANUAL steps (need real secrets):
============================================================================

 4a. Keycloak realm + clients (idempotent; does NOT touch secrets in .env):
       KC_SERVER_URL=https://auth.unicorncommander.ai \
       KC_REALM=uchub KC_ADMIN_USER=admin KC_ADMIN_PASS='<admin>' \
       APP_HOST=<contacts.host> MCP_HOST=<mcp.host> CARDDAV_HOST=<carddav.host> \
         bash scripts/keycloak_bootstrap.sh
       Then create the switch client:
         bash scripts/keycloak/setup_contact_ops_switch.sh
       Fetch confidential client secrets and paste into .env
       (KEYCLOAK_SWITCH_CLIENT_SECRET, publisher/carddav/bridge secrets):
         docker exec -i uchub-keycloak /opt/keycloak/bin/kcadm.sh \
           get clients/<id>/client-secret -r uchub
       -> then: docker compose -f docker-compose.prod.yml up -d contact-ops-backend

 4b. Garage per-tenant buckets (ONLY when storage auto-provision goes on):
       set GARAGE_ADMIN_TOKEN + real GARAGE_ACCESS_KEY + GARAGE_AUTO_PROVISION=true, then:
         docker exec contact-ops-backend python -m contact_ops.services._bootstrap_cli --force

 4c. Frontend: build with VITE_* baked (analytics/keycloak/MCP URLs are
     compile-time). Changing them later needs a frontend REBUILD.

 4d. Going live = flip the dormant flags in .env per deploy/cell/README.md (d).
     All shadow-first; observe *_would_* logs before enforcing.

 Verify: curl -fsS https://mcp.contacts.<domain>/health
         curl -fsS https://mcp.contacts.<domain>/api/auth/signup-config
============================================================================
NEXT

log "Done."
