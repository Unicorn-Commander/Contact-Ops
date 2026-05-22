#!/usr/bin/env bash
# Idempotent Garage bucket bootstrap for Contact-Ops tenants.
#
# Reads tenant rows from the running Contact-Ops Postgres and ensures
# every (tenant_slug, bucket_kind) pair has a Garage bucket created
# with the right retention policy. Safe to re-run; existing buckets
# are detected via the admin API and only their retention is refreshed.
#
# Usage:
#   GARAGE_ADMIN_ENDPOINT=http://unicorn-garage:3903 \
#   GARAGE_ADMIN_TOKEN=<token> \
#   DATABASE_URL=postgresql+asyncpg://contact_ops_app:...@db/contact_ops \
#       ./scripts/garage_bootstrap.sh
#
# Optional flags:
#   --tenant=<slug>     Only provision buckets for this single tenant.
#   --dry-run           Print what would be created without calling Garage.

set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required" >&2
    exit 1
fi

ARGS=("$@")

python3 -m contact_ops.services._bootstrap_cli "${ARGS[@]}"
