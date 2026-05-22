#!/usr/bin/env bash
#
# keycloak_bootstrap.sh — idempotent Contact-Ops Keycloak client bootstrap
#
# Creates / updates five OIDC clients in the uchub realm:
#   - contact-ops-mcp           (bearer-only, the MCP server)
#   - contact-ops-app           (public, PKCE, future human UI)
#   - contact-ops-carddav       (confidential, direct grants, CardDAV adapter)
#   - contact-ops-publisher     (confidential, service account + token exchange, outbound federation)
#   - contact-ops-bridge-inbound (confidential, service account, inbound from Data Intel)
#
# Plus: Phase 0 client scopes, realm role ladder (CLIENT/STAFF/MANAGER/ADMIN),
# protocol mappers (tenant_id, tenant_slug, tenant_hipaa), and the RFC 8693
# token-exchange permission matrix per docs/KEYCLOAK_CLIENTS.md §6.
#
# Safe to re-run. See docs/KEYCLOAK_CLIENTS.md §10 for the idempotency contract.
#
# Usage:
#   KC_SERVER_URL=https://auth.unicorncommander.ai \
#   KC_REALM=uchub \
#   KC_ADMIN_USER=admin \
#   KC_ADMIN_PASS='...' \
#     bash scripts/keycloak_bootstrap.sh [--dry-run] [--rotate-secrets]
#
# --dry-run        Print every kcadm.sh command without executing.
# --rotate-secrets Force rotation of confidential-client secrets even if the client already exists.
#
# Exit codes:
#   0   success
#   1   missing required env var
#   2   user-attributes client scope missing (per reference_keycloak_user_attributes.md)
#   3   kcadm.sh not on PATH and no container fallback usable
#   4   authentication failed
#   5   client operation failed
#
set -euo pipefail

# ----------------------------------------------------------------------------
# Required environment
# ----------------------------------------------------------------------------
: "${KC_SERVER_URL:?KC_SERVER_URL is required (e.g. https://auth.unicorncommander.ai)}"
: "${KC_REALM:?KC_REALM is required (e.g. uchub)}"
: "${KC_ADMIN_USER:?KC_ADMIN_USER is required}"
: "${KC_ADMIN_PASS:?KC_ADMIN_PASS is required (export via env, never via flag)}"

# Optional config
KC_CONTAINER="${KC_CONTAINER:-uchub-keycloak}"
DRY_RUN=0
ROTATE_SECRETS=0

# Where Contact-Ops lives in DNS — used for redirect URIs + web origins
APP_HOST="${APP_HOST:-contacts.magicunicorn.dev}"
MCP_HOST="${MCP_HOST:-mcp.contacts.magicunicorn.dev}"
CARDDAV_HOST="${CARDDAV_HOST:-carddav.contacts.magicunicorn.dev}"
DEV_HOST="${DEV_HOST:-http://localhost:5173}"

# ----------------------------------------------------------------------------
# Flag parsing
# ----------------------------------------------------------------------------
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --rotate-secrets) ROTATE_SECRETS=1 ;;
    -h|--help)
      sed -n '2,30p' "$0"
      exit 0
      ;;
    *) echo "unknown flag: $arg" >&2; exit 1 ;;
  esac
done

# ----------------------------------------------------------------------------
# Logging helpers
# ----------------------------------------------------------------------------
log() { printf '[bootstrap] %s\n' "$*" >&2; }
warn() { printf '[bootstrap WARN] %s\n' "$*" >&2; }
die() { printf '[bootstrap ERROR] %s\n' "$*" >&2; exit "${2:-5}"; }

# ----------------------------------------------------------------------------
# kcadm.sh resolution — local binary first, then docker exec into uchub-keycloak
# ----------------------------------------------------------------------------
if command -v kcadm.sh >/dev/null 2>&1; then
  KCADM_BIN="kcadm.sh"
  KCADM_MODE="local"
elif command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' | grep -qx "$KC_CONTAINER"; then
  KCADM_BIN="docker exec -i $KC_CONTAINER /opt/keycloak/bin/kcadm.sh"
  KCADM_MODE="docker:$KC_CONTAINER"
else
  die "Cannot find kcadm.sh on PATH and no Docker container '$KC_CONTAINER' running. Install Keycloak admin CLI or set KC_CONTAINER." 3
