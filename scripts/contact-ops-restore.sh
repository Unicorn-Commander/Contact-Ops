#!/bin/bash
# Contact-Ops Postgres restore — recovery runbook driver
#
# Usage:
#   ./contact-ops-restore.sh /path/to/backup.sql.gz
#   ./contact-ops-restore.sh --latest        # most recent local
#   ./contact-ops-restore.sh --from-lambda DATE  # pull from Lambda S3
#
# DESTRUCTIVE — drops + recreates contact_ops_db. Requires typed confirmation.

set -euo pipefail

LOCAL_KEEP_DIR="${HOME}/backups/contact-ops"
DB_USER="contact_ops_admin"
DB_NAME="contact_ops_db"
CONTAINER="contact-ops-postgres"

print_usage() {
  cat <<'EOF'
Usage:
  ./contact-ops-restore.sh PATH_TO_DUMP.sql.gz
  ./contact-ops-restore.sh --latest
  ./contact-ops-restore.sh --from-lambda YYYYMMDD

Options:
  --latest             Restore from the most recent local backup
  --from-lambda DATE   Download from Lambda S3 first, then restore
  -h, --help           This help

WARNING: drops and recreates contact_ops_db. The backend MUST be stopped
before running this. Requires typed confirmation.
EOF
}

if [ $# -lt 1 ]; then print_usage; exit 1; fi

DUMP_FILE=""

case "$1" in
  -h|--help)
    print_usage; exit 0 ;;
  --latest)
    DUMP_FILE=$(ls -t "$LOCAL_KEEP_DIR"/contact_ops_db_*.sql.gz 2>/dev/null | head -1)
    [ -z "$DUMP_FILE" ] && { echo "no local backups in $LOCAL_KEEP_DIR"; exit 1; } ;;
  --from-lambda)
    DATE="$2"
    DUMP_FILE="/tmp/contact_ops_db_${DATE}.sql.gz"
    echo "Downloading from Lambda S3..."
    export AWS_ACCESS_KEY_ID=7U0ST6S7Q86BUWWDX0CD
    export AWS_SECRET_ACCESS_KEY="rl6nuYtyiyActOM0HX58mQAJCYqt/xkv9TSCpg3R"  # gitleaks:allow
    export AWS_DEFAULT_REGION=us-east-3
    aws s3 cp \
      "s3://9eb54b95-1ad1-4db4-8311-a6ae979b44da/contact-ops-backups/contact_ops_db_${DATE}.sql.gz" \
      "$DUMP_FILE" \
      --endpoint-url https://files.us-east-3.lambda.ai ;;
  *) DUMP_FILE="$1" ;;
esac

if [ ! -f "$DUMP_FILE" ]; then
  echo "ERROR: dump file not found: $DUMP_FILE"; exit 1
fi

echo "Restore source: $DUMP_FILE ($(du -sh "$DUMP_FILE" | cut -f1))"
echo
echo "WARNING: this will DROP and recreate $DB_NAME on container $CONTAINER."
echo "Type 'restore contact-ops' to confirm:"
read -r CONFIRM
if [ "$CONFIRM" != "restore contact-ops" ]; then
  echo "Aborted."
  exit 0
fi

echo "Stopping contact-ops-backend so it can't write during restore..."
docker stop contact-ops-backend || true

echo "Dropping + recreating $DB_NAME..."
docker exec -i "$CONTAINER" psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME;"
docker exec -i "$CONTAINER" psql -U "$DB_USER" -d postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"

echo "Restoring from dump..."
gunzip -c "$DUMP_FILE" | docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME"

echo "Starting contact-ops-backend..."
docker start contact-ops-backend

sleep 6
echo "Health check..."
docker exec contact-ops-backend curl -fsS http://localhost:8501/health || {
  echo "WARN: backend not responding"; exit 3
}
echo "Restore complete."
