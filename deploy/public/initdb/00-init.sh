#!/bin/bash
# Contact-Ops PUBLIC instance — Postgres bootstrap (runs once, on first init).
#
# Creates the dedicated `contactops` database + the runtime LOGIN roles that the
# app binds RLS through. The NOLOGIN group roles (contact_ops_app/_audit/_ro) are
# ALSO created idempotently by migration 0001 — we pre-create them here so the
# login roles can be granted membership before migrations run. Migrations connect
# as the `postgres` superuser (MIGRATION_DATABASE_URL) for DDL + CREATE EXTENSION
# vector + CREATE ROLE; the runtime connects as the non-superuser `co_app`, so RLS
# is always enforced on it (it is neither superuser nor table owner).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  -- NOLOGIN group roles (migration 0001 re-creates these idempotently)
  DO \$\$ BEGIN CREATE ROLE contact_ops_app   NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;
  DO \$\$ BEGIN CREATE ROLE contact_ops_audit NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;
  DO \$\$ BEGIN CREATE ROLE contact_ops_ro    NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;

  -- LOGIN users: members of the group roles, so they inherit the migration-granted
  -- privileges AND the RLS policies (\`... TO contact_ops_app\`) apply to them.
  DO \$\$ BEGIN
    CREATE ROLE co_app   LOGIN PASSWORD '${CO_APP_PASSWORD}'   IN ROLE contact_ops_app;
  EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;
  DO \$\$ BEGIN
    CREATE ROLE co_audit LOGIN PASSWORD '${CO_AUDIT_PASSWORD}' IN ROLE contact_ops_audit;
  EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;

  -- Dedicated database (owned by the superuser; migrations run as superuser).
  SELECT 'CREATE DATABASE contactops OWNER ' || current_user
   WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'contactops')\gexec

  GRANT CONNECT ON DATABASE contactops TO co_app, co_audit;
EOSQL

echo "[contact-ops init] roles + contactops database ready"