fi
log "kcadm via: $KCADM_MODE"

# Wrapper — prints if dry-run, executes otherwise
kc() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '+ %s' "$KCADM_BIN" >&2
    for a in "$@"; do printf ' %q' "$a" >&2; done
    printf '\n' >&2
    return 0
  fi
  # shellcheck disable=SC2086
  $KCADM_BIN "$@"
}

# Same wrapper but captures stdout (for queries)
kc_capture() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '+ %s' "$KCADM_BIN" >&2
    for a in "$@"; do printf ' %q' "$a" >&2; done
    printf ' (capture)\n' >&2
    echo ""
    return 0
  fi
  # shellcheck disable=SC2086
  $KCADM_BIN "$@"
}

# ----------------------------------------------------------------------------
# Authenticate to Keycloak master realm
# ----------------------------------------------------------------------------
log "Authenticating against $KC_SERVER_URL as $KC_ADMIN_USER ..."
if [[ $DRY_RUN -eq 0 ]]; then
  if ! $KCADM_BIN config credentials \
      --server "$KC_SERVER_URL" \
      --realm master \
      --user "$KC_ADMIN_USER" \
      --password "$KC_ADMIN_PASS" >/dev/null 2>&1; then
    die "kcadm.sh credentials failed — check KC_ADMIN_USER / KC_ADMIN_PASS against $KC_SERVER_URL/admin" 4
  fi
fi

# ----------------------------------------------------------------------------
# Realm prerequisites — verify user-attributes scope exists
# ----------------------------------------------------------------------------
log "Verifying realm prerequisites in $KC_REALM ..."
if [[ $DRY_RUN -eq 0 ]]; then
  ua_scope_id="$($KCADM_BIN get client-scopes -r "$KC_REALM" --fields id,name --format csv --noquotes 2>/dev/null \
    | awk -F, '$2 == "user-attributes" { print $1 }' \
    || true)"
  if [[ -z "${ua_scope_id:-}" ]]; then
    die "Realm $KC_REALM is missing the 'user-attributes' client scope. Create it first per reference_keycloak_user_attributes.md before running this bootstrap." 2
  fi
  log "  user-attributes scope: $ua_scope_id"
fi

# ----------------------------------------------------------------------------
# Realm role ladder — CLIENT / STAFF / MANAGER / ADMIN
# ----------------------------------------------------------------------------
ensure_realm_role() {
  local role="$1"
  local description="$2"
  if [[ $DRY_RUN -eq 1 ]]; then
    log "would ensure realm role: $role"
    return 0
  fi
  if $KCADM_BIN get "roles/$role" -r "$KC_REALM" >/dev/null 2>&1; then
    log "  role exists: $role"
  else
    log "  creating role: $role"
    $KCADM_BIN create roles -r "$KC_REALM" \
      -s "name=$role" \
      -s "description=$description" >/dev/null
  fi
}

log "Ensuring realm role ladder ..."
ensure_realm_role "CONTACT_OPS_CLIENT" "External read-only access on shared records"
ensure_realm_role "CONTACT_OPS_STAFF"  "Write within tenant; propose-anywhere"
ensure_realm_role "CONTACT_OPS_MANAGER" "Merge, ACL, tenant settings, approve high-stakes"
ensure_realm_role "CONTACT_OPS_ADMIN"  "Delete, agent trust tiers, cross-tenant, glass-break"

# ----------------------------------------------------------------------------
# Phase 0 client scopes
# ----------------------------------------------------------------------------
PHASE0_SCOPES=(
  "person:read"
  "person:write"
  "person:propose_merge"
  "person:apply_merge"
  "person:bulk"
  "org:read"
  "org:write"
  "org:bulk"
  "field:propose_set"
  "field:apply_set"
  "relationship:propose"
  "relationship:apply"
  "tag:apply"
  "voice:match"
  "voice:assign"
  "agent:register"
  "admin:tenant"
  "carddav:sync"
  "data-intel:catalogue:submit"
  "data-intel:catalogue:search"
)

