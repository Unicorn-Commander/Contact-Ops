#!/bin/bash
# Daily backup for Contact-Ops dedicated Postgres (contact-ops-postgres)
# This is SEPARATE from /home/muut/UC-Cloud-production/scripts/backup-db.sh
# which backs up the shared unicorn-postgresql (does NOT include contact_ops_db).
#
# Run via cron: 0 3 * * * /home/muut/contact-ops/scripts/backup-contact-ops.sh
# Backup destination: Lambda S3 (matches existing UC-Cloud convention)
# Retention: 30 days local, indefinite remote (S3 lifecycle handles retention)

set -euo pipefail
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DATE_TODAY=$(date +%Y%m%d)
BACKUP_DIR="/tmp/contact-ops-backup-${TIMESTAMP}"
LOCAL_KEEP_DIR="${HOME}/backups/contact-ops"
LOG="${HOME}/logs/contact-ops-backup.log"

mkdir -p "$BACKUP_DIR"
mkdir -p "$LOCAL_KEEP_DIR"
mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

trap 'rm -rf "$BACKUP_DIR"' EXIT

log "=== Contact-Ops backup starting ==="

# 1. Dump contact_ops_db
log "Dumping contact_ops_db from contact-ops-postgres..."
DUMP_FILE="${BACKUP_DIR}/contact_ops_db_${DATE_TODAY}.sql.gz"
docker exec contact-ops-postgres pg_dump \
  -U contact_ops_admin \
  -d contact_ops_db \
  --verbose \
  --no-owner \
  --no-acl \
  --format=plain \
  2>>"$LOG" \
  | gzip > "$DUMP_FILE"

DUMP_SIZE=$(du -sh "$DUMP_FILE" | cut -f1)
log "Dump size: ${DUMP_SIZE}"

# 2. Verify dump is non-empty + non-corrupt
DUMP_BYTES=$(stat -c%s "$DUMP_FILE")
if [ "$DUMP_BYTES" -lt 1024 ]; then
  log "ERROR: dump file is suspiciously small (${DUMP_BYTES} bytes); refusing to upload"
  exit 1
fi
if ! gzip -t "$DUMP_FILE" 2>/dev/null; then
  log "ERROR: dump gzip corrupted; refusing to upload"
  exit 2
fi

# 3. Copy to local retention dir (30 days)
cp "$DUMP_FILE" "$LOCAL_KEEP_DIR/"
log "Copied to ${LOCAL_KEEP_DIR}/"

# 4. Upload to Lambda S3 (off-host)
log "Uploading to Lambda S3..."
export AWS_ACCESS_KEY_ID=7U0ST6S7Q86BUWWDX0CD
export AWS_SECRET_ACCESS_KEY="rl6nuYtyiyActOM0HX58mQAJCYqt/xkv9TSCpg3R"  # gitleaks:allow
export AWS_DEFAULT_REGION=us-east-3
S3_BUCKET="s3://9eb54b95-1ad1-4db4-8311-a6ae979b44da/contact-ops-backups"
S3_ENDPOINT="https://files.us-east-3.lambda.ai"
/home/muut/.local/bin/aws s3 cp "$DUMP_FILE" \
  "${S3_BUCKET}/contact_ops_db_${DATE_TODAY}.sql.gz" \
  --endpoint-url "$S3_ENDPOINT" \
  2>>"$LOG"

if [ $? -eq 0 ]; then
  log "Upload to Lambda S3 complete"
else
  log "WARN: upload failed; local copy preserved"
fi

# 4b. Encrypt + back up the deploy .env (the server secrets needed for a
# from-scratch restore). age PUBLIC-KEY encryption: only the recipient (public
# key) lives on this box, so the encrypted .env can ride to S3 safely; DECRYPTING
# requires the matching private key, which is held OFF-box in the operator's
# password manager (Vaultwarden) — see docs/OPERATIONS.md. One key per deployment.
ENV_SRC="/home/muut/contact-ops/.env"
AGE_REC_FILE="${HOME}/.config/contact-ops/backup-age-recipient.txt"
if [ -f "$ENV_SRC" ] && [ -f "$AGE_REC_FILE" ] && command -v age >/dev/null 2>&1; then
  ENV_AGE="${BACKUP_DIR}/contact_ops_env_${DATE_TODAY}.env.age"
  if age -r "$(cat "$AGE_REC_FILE")" -o "$ENV_AGE" "$ENV_SRC" 2>>"$LOG"; then
    cp "$ENV_AGE" "$LOCAL_KEEP_DIR/"
    if /home/muut/.local/bin/aws s3 cp "$ENV_AGE" \
        "${S3_BUCKET}/contact_ops_env_${DATE_TODAY}.env.age" \
        --endpoint-url "$S3_ENDPOINT" 2>>"$LOG"; then
      log "Encrypted .env uploaded to S3"
    else
      log "WARN: encrypted .env upload failed; local copy preserved"
    fi
  else
    log "WARN: age encryption of .env failed"
  fi
else
  log "WARN: skipping .env backup (missing .env, age recipient, or age binary)"
fi

# 5. Prune local backups older than 30 days (DB dumps + encrypted env snapshots)
log "Pruning local backups older than 30 days..."
find "$LOCAL_KEEP_DIR" -name 'contact_ops_db_*.sql.gz' -mtime +30 -delete -print 2>>"$LOG"
find "$LOCAL_KEEP_DIR" -name 'contact_ops_env_*.env.age' -mtime +30 -delete -print 2>>"$LOG"

log "=== Contact-Ops backup complete ==="
