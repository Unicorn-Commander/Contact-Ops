#!/usr/bin/env bash
# Contact-Ops PostgreSQL restore — recovery runbook driver.
# DESTRUCTIVE: drops and recreates the configured database after typed consent.

set -euo pipefail
umask 077

CONFIG_FILE="${CONTACT_OPS_BACKUP_CONFIG:-${HOME}/.config/contact-ops/backup.env}"
if [ -f "$CONFIG_FILE" ]; then
  # shellcheck disable=SC1090
  . "$CONFIG_FILE"
fi

LOCAL_KEEP_DIR="${CONTACT_OPS_BACKUP_LOCAL_DIR:-${HOME}/backups/contact-ops}"
DB_USER="${CONTACT_OPS_DB_USER:-contact_ops_admin}"
DB_NAME="${CONTACT_OPS_DB_NAME:-contact_ops_db}"
CONTAINER="${CONTACT_OPS_DB_CONTAINER:-contact-ops-postgres}"
BACKEND_CONTAINER="${CONTACT_OPS_BACKEND_CONTAINER:-contact-ops-backend}"
REMOTE_URI="${CONTACT_OPS_BACKUP_REMOTE_URI:-}"
S3_ENDPOINT="${CONTACT_OPS_BACKUP_S3_ENDPOINT:-}"
AWS_BIN="${CONTACT_OPS_AWS_BIN:-$(command -v aws 2>/dev/null || true)}"

print_usage() {
  cat <<'EOF'
Usage:
  ./contact-ops-restore.sh PATH_TO_DUMP.sql.gz
  ./contact-ops-restore.sh --latest
  ./contact-ops-restore.sh --from-lambda YYYYMMDD

Options:
  --latest             Restore from the most recent local backup
  --from-lambda DATE   Download from the configured S3-compatible destination
  -h, --help           Show this help

WARNING: drops and recreates contact_ops_db. Stop all writers first. A typed
confirmation is required before any database mutation.
EOF
}

if [ "$#" -lt 1 ]; then
  print_usage
  exit 1
fi

DUMP_FILE=""
TEMP_DOWNLOAD=0

case "$1" in
  -h|--help)
    print_usage
    exit 0
    ;;
  --latest)
    DUMP_FILE="$(find "$LOCAL_KEEP_DIR" -maxdepth 1 -type f -name 'contact_ops_db_*.sql.gz' -print 2>/dev/null | sort -r | head -1)"
    [ -n "$DUMP_FILE" ] || { echo "ERROR: no local backups in $LOCAL_KEEP_DIR"; exit 1; }
    ;;
  --from-lambda)
    [ "$#" -ge 2 ] || { echo "ERROR: --from-lambda requires YYYYMMDD"; exit 1; }
    DATE="$2"
    [[ "$DATE" =~ ^[0-9]{8}$ ]] || { echo "ERROR: date must be YYYYMMDD"; exit 1; }
    [ -n "$REMOTE_URI" ] || { echo "ERROR: CONTACT_OPS_BACKUP_REMOTE_URI is not configured"; exit 1; }
    [ -n "$AWS_BIN" ] || { echo "ERROR: AWS CLI is unavailable"; exit 1; }
    DUMP_FILE="$(mktemp "${TMPDIR:-/tmp}/contact_ops_db_${DATE}.XXXXXX.sql.gz")"
    TEMP_DOWNLOAD=1
    endpoint_args=()
    [ -n "$S3_ENDPOINT" ] && endpoint_args=(--endpoint-url "$S3_ENDPOINT")
    echo "Downloading from configured off-host backup..."
    "$AWS_BIN" s3 cp \
      "${REMOTE_URI%/}/contact_ops_db_${DATE}.sql.gz" \
      "$DUMP_FILE" \
      "${endpoint_args[@]}"
    ;;
  *)
    DUMP_FILE="$1"
    ;;
esac

cleanup() {
  if [ "$TEMP_DOWNLOAD" -eq 1 ] && [ -n "$DUMP_FILE" ]; then
    rm -f -- "$DUMP_FILE"
  fi
}
trap cleanup EXIT

[ -f "$DUMP_FILE" ] || { echo "ERROR: dump file not found: $DUMP_FILE"; exit 1; }
gzip -t "$DUMP_FILE" || { echo "ERROR: dump is not a valid gzip archive"; exit 1; }

echo "Restore source: $DUMP_FILE ($(du -sh "$DUMP_FILE" | cut -f1))"
echo
echo "WARNING: this will DROP and recreate $DB_NAME on container $CONTAINER."
echo "Type 'restore contact-ops' to confirm:"
read -r CONFIRM
if [ "$CONFIRM" != "restore contact-ops" ]; then
  echo "Aborted."
  exit 0
fi

echo "Stopping $BACKEND_CONTAINER so it cannot write during restore..."
docker stop "$BACKEND_CONTAINER" || true

echo "Dropping and recreating $DB_NAME..."
docker exec -i "$CONTAINER" psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME;"
docker exec -i "$CONTAINER" psql -U "$DB_USER" -d postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"

echo "Restoring from dump..."
gunzip -c "$DUMP_FILE" | docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME"

echo "Starting $BACKEND_CONTAINER..."
docker start "$BACKEND_CONTAINER"

sleep 6
echo "Health check..."
docker exec "$BACKEND_CONTAINER" curl -fsS http://localhost:8501/health || {
  echo "WARN: backend not responding"
  exit 3
}
echo "Restore complete."