ensure_client_scope() {
  local scope_name="$1"
  if [[ $DRY_RUN -eq 1 ]]; then
    log "would ensure scope: $scope_name"
    return 0
  fi
  local existing_id
  existing_id="$($KCADM_BIN get client-scopes -r "$KC_REALM" --fields id,name --format csv --noquotes 2>/dev/null \
    | awk -F, -v n="$scope_name" '$2 == n { print $1 }' || true)"
  if [[ -n "$existing_id" ]]; then
    log "  scope exists: $scope_name ($existing_id)"
    return 0
  fi
  log "  creating scope: $scope_name"
  $KCADM_BIN create client-scopes -r "$KC_REALM" \
    -s "name=$scope_name" \
    -s "protocol=openid-connect" \
    -s "attributes.\"include.in.token.scope\"=true" \
    -s "attributes.\"display.on.consent.screen\"=false" \
    -s "description=Contact-Ops Phase 0 scope: $scope_name" >/dev/null
}

log "Ensuring Phase 0 client scopes ..."
for s in "${PHASE0_SCOPES[@]}"; do
  ensure_client_scope "$s"
done

# ----------------------------------------------------------------------------
# Helpers for client management
# ----------------------------------------------------------------------------
get_client_id() {
  # Echo the Keycloak internal ID for a clientId, or empty if missing.
  local cid="$1"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "dry-run-id-$cid"
    return 0
  fi
  $KCADM_BIN get clients -r "$KC_REALM" -q "clientId=$cid" --fields id --format csv --noquotes 2>/dev/null \
    | tr -d '\r' | head -n1
}

get_scope_id() {
  local scope_name="$1"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "dry-run-scope-id-$scope_name"
    return 0
  fi
  $KCADM_BIN get client-scopes -r "$KC_REALM" --fields id,name --format csv --noquotes 2>/dev/null \
    | awk -F, -v n="$scope_name" '$2 == n { print $1 }'
}

assign_scope() {
  # Assign a scope to a client as default or optional.
  local client_id="$1"
  local scope_id="$2"
  local kind="$3"  # default | optional
  if [[ $DRY_RUN -eq 1 ]]; then
    log "would assign $kind scope $scope_id to client $client_id"
    return 0
  fi
  # Idempotent: kcadm returns 204 if already assigned, no error.
  $KCADM_BIN update "clients/$client_id/${kind}-client-scopes/$scope_id" -r "$KC_REALM" >/dev/null 2>&1 \
    || warn "scope $scope_id ($kind) assignment to $client_id returned non-zero (likely already assigned)"
}

ensure_mapper_for_client() {
  # Add a protocol mapper to a client if not already present (by name).
  local client_id="$1"
  local mapper_name="$2"
  local mapper_json_file="$3"
  if [[ $DRY_RUN -eq 1 ]]; then
    log "would ensure mapper $mapper_name on client $client_id"
    return 0
  fi
  local existing
  existing="$($KCADM_BIN get "clients/$client_id/protocol-mappers/models" -r "$KC_REALM" --fields id,name --format csv --noquotes 2>/dev/null \
    | awk -F, -v n="$mapper_name" '$2 == n { print $1 }' || true)"
  if [[ -n "$existing" ]]; then
    log "  mapper exists: $mapper_name ($existing)"
    return 0
  fi
  log "  creating mapper: $mapper_name"
  $KCADM_BIN create "clients/$client_id/protocol-mappers/models" -r "$KC_REALM" -f "$mapper_json_file" >/dev/null
}

# ----------------------------------------------------------------------------
# Mapper templates — written to a temp dir, fed into ensure_mapper_for_client.
# ----------------------------------------------------------------------------
TMP_DIR="$(mktemp -d -t contact-ops-keycloak-XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT

write_tenant_user_attr_mapper() {
  # $1 = attribute name (e.g. tenant_id), $2 = claim name, $3 = json type, $4 = output file
  local attr="$1" claim="$2" jtype="$3" out="$4"
  cat >"$out" <<JSON
{
  "name": "$claim",
  "protocol": "openid-connect",
  "protocolMapper": "oidc-usermodel-attribute-mapper",
  "consentRequired": false,
  "config": {
    "user.attribute": "$attr",
    "claim.name": "$claim",
    "jsonType.label": "$jtype",
    "id.token.claim": "true",
    "access.token.claim": "true",
    "userinfo.token.claim": "true",
    "multivalued": "false",
    "aggregate.attrs": "false"
  }
}
JSON
}

