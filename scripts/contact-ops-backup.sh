#!/usr/bin/env bash
# Daily backup for the dedicated Contact-Ops PostgreSQL database.
# Optional off-host upload uses the standard AWS credential chain. This file
# contains no credentials; production configuration is operator-owned and
# stored outside the repository.
#
# Preferred S3-compatible keys (Garage / Lambda object storage):
#   GARAGE_ACCESS_KEY / GARAGE_SECRET_KEY
# These map onto AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY for the AWS CLI.
# Alternatives: AWS_PROFILE, or AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY.

set -euo pipefail
umask 077

CONFIG_FILE="${CONTACT_OPS_BACKUP_CONFIG:-${HOME}/.config/contact-ops/backup.env}"
if [ -f "$CONFIG_FILE" ]; then
  # shellcheck disable=SC1090
  . "$CONFIG_FILE"
fi

# Suite-standard Garage names → AWS CLI credential chain (never hardcode keys).
if [ -n "${GARAGE_ACCESS_KEY:-}" ]; then
  export AWS_ACCESS_KEY_ID="${GARAGE_ACCESS_KEY}"
fi
if [ -n "${GARAGE_SECRET_KEY:-}" ]; then
  export AWS_SECRET_ACCESS_KEY="${GARAGE_SECRET_KEY}"  # gitleaks:allow
fi
: "${AWS_DEFAULT_REGION:=${GARAGE_REGION:-us-east-3}}"
export AWS_DEFAULT_REGION

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DATE_TODAY="$(date +%Y%m%d)"
BACKUP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/contact-ops-backup-${TIMESTAMP}.XXXXXX")"
LOCAL_KEEP_DIR="${CONTACT_OPS_BACKUP_LOCAL_DIR:-${HOME}/backups/contact-ops}"
LOG="${CONTACT_OPS_BACKUP_LOG:-${HOME}/logs/contact-ops-backup.log}"
DB_CONTAINER="${CONTACT_OPS_DB_CONTAINER:-contact-ops-postgres}"
DB_USER="${CONTACT_OPS_DB_USER:-contact_ops_admin}"
DB_NAME="${CONTACT_OPS_DB_NAME:-contact_ops_db}"
ENV_SRC="${CONTACT_OPS_ENV_SRC:-/home/muut/contact-ops/.env}"
AGE_REC_FILE="${CONTACT_OPS_AGE_RECIPIENT_FILE:-${HOME}/.config/contact-ops/backup-age-recipient.txt}"
REMOTE_URI="${CONTACT_OPS_BACKUP_REMOTE_URI:-}"
S3_ENDPOINT="${CONTACT_OPS_BACKUP_S3_ENDPOINT:-}"
AWS_BIN="${CONTACT_OPS_AWS_BIN:-$(command -v aws 2>/dev/null || true)}"

mkdir -p "$LOCAL_KEEP_DIR" "$(dirname "$LOG")"
chmod 700 "$LOCAL_KEEP_DIR"
touch "$LOG"
chmod 600 "$LOG"
trap 'rm -rf "$BACKUP_DIR"' EXIT

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

# Fail closed when off-host upload is configured but no operator credentials
# are available. Does not print secret values — only which vars are missing.
require_offhost_credentials() {
  if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
    return 0
  fi
  if [ -n "${AWS_PROFILE:-}" ]; then
    return 0
  fi
  log "ERROR: off-host backup is configured (CONTACT_OPS_BACKUP_REMOTE_URI) but no credentials are set."
  log "ERROR: on the backup cron host, set GARAGE_ACCESS_KEY + GARAGE_SECRET_KEY"
  log "ERROR: (or AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY, or AWS_PROFILE) in ${CONFIG_FILE} (mode 0600)."
  log "ERROR: see deploy/contact-ops-backup.env.example — never commit live keys."
  return 1
}

