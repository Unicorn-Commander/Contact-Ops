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
BACKEND_CONTAINER="${CONTACT_OPS_BACKEND_CONTAINER:-}"
WRITER_CONTAINERS="${CONTACT_OPS_WRITER_CONTAINERS:-}"
COMPOSE_PROJECT="${CONTACT_OPS_COMPOSE_PROJECT:-contact-ops}"
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

cleanup() {
  if [ "$TEMP_DOWNLOAD" -eq 1 ] && [ -n "$DUMP_FILE" ]; then
    rm -f -- "$DUMP_FILE"
  fi
}
trap cleanup EXIT

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
    # Keep XXXXXX at the end: BSD/macOS mktemp otherwise creates the template
    # literally instead of replacing the placeholder.
    DUMP_FILE="$(mktemp "${TMPDIR:-/tmp}/contact_ops_db_${DATE}.sql.gz.XXXXXX")"
    TEMP_DOWNLOAD=1
    echo "Downloading from configured off-host backup..."
    if [ -n "$S3_ENDPOINT" ]; then
      "$AWS_BIN" s3 cp \
        "${REMOTE_URI%/}/contact_ops_db_${DATE}.sql.gz" \
        "$DUMP_FILE" \
        --endpoint-url "$S3_ENDPOINT"
    else
      "$AWS_BIN" s3 cp \
        "${REMOTE_URI%/}/contact_ops_db_${DATE}.sql.gz" \
        "$DUMP_FILE"
    fi
    ;;
  *)
    DUMP_FILE="$1"
    ;;
esac

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

resolve_compose_service() {
  local service="$1"
  docker ps \
    --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
    --filter "label=com.docker.compose.service=${service}" \
    --format '{{.Names}}' | head -1
}

if [ -z "$BACKEND_CONTAINER" ]; then
  BACKEND_CONTAINER="$(resolve_compose_service contact-ops-backend)"
  BACKEND_CONTAINER="${BACKEND_CONTAINER:-contact-ops-backend}"
fi

WRITER_ARRAY=()
if [ -n "$WRITER_CONTAINERS" ]; then
  # Operator override: whitespace-separated Docker container names.
  read -r -a WRITER_ARRAY <<< "$WRITER_CONTAINERS"
else
  # Default to the resolved backend and include each live asynchronous writer.
  # Compose labels avoid assuming generated container-name prefixes/suffixes.
  WRITER_ARRAY=("$BACKEND_CONTAINER")
  for service in contact-ops-celery-worker contact-ops-celery-beat; do
    writer="$(resolve_compose_service "$service")"
    [ -z "$writer" ] || WRITER_ARRAY+=("$writer")
  done
fi

echo "Stopping database writers so they cannot write during restore..."
# Stop schedulers/workers before the API (reverse of startup order), so no new
# work can be admitted while the maintenance gate closes.
writer_index=$((${#WRITER_ARRAY[@]} - 1))
while [ "$writer_index" -ge 0 ]; do
  docker stop "${WRITER_ARRAY[$writer_index]}"
  writer_index=$((writer_index - 1))
done

echo "Dropping and recreating $DB_NAME..."
docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME;"
docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"

echo "Restoring from dump..."
gunzip -c "$DUMP_FILE" | docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 --single-transaction -U "$DB_USER" -d "$DB_NAME"

echo "Starting database writers..."
for writer in "${WRITER_ARRAY[@]}"; do
  docker start "$writer"
done

sleep 6
echo "Health check..."
docker exec "$BACKEND_CONTAINER" curl -fsS http://localhost:8501/health || {
  echo "WARN: backend not responding"
  exit 3
}
echo "Restore complete."