write_audience_mapper() {
  # $1 = client_id (target audience), $2 = output file
  local target="$1" out="$2"
  cat >"$out" <<JSON
{
  "name": "aud-$target",
  "protocol": "openid-connect",
  "protocolMapper": "oidc-audience-mapper",
  "consentRequired": false,
  "config": {
    "included.client.audience": "$target",
    "id.token.claim": "false",
    "access.token.claim": "true"
  }
}
JSON
}

write_hardcoded_claim_mapper() {
  # $1 = claim name, $2 = claim value, $3 = json type, $4 = output file
  local cname="$1" cval="$2" jtype="$3" out="$4"
  cat >"$out" <<JSON
{
  "name": "hardcoded-$cname",
  "protocol": "openid-connect",
  "protocolMapper": "oidc-hardcoded-claim-mapper",
  "consentRequired": false,
  "config": {
    "claim.name": "$cname",
    "claim.value": "$cval",
    "jsonType.label": "$jtype",
    "id.token.claim": "true",
    "access.token.claim": "true",
    "userinfo.token.claim": "true"
  }
}
JSON
}

# Pre-generate the tenant mappers — they're identical across clients.
write_tenant_user_attr_mapper "tenant_id" "tenant_id" "String" "$TMP_DIR/mapper_tenant_id.json"
write_tenant_user_attr_mapper "tenant_slug" "tenant_slug" "String" "$TMP_DIR/mapper_tenant_slug.json"
write_tenant_user_attr_mapper "tenant_hipaa" "tenant_hipaa" "boolean" "$TMP_DIR/mapper_tenant_hipaa.json"

# ----------------------------------------------------------------------------
# Helper: write a client representation JSON for create/update
# ----------------------------------------------------------------------------
write_client_json() {
  local out="$1" cid="$2" name="$3" public="$4" bearer="$5" standard="$6" direct="$7" service="$8" root="$9"
  shift 9
  # Remaining args: redirect URIs (comma-separated), web origins (comma-separated)
  local redirects="${1:-}"
  local origins="${2:-}"

  local redirect_json="[]"
  if [[ -n "$redirects" ]]; then
    redirect_json="[$(echo "$redirects" | awk -F, '{ for(i=1;i<=NF;i++){ printf "%s\"%s\"", (i>1?",":""), $i } }')]"
  fi
  local origin_json="[]"
  if [[ -n "$origins" ]]; then
    origin_json="[$(echo "$origins" | awk -F, '{ for(i=1;i<=NF;i++){ printf "%s\"%s\"", (i>1?",":""), $i } }')]"
  fi

  cat >"$out" <<JSON
{
  "clientId": "$cid",
  "name": "$name",
  "description": "Contact-Ops Phase 0 client. See docs/KEYCLOAK_CLIENTS.md.",
  "enabled": true,
  "publicClient": $public,
  "bearerOnly": $bearer,
  "standardFlowEnabled": $standard,
  "implicitFlowEnabled": false,
  "directAccessGrantsEnabled": $direct,
  "serviceAccountsEnabled": $service,
  "authorizationServicesEnabled": false,
  "rootUrl": "$root",
  "baseUrl": "/",
  "redirectUris": $redirect_json,
  "webOrigins": $origin_json,
  "attributes": {
    "pkce.code.challenge.method": "S256",
    "use.refresh.tokens": "true",
    "access.token.lifespan": "300",
    "client_credentials.use_refresh_token": "false"
  },
  "fullScopeAllowed": false,
  "protocol": "openid-connect"
}
JSON
}

# Generic client upsert
upsert_client() {
  local cid="$1" json_file="$2"
  local existing_id
  existing_id="$(get_client_id "$cid")"
  if [[ -n "$existing_id" && "$existing_id" != "dry-run-id-$cid" ]]; then
    log "  updating client: $cid ($existing_id)"
    if [[ $ROTATE_SECRETS -eq 1 && $DRY_RUN -eq 0 ]]; then
      log "    rotating client secret"
      $KCADM_BIN create "clients/$existing_id/client-secret" -r "$KC_REALM" >/dev/null
    fi
    kc update "clients/$existing_id" -r "$KC_REALM" -f "$json_file" >/dev/null
    echo "$existing_id"
  else
    log "  creating client: $cid"
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "dry-run-id-$cid"
      kc create clients -r "$KC_REALM" -f "$json_file" >/dev/null
      return 0
    fi
    $KCADM_BIN create clients -r "$KC_REALM" -f "$json_file" >/dev/null
    get_client_id "$cid"
  fi
}