upload_remote() {
  local source_file="$1"
  local remote_name="$2"

  # macOS ships Bash 3.2, where expanding an empty array under `set -u`
  # aborts the script. Keep the optional endpoint in explicit branches so the
  # standard AWS credential/endpoint chain works on every supported host.
  if [ -n "$S3_ENDPOINT" ]; then
    "$AWS_BIN" s3 cp "$source_file" \
      "${REMOTE_URI%/}/${remote_name}" \
      --endpoint-url "$S3_ENDPOINT" \
      2>>"$LOG"
  else
    "$AWS_BIN" s3 cp "$source_file" \
      "${REMOTE_URI%/}/${remote_name}" \
      2>>"$LOG"
  fi
}

log "=== Contact-Ops backup starting ==="

# Credential gate runs before any dump work so misconfigured cron fails closed
# with a clear message (and so dry-runs do not need a live database).
if [ -n "$REMOTE_URI" ]; then
  if [ -z "$AWS_BIN" ]; then
    log "ERROR: remote backup configured but AWS CLI is unavailable"
    exit 3
  fi
  require_offhost_credentials || exit 3
fi

log "Dumping ${DB_NAME} from ${DB_CONTAINER}..."
DUMP_FILE="${BACKUP_DIR}/contact_ops_db_${DATE_TODAY}.sql.gz"
docker exec "$DB_CONTAINER" pg_dump \
  -U "$DB_USER" \
  -d "$DB_NAME" \
  --verbose \
  --no-owner \
  --no-acl \
  --format=plain \
  2>>"$LOG" \
  | gzip > "$DUMP_FILE"

DUMP_BYTES="$(wc -c < "$DUMP_FILE" | tr -d ' ')"
if [ "$DUMP_BYTES" -lt 1024 ]; then
  log "ERROR: dump file is suspiciously small (${DUMP_BYTES} bytes); refusing to publish"
  exit 1
fi
if ! gzip -t "$DUMP_FILE" 2>/dev/null; then
  log "ERROR: dump gzip is corrupt; refusing to publish"
  exit 2
fi

install -m 600 "$DUMP_FILE" "$LOCAL_KEEP_DIR/"
log "Verified database backup preserved locally (${DUMP_BYTES} bytes)"

REMOTE_FAILED=0
if [ -n "$REMOTE_URI" ]; then
  # Credentials and AWS CLI were already validated above (fail closed).
  if upload_remote "$DUMP_FILE" "contact_ops_db_${DATE_TODAY}.sql.gz"; then
    log "Off-host database upload complete"
  else
    log "ERROR: off-host database upload failed; local copy preserved"
    REMOTE_FAILED=1
  fi
else
  log "Remote backup is not configured; preserving verified local copy only"
fi

# Encrypt the deploy environment with the deployment-specific public recipient.
# The matching private identity remains off-host in the operator's password manager.
if [ -f "$ENV_SRC" ] && [ -f "$AGE_REC_FILE" ] && command -v age >/dev/null 2>&1; then
  ENV_AGE="${BACKUP_DIR}/contact_ops_env_${DATE_TODAY}.env.age"
  if age -r "$(cat "$AGE_REC_FILE")" -o "$ENV_AGE" "$ENV_SRC" 2>>"$LOG"; then
    install -m 600 "$ENV_AGE" "$LOCAL_KEEP_DIR/"
    if [ -n "$REMOTE_URI" ] && [ -n "$AWS_BIN" ]; then
      if upload_remote "$ENV_AGE" "contact_ops_env_${DATE_TODAY}.env.age"; then
        log "Encrypted environment uploaded off-host"
      else
        log "ERROR: encrypted environment upload failed; local copy preserved"
        REMOTE_FAILED=1
      fi
    else
      log "Encrypted environment preserved locally; remote backup is not configured"
    fi
  else
    log "ERROR: age encryption of environment failed; database copy preserved"
    REMOTE_FAILED=1
  fi
else
  log "WARN: skipping environment backup (missing source, age recipient, or age binary)"
fi

log "Pruning local backups older than 30 days..."
find "$LOCAL_KEEP_DIR" -name 'contact_ops_db_*.sql.gz' -mtime +30 -delete -print 2>>"$LOG"
find "$LOCAL_KEEP_DIR" -name 'contact_ops_env_*.env.age' -mtime +30 -delete -print 2>>"$LOG"

log "=== Contact-Ops backup complete ==="
if [ "$REMOTE_FAILED" -ne 0 ]; then
  exit 4
fi
