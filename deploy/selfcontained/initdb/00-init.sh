#!/bin/bash
# Contact-Ops SELF-CONTAINED cell — Postgres bootstrap (runs ONCE, on first init).
#
# Runs only on a FRESH data volume (the postgres image executes
# /docker-entrypoint-initdb.d/* exactly once, when PGDATA is empty). It creates
# the runtime LOGIN role the app DSN (DATABASE_URL) authenticates as, aligned to
# MAIN's 3-role model:
#
#   contact_ops_admin    LOGIN BYPASSRLS  -> the container POSTGRES_USER /
#                        (superuser)         MIGRATION_DATABASE_URL. Auto-created
#                                            by the image; runs all migrations.
#   contact_ops_runtime  LOGIN NOBYPASSRLS -> DATABASE_URL + AUDIT_DATABASE_URL.
#                        (RLS-subject)       Created HERE. Non-superuser + not a
#                                            table owner, so RLS always applies.
#   contact_ops_app/ro/audit  NOLOGIN      -> the policy/group roles. RLS policies
#                        (group roles)       are written `... TO contact_ops_app`.
#                                            alembic 0001 CREATEs them idempotently
#                                            on first migrate; we PRE-create them
#                                            here so the runtime login role can be
#                                            granted membership BEFORE migrations.
#
# WHY the membership grant is REQUIRED for RLS:
#   The RLS policies (migrations 0015/0021/0036/0037 ...) are `TO contact_ops_app`
#   and table privileges are GRANTed to contact_ops_app (0017). The runtime login
#   role only inherits those privileges AND has those policies apply to it if it
#   is a MEMBER of contact_ops_app. main's 0001 creates the group roles but does
#   NOT grant the runtime role into them (despite the README's wording), and the
#   shared-infra path relies on an out-of-band grant; the canonical mechanism —
#   used verbatim by the P4 RLS tests (test_p4_isolation_rls / _membership_gate /
#   _auto_provision: `GRANT contact_ops_app TO contact_ops_runtime`) — is replicated
#   here so a self-contained cell enforces RLS with zero manual steps.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  -- NOLOGIN policy/group roles. alembic 0001 re-creates these idempotently; we
  -- pre-create them so the runtime login role can join contact_ops_app now.
  DO \$\$ BEGIN CREATE ROLE contact_ops_app   NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;
  DO \$\$ BEGIN CREATE ROLE contact_ops_ro    NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;
  DO \$\$ BEGIN CREATE ROLE contact_ops_audit NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;

  -- Runtime LOGIN role (NOBYPASSRLS -> RLS-subject) that DATABASE_URL uses, made
  -- a MEMBER of contact_ops_app so the group's privileges + RLS policies apply.
  DO \$\$ BEGIN
    CREATE ROLE contact_ops_runtime LOGIN NOBYPASSRLS PASSWORD '${CO_RUNTIME_PASSWORD}' IN ROLE contact_ops_app;
  EXCEPTION WHEN duplicate_object THEN
    ALTER ROLE contact_ops_runtime LOGIN NOBYPASSRLS PASSWORD '${CO_RUNTIME_PASSWORD}';
    GRANT contact_ops_app TO contact_ops_runtime;
  END \$\$;

  -- Belt-and-suspenders: ensure membership even if the role pre-existed without it.
  GRANT contact_ops_app TO contact_ops_runtime;

  -- The database (contact_ops_db) is auto-created by the postgres image from
  -- POSTGRES_DB; just grant the runtime role CONNECT on it.
  GRANT CONNECT ON DATABASE contact_ops_db TO contact_ops_runtime;
EOSQL

echo "[contact-ops init] contact_ops_runtime LOGIN role created (member of contact_ops_app); RLS will enforce."