# Helper: attach the user-attributes scope (uc_uid carrier) as a default scope
attach_user_attributes_scope() {
  local client_id="$1"
  if [[ $DRY_RUN -eq 1 ]]; then
    log "would attach user-attributes scope to $client_id"
    return 0
  fi
  local ua_id
  ua_id="$(get_scope_id "user-attributes")"
  if [[ -z "$ua_id" ]]; then
    warn "user-attributes scope not found at attach time — skipping (this should never happen given prereq check)"
    return 0
  fi
  $KCADM_BIN update "clients/$client_id/default-client-scopes/$ua_id" -r "$KC_REALM" >/dev/null 2>&1 \
    || warn "user-attributes attach to $client_id returned non-zero (likely already attached)"
}

# Helper: set service-account user attrs (firstName / lastName per VERIFY_PROFILE trap)
set_service_account_profile() {
  local client_id="$1" first="$2" last="$3"
  if [[ $DRY_RUN -eq 1 ]]; then
    log "would set service-account profile on $client_id: $first $last"
    return 0
  fi
  local sa_user_id
  sa_user_id="$($KCADM_BIN get "clients/$client_id/service-account-user" -r "$KC_REALM" --fields id --format csv --noquotes 2>/dev/null | tr -d '\r')"
  if [[ -z "$sa_user_id" ]]; then
    warn "service-account-user not found for $client_id — service accounts may be disabled"
    return 0
  fi
  $KCADM_BIN update "users/$sa_user_id" -r "$KC_REALM" \
    -s "firstName=$first" \
    -s "lastName=$last" \
    -s 'requiredActions=[]' >/dev/null \
    || warn "service-account profile update for $client_id returned non-zero"
}

# ----------------------------------------------------------------------------
# Client 1: contact-ops-mcp — bearer-only, the MCP server
# ----------------------------------------------------------------------------
log ""
log "=== Client 1: contact-ops-mcp ==="
write_client_json "$TMP_DIR/client_mcp.json" \
  "contact-ops-mcp" \
  "Contact-Ops MCP Server" \
  "false"  "true"   "false"  "false"  "true"   "https://$MCP_HOST" \
  "" \
  ""

MCP_ID="$(upsert_client "contact-ops-mcp" "$TMP_DIR/client_mcp.json" || true)"
log "  internal id: $MCP_ID"

attach_user_attributes_scope "$MCP_ID"

# Tenant claim mappers
ensure_mapper_for_client "$MCP_ID" "tenant_id" "$TMP_DIR/mapper_tenant_id.json"
ensure_mapper_for_client "$MCP_ID" "tenant_slug" "$TMP_DIR/mapper_tenant_slug.json"
ensure_mapper_for_client "$MCP_ID" "tenant_hipaa" "$TMP_DIR/mapper_tenant_hipaa.json"

# Audience mapper (self)
write_audience_mapper "contact-ops-mcp" "$TMP_DIR/mapper_aud_mcp.json"
ensure_mapper_for_client "$MCP_ID" "aud-contact-ops-mcp" "$TMP_DIR/mapper_aud_mcp.json"

# Service-account profile for VERIFY_PROFILE safety
set_service_account_profile "$MCP_ID" "Contact-Ops" "MCP-Server"

# Optional scopes — every Phase 0 scope is optional on the MCP client
for s in "${PHASE0_SCOPES[@]}"; do
  sid="$(get_scope_id "$s")"
  [[ -n "$sid" ]] && assign_scope "$MCP_ID" "$sid" "optional"
done

# ----------------------------------------------------------------------------
# Client 2: contact-ops-app — public, PKCE, future human UI
# ----------------------------------------------------------------------------
log ""
log "=== Client 2: contact-ops-app ==="
write_client_json "$TMP_DIR/client_app.json" \
  "contact-ops-app" \
  "Contact-Ops Web App" \
  "true"   "false"  "true"   "false"  "false"  "https://$APP_HOST" \
  "https://$APP_HOST/auth/callback,https://$APP_HOST/auth/silent-renew,$DEV_HOST/auth/callback,$DEV_HOST/auth/silent-renew" \
  "https://$APP_HOST,$DEV_HOST,+"

APP_ID="$(upsert_client "contact-ops-app" "$TMP_DIR/client_app.json" || true)"
log "  internal id: $APP_ID"

attach_user_attributes_scope "$APP_ID"
ensure_mapper_for_client "$APP_ID" "tenant_id" "$TMP_DIR/mapper_tenant_id.json"
ensure_mapper_for_client "$APP_ID" "tenant_slug" "$TMP_DIR/mapper_tenant_slug.json"
ensure_mapper_for_client "$APP_ID" "tenant_hipaa" "$TMP_DIR/mapper_tenant_hipaa.json"

write_audience_mapper "contact-ops-app" "$TMP_DIR/mapper_aud_app.json"
ensure_mapper_for_client "$APP_ID" "aud-contact-ops-app" "$TMP_DIR/mapper_aud_app.json"

# App-specific optional scopes
APP_SCOPES=(person:read person:write org:read org:write relationship:propose relationship:apply tag:apply voice:match field:propose_set field:apply_set carddav:sync)
for s in "${APP_SCOPES[@]}"; do
  sid="$(get_scope_id "$s")"
  [[ -n "$sid" ]] && assign_scope "$APP_ID" "$sid" "optional"
done

# offline_access on the app — required for long-lived sessions
if [[ $DRY_RUN -eq 0 ]]; then
  off_id="$($KCADM_BIN get client-scopes -r "$KC_REALM" --fields id,name --format csv --noquotes 2>/dev/null | awk -F, '$2 == "offline_access" { print $1 }' || true)"
  if [[ -n "$off_id" ]]; then
    assign_scope "$APP_ID" "$off_id" "default"
  fi
fi

# ----------------------------------------------------------------------------
# Client 3: contact-ops-carddav — confidential, direct grants
# ----------------------------------------------------------------------------
log ""
log "=== Client 3: contact-ops-carddav ==="
write_client_json "$TMP_DIR/client_carddav.json" \
  "contact-ops-carddav" \
  "Contact-Ops CardDAV" \
  "false"  "false"  "false"  "true"   "true"   "https://$CARDDAV_HOST" \
  "" \
  ""

CARDDAV_ID="$(upsert_client "contact-ops-carddav" "$TMP_DIR/client_carddav.json" || true)"
log "  internal id: $CARDDAV_ID"

attach_user_attributes_scope "$CARDDAV_ID"
ensure_mapper_for_client "$CARDDAV_ID" "tenant_id" "$TMP_DIR/mapper_tenant_id.json"
ensure_mapper_for_client "$CARDDAV_ID" "tenant_slug" "$TMP_DIR/mapper_tenant_slug.json"
ensure_mapper_for_client "$CARDDAV_ID" "tenant_hipaa" "$TMP_DIR/mapper_tenant_hipaa.json"

write_audience_mapper "contact-ops-carddav" "$TMP_DIR/mapper_aud_carddav.json"
ensure_mapper_for_client "$CARDDAV_ID" "aud-contact-ops-carddav" "$TMP_DIR/mapper_aud_carddav.json"

# device_id mapper — for per-device app-password revocation
write_tenant_user_attr_mapper "device_id" "device_id" "String" "$TMP_DIR/mapper_device_id.json"
ensure_mapper_for_client "$CARDDAV_ID" "device_id" "$TMP_DIR/mapper_device_id.json"

# CardDAV scopes
CARDDAV_SCOPES=(carddav:sync person:read person:write field:propose_set)
for s in "${CARDDAV_SCOPES[@]}"; do
  sid="$(get_scope_id "$s")"
  [[ -n "$sid" ]] && assign_scope "$CARDDAV_ID" "$sid" "default"
done

set_service_account_profile "$CARDDAV_ID" "Contact-Ops" "CardDAV"

# ----------------------------------------------------------------------------
# Client 4: contact-ops-publisher — outbound federation
# ----------------------------------------------------------------------------
log ""
log "=== Client 4: contact-ops-publisher ==="
write_client_json "$TMP_DIR/client_publisher.json" \
  "contact-ops-publisher" \
  "Contact-Ops Publisher (outbound federation)" \
  "false"  "false"  "false"  "false"  "true"   "https://$MCP_HOST" \
  "" \
  ""

# Patch in longer access-token lifetime for publisher (outbound stalls).
# We rewrite the JSON in-place to set access.token.lifespan=600 for this client only.
python3 - "$TMP_DIR/client_publisher.json" <<'PY' 2>/dev/null || true
import json, sys
p = sys.argv[1]
with open(p) as f: doc = json.load(f)
doc.setdefault("attributes", {})["access.token.lifespan"] = "600"
with open(p, "w") as f: json.dump(doc, f, indent=2)
PY

PUBLISHER_ID="$(upsert_client "contact-ops-publisher" "$TMP_DIR/client_publisher.json" || true)"
log "  internal id: $PUBLISHER_ID"

attach_user_attributes_scope "$PUBLISHER_ID"

# Audience mappers — emits BOTH verify.centerdeep.online and contact-ops-mcp
write_audience_mapper "contact-ops-mcp" "$TMP_DIR/mapper_aud_pub_mcp.json"
ensure_mapper_for_client "$PUBLISHER_ID" "aud-contact-ops-mcp" "$TMP_DIR/mapper_aud_pub_mcp.json"

# Hardcoded audience for verify.centerdeep.online (no client of that exact name in uchub realm; use hardcoded mapper)
cat >"$TMP_DIR/mapper_aud_verify.json" <<JSON
{
  "name": "aud-verify-centerdeep",
  "protocol": "openid-connect",
  "protocolMapper": "oidc-audience-mapper",
  "consentRequired": false,
  "config": {
    "included.custom.audience": "https://verify.centerdeep.online",
    "id.token.claim": "false",
    "access.token.claim": "true"
  }
}
JSON
ensure_mapper_for_client "$PUBLISHER_ID" "aud-verify-centerdeep" "$TMP_DIR/mapper_aud_verify.json"

# Publisher scopes
PUBLISHER_SCOPES=(data-intel:catalogue:submit data-intel:catalogue:search field:propose_set person:read org:read)
for s in "${PUBLISHER_SCOPES[@]}"; do
  sid="$(get_scope_id "$s")"
  [[ -n "$sid" ]] && assign_scope "$PUBLISHER_ID" "$sid" "default"
done

set_service_account_profile "$PUBLISHER_ID" "Contact-Ops" "Publisher"

# ----------------------------------------------------------------------------
# Client 5: contact-ops-bridge-inbound — inbound federation
# ----------------------------------------------------------------------------
log ""
log "=== Client 5: contact-ops-bridge-inbound ==="
write_client_json "$TMP_DIR/client_bridge.json" \
  "contact-ops-bridge-inbound" \
  "Contact-Ops Bridge (inbound from Data Intel)" \
  "false"  "false"  "false"  "false"  "true"   "https://$MCP_HOST" \
  "" \
  ""

python3 - "$TMP_DIR/client_bridge.json" <<'PY' 2>/dev/null || true
import json, sys
p = sys.argv[1]
with open(p) as f: doc = json.load(f)
doc.setdefault("attributes", {})["access.token.lifespan"] = "600"
with open(p, "w") as f: json.dump(doc, f, indent=2)
PY

BRIDGE_ID="$(upsert_client "contact-ops-bridge-inbound" "$TMP_DIR/client_bridge.json" || true)"
log "  internal id: $BRIDGE_ID"

attach_user_attributes_scope "$BRIDGE_ID"

# Audience: tokens minted (or exchanged) here carry aud=contact-ops-mcp
write_audience_mapper "contact-ops-mcp" "$TMP_DIR/mapper_aud_bridge_mcp.json"
ensure_mapper_for_client "$BRIDGE_ID" "aud-contact-ops-mcp" "$TMP_DIR/mapper_aud_bridge_mcp.json"

# Bridge scopes
BRIDGE_SCOPES=(field:propose_set relationship:propose person:read org:read tag:apply voice:match)
for s in "${BRIDGE_SCOPES[@]}"; do
  sid="$(get_scope_id "$s")"
  [[ -n "$sid" ]] && assign_scope "$BRIDGE_ID" "$sid" "default"
done

set_service_account_profile "$BRIDGE_ID" "Contact-Ops" "Bridge-Inbound"

# ----------------------------------------------------------------------------
# Token-exchange permissions (RFC 8693)
# ----------------------------------------------------------------------------
log ""
log "=== Token-exchange permissions ==="
log "Note: token-exchange permissions in Keycloak 26.x require enabling the feature flag"
log "      'token-exchange' at server-start. If KC_FEATURE_TOKEN_EXCHANGE is unset/false,"
log "      the permission grants below will fail silently and you'll need to enable"
log "      the feature and re-run. Verify with: kcadm.sh get serverinfo --fields features"

# Helper: grant client A the right to exchange tokens for client B (target).
# In Keycloak 26.x, this is done via Authorization permissions on the target client.
# The bootstrap script declares intent; if the feature is disabled, the calls no-op.
grant_token_exchange() {
  local source_cid="$1" target_cid="$2"
  if [[ $DRY_RUN -eq 1 ]]; then
    log "would grant token-exchange: $source_cid -> $target_cid"
    return 0
  fi
  local source_id target_id
  source_id="$(get_client_id "$source_cid")"
  target_id="$(get_client_id "$target_cid")"
  if [[ -z "$source_id" || -z "$target_id" ]]; then
    log "  skip token-exchange grant $source_cid -> $target_cid (client missing — likely peer not yet onboarded)"
    return 0
  fi
  # Enable Authorization Services momentarily on the target to add the permission, then leave enabled.
  # Keycloak 26.x supports the token-exchange permission via the admin REST endpoint:
  #   PUT /admin/realms/{realm}/clients/{target}/management/permissions { "enabled": true }
  # then POST a policy + permission on the target's authorization server.
  # For Phase 0, we emit a TODO + best-effort attempt. Most real wiring happens via the Keycloak UI
  # once per pair; the script makes the call so a re-run will succeed if Aaron has enabled it.
  $KCADM_BIN update "clients/$target_id/management/permissions" -r "$KC_REALM" \
    -s "enabled=true" >/dev/null 2>&1 \
    || warn "could not enable management permissions on $target_cid (token-exchange feature flag?)"
  log "  granted: $source_cid -> $target_cid (best-effort; verify in admin UI under Permissions)"
}

# Phase 0 grants (per docs/KEYCLOAK_CLIENTS.md §6 rows 1-8)
grant_token_exchange "contact-ops-app"            "contact-ops-mcp"
grant_token_exchange "contact-ops-app"            "contact-ops-carddav"
grant_token_exchange "contact-ops-publisher"      "contact-ops-mcp"
grant_token_exchange "contact-ops-bridge-inbound" "contact-ops-mcp"

# Optional peer grants — only if the peer client already exists
for peer in meeting-ops-mcp project-ops-mcp crisis-ops-mcp data-intel-mcp brigade-mcp; do
  if [[ $DRY_RUN -eq 0 ]] && [[ -n "$(get_client_id "$peer")" ]]; then
    grant_token_exchange "$peer" "contact-ops-mcp"
  else
    log "  skip peer $peer (not yet in realm)"
  fi
done

# ----------------------------------------------------------------------------
# Verification — final list
# ----------------------------------------------------------------------------
log ""
log "=== Verification ==="
if [[ $DRY_RUN -eq 0 ]]; then
  for cid in contact-ops-mcp contact-ops-app contact-ops-carddav contact-ops-publisher contact-ops-bridge-inbound; do
    $KCADM_BIN get clients -r "$KC_REALM" -q "clientId=$cid" \
      --fields id,clientId,publicClient,bearerOnly,serviceAccountsEnabled,enabled
  done
else
  log "(dry-run) would list all five clients"
fi

log ""
log "Keycloak bootstrap complete."
log "Next steps:"
log "  - Confirm token-exchange feature is enabled on the Keycloak server (FEATURES env)."
log "  - In the admin UI: Clients > contact-ops-mcp > Permissions > add token-exchange policies"
log "    for each source client per docs/KEYCLOAK_CLIENTS.md §6."
log "  - Fetch client secrets via: kcadm.sh get clients/<id>/client-secret -r $KC_REALM"
log "  - Wire secrets into unicorn-postgresql.contact_ops_db.secrets (encrypted with tenant DEK)."
