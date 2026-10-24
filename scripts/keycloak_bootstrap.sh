#!/usr/bin/env bash
#
# keycloak_bootstrap.sh - idempotent local/self-hosted Contact-Ops bootstrap
#
# This script creates the realm-side contract used by the self-host compose:
# exact application roles, permission scopes, OIDC clients, the confidential
# workspace-switch client, and the first human admin. It deliberately does not
# depend on a suite realm, a shared network, a pre-created user-attribute scope,
# or any external hostname.
#
# Required environment:
#   KC_SERVER_URL                         Keycloak base URL, without /admin
#   KC_REALM                              realm to create/update
#   KC_ADMIN_USER / KC_ADMIN_PASS         master-realm administrator
#   KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME     first human realm user
#   KEYCLOAK_BOOTSTRAP_ADMIN_EMAIL        its email (default REMOVED); Keycloak
#                                         refuses a password login for a user with no profile
#   KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD     first human realm-user password
#   KEYCLOAK_SWITCH_CLIENT_SECRET         secret used by the onboarding API
#
# Optional environment:
#   APP_ORIGIN (default http://127.0.0.1:3000)
#   MCP_ORIGIN (default http://127.0.0.1:8501)
#   CARDDAV_ORIGIN (default MCP_ORIGIN)
#   DEV_ORIGIN (default APP_ORIGIN)
#   SIGNUP_ENABLED (default false)
#   KC_CONTAINER (default contact-ops-keycloak, for host-side execution)
#
# Usage:
#   bash scripts/keycloak_bootstrap.sh [--dry-run] [--rotate-secrets]
#
# --dry-run        Show planned non-secret kcadm operations without connecting.
# --rotate-secrets Rotate non-bootstrap confidential clients. The switch-client
#                  secret is always set from KEYCLOAK_SWITCH_CLIENT_SECRET.
#
# Secrets are passed to kcadm only through process arguments at execution time;
# this script never prints them and never writes them into the repository.
set -euo pipefail

: "${KC_SERVER_URL:?KC_SERVER_URL is required}"
: "${KC_REALM:?KC_REALM is required}"
: "${KC_ADMIN_USER:?KC_ADMIN_USER is required}"
: "${KC_ADMIN_PASS:?KC_ADMIN_PASS is required}"
: "${KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME:?KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME is required}"
: "${KEYCLOAK_BOOTSTRAP_ADMIN_EMAIL:=REMOVED}"
: "${KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD:?KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD is required}"
: "${KEYCLOAK_SWITCH_CLIENT_SECRET:?KEYCLOAK_SWITCH_CLIENT_SECRET is required}"

KC_CONTAINER="${KC_CONTAINER:-contact-ops-keycloak}"
APP_ORIGIN="${APP_ORIGIN:-http://127.0.0.1:3000}"
MCP_ORIGIN="${MCP_ORIGIN:-http://127.0.0.1:8501}"
CARDDAV_ORIGIN="${CARDDAV_ORIGIN:-$MCP_ORIGIN}"
DEV_ORIGIN="${DEV_ORIGIN:-$APP_ORIGIN}"
SIGNUP_ENABLED="${SIGNUP_ENABLED:-false}"

DRY_RUN=0
ROTATE_SECRETS=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --rotate-secrets) ROTATE_SECRETS=1 ;;
    -h|--help)
      sed -n '2,35p' "$0"
      exit 0
      ;;
    *)
      printf '[bootstrap ERROR] unknown flag: %s\n' "$arg" >&2
      exit 1
      ;;
  esac
done

log() { printf '[bootstrap] %s\n' "$*" >&2; }
warn() { printf '[bootstrap WARN] %s\n' "$*" >&2; }
die() { printf '[bootstrap ERROR] %s\n' "$*" >&2; exit "${2:-5}"; }

# Print the id of the first "id,name" CSV row whose name matches $1.
# Pure bash: the Keycloak image this runs in ships no awk.
csv_id_for_name() {
  local wanted="$1" line id name
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    id="${line%%,*}"
    name="${line#*,}"
    if [[ "$name" == "$wanted" ]]; then
      printf '%s\n' "$id"
      return 0
    fi
  done
  return 0
}

case "$SIGNUP_ENABLED" in
  true|false) ;;
  *) die "SIGNUP_ENABLED must be true or false" 1 ;;
esac

# -----------------------------------------------------------------------------
# Resolve kcadm.sh without assuming a host installation.
# -----------------------------------------------------------------------------
KCADM_ARGS=()
KCADM_MODE=""
if command -v kcadm.sh >/dev/null 2>&1; then
  KCADM_ARGS=("$(command -v kcadm.sh)")
  KCADM_MODE="local"
elif [[ -x /opt/keycloak/bin/kcadm.sh ]]; then
  KCADM_ARGS=(/opt/keycloak/bin/kcadm.sh)
  KCADM_MODE="keycloak-image"
elif command -v docker >/dev/null 2>&1 \
    && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$KC_CONTAINER"; then
  KCADM_ARGS=(docker exec -i "$KC_CONTAINER" /opt/keycloak/bin/kcadm.sh)
  KCADM_MODE="docker:$KC_CONTAINER"
elif [[ $DRY_RUN -eq 1 ]]; then
  KCADM_ARGS=(kcadm.sh)
  KCADM_MODE="dry-run"
else
  die "Cannot find kcadm.sh and no running Keycloak container '$KC_CONTAINER' is available" 3
fi
log "kcadm via: $KCADM_MODE"

# All normal operations have no secret arguments. Dry-run output is therefore
# safe. Authentication and password updates use dedicated wrappers so secrets
# are never rendered by the dry-run logger.
kc() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '+ kcadm.sh' >&2
    for arg in "$@"; do printf ' %q' "$arg" >&2; done
    printf '\n' >&2
    return 0
  fi
  "${KCADM_ARGS[@]}" "$@"
}

kc_capture() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '+ kcadm.sh' >&2
    for arg in "$@"; do printf ' %q' "$arg" >&2; done
    printf ' (capture)\n' >&2
    return 0
  fi
  "${KCADM_ARGS[@]}" "$@"
}

kc_set_password() {
  local user_id="$1" password="$2"
  if [[ $DRY_RUN -eq 1 ]]; then
    log "would set the bootstrap user's password"
    return 0
  fi
  "${KCADM_ARGS[@]}" set-password -r "$KC_REALM" --userid "$user_id" \
    --new-password "$password" >/dev/null
}

kc_set_client_secret() {
  local client_id="$1" secret="$2"
  if [[ $DRY_RUN -eq 1 ]]; then
    log "would set the workspace-switch client secret"
    return 0
  fi
  "${KCADM_ARGS[@]}" update "clients/$client_id" -r "$KC_REALM" \
    -s "secret=$secret" >/dev/null
}

# -----------------------------------------------------------------------------
# Authenticate and ensure the realm itself exists.
# -----------------------------------------------------------------------------
if [[ $DRY_RUN -eq 1 ]]; then
  log "would authenticate to the master realm at $KC_SERVER_URL"
else
  authenticated=0
  for attempt in $(seq 1 60); do
    if "${KCADM_ARGS[@]}" config credentials \
        --server "$KC_SERVER_URL" \
        --realm master \
        --user "$KC_ADMIN_USER" \
        --password "$KC_ADMIN_PASS" >/dev/null 2>&1; then
      authenticated=1
      break
    fi
    if [[ $attempt -eq 60 ]]; then
      break
    fi
    sleep 2
  done
  [[ $authenticated -eq 1 ]] || die "Keycloak administrator authentication failed" 4
fi

log "Ensuring realm: $KC_REALM"
if [[ $DRY_RUN -eq 1 ]]; then
  log "would create or update realm registrationAllowed=$SIGNUP_ENABLED"
elif "${KCADM_ARGS[@]}" get "realms/$KC_REALM" >/dev/null 2>&1; then
  "${KCADM_ARGS[@]}" update "realms/$KC_REALM" \
    -s enabled=true \
    -s "registrationAllowed=$SIGNUP_ENABLED" >/dev/null
else
  "${KCADM_ARGS[@]}" create realms \
    -s "realm=$KC_REALM" \
    -s enabled=true \
    -s "registrationAllowed=$SIGNUP_ENABLED" >/dev/null
fi

# -----------------------------------------------------------------------------
# Realm roles and permission scopes.
# -----------------------------------------------------------------------------
ensure_realm_role() {
  local role="$1" description="$2"
  if [[ $DRY_RUN -eq 1 ]]; then
    log "would ensure realm role: $role"
    return 0
  fi
  if "${KCADM_ARGS[@]}" get "roles/$role" -r "$KC_REALM" >/dev/null 2>&1; then
    return 0
  fi
  "${KCADM_ARGS[@]}" create roles -r "$KC_REALM" \
    -s "name=$role" -s "description=$description" >/dev/null
}

log "Ensuring application roles"
ensure_realm_role CLIENT "External read access within a workspace"
ensure_realm_role STAFF "Operational writes within a workspace"
ensure_realm_role MANAGER "Workspace administration and approvals"
ensure_realm_role ADMIN "Full administration for this Contact-Ops cell"

CLIENT_SCOPES=(
  "person:read" "person:write" "person:propose_merge" "person:apply_merge"
  "person:bulk" "org:read" "org:write" "org:bulk" "field:propose_set"
  "field:apply_set" "relationship:propose" "relationship:apply" "tag:apply"
  "voice:match" "voice:assign" "agent:register" "admin:tenant" "admin:debug"
  "carddav:sync" "pat:read" "pat:write"
)

ensure_client_scope() {
  local scope_name="$1" existing_id
  if [[ $DRY_RUN -eq 1 ]]; then
    log "would ensure client scope: $scope_name"
    return 0
  fi
  existing_id="$("${KCADM_ARGS[@]}" get client-scopes -r "$KC_REALM" \
    --fields id,name --format csv --noquotes 2>/dev/null \
    | csv_id_for_name "$scope_name" || true)"
  [[ -n "$existing_id" ]] && return 0
  "${KCADM_ARGS[@]}" create client-scopes -r "$KC_REALM" \
    -s "name=$scope_name" \
    -s protocol=openid-connect \
    -s 'attributes."include.in.token.scope"=true' \
    -s 'attributes."display.on.consent.screen"=false' \
    -s "description=Contact-Ops permission scope: $scope_name" >/dev/null
}

# Keycloak 24+ drops user attributes the declarative user profile does not
# declare, so onboarding's tenant_id/tenant_slug/tenant_hipaa writes vanished
# and tokens never carried a tenant. ADMIN_EDIT keeps them writable by the
# admin API only; a user cannot set their own tenant.
log "Allowing admin-managed user attributes"
if [[ $DRY_RUN -eq 0 ]]; then
  profile_json="$("${KCADM_ARGS[@]}" get users/profile -r "$KC_REALM")"
  if [[ "$profile_json" != *'"unmanagedAttributePolicy"'* ]]; then
    profile_json="{\"unmanagedAttributePolicy\":\"ADMIN_EDIT\",${profile_json#*\{}"
    printf '%s\n' "$profile_json" \
      | "${KCADM_ARGS[@]}" update users/profile -r "$KC_REALM" -f - >/dev/null
  fi
fi

log "Ensuring permission scopes"
for scope in "${CLIENT_SCOPES[@]}"; do
  ensure_client_scope "$scope"
done

get_scope_id() {
  local scope_name="$1"
  if [[ $DRY_RUN -eq 1 ]]; then
    printf 'dry-run-scope-%s\n' "$scope_name"
    return 0
  fi
  "${KCADM_ARGS[@]}" get client-scopes -r "$KC_REALM" \
    --fields id,name --format csv --noquotes 2>/dev/null \
    | csv_id_for_name "$scope_name"
}

assign_scope() {
  local client_id="$1" scope_id="$2" kind="$3"
  [[ -z "$scope_id" ]] && return 0
  if [[ $DRY_RUN -eq 1 ]]; then
    log "would assign $kind scope $scope_id to client $client_id"
    return 0
  fi
  "${KCADM_ARGS[@]}" update "clients/$client_id/${kind}-client-scopes/$scope_id" \
    -r "$KC_REALM" >/dev/null 2>&1 \
    || warn "scope assignment returned non-zero; it may already exist"
}

# -----------------------------------------------------------------------------
# Small JSON helpers for kcadm representations. All values are operator-provided
# URLs or fixed client metadata, never secrets or user/customer data.
# -----------------------------------------------------------------------------
json_array() {
  local raw="${1:-}" item first=1
  local -a items=()
  [[ -n "$raw" ]] && IFS=',' read -r -a items <<< "$raw"
  printf '['
  if [[ ${#items[@]} -gt 0 ]]; then
    for item in "${items[@]}"; do
      [[ -z "$item" ]] && continue
      if [[ $first -eq 0 ]]; then printf ','; fi
      printf '"%s"' "$item"
      first=0
    done
  fi
  printf ']'
}

write_client_json() {
  local out="$1" cid="$2" name="$3" public="$4" bearer="$5"
  local standard="$6" direct="$7" service="$8" root="$9"
  local redirects="${10:-}" origins="${11:-}" redirect_json origin_json
  redirect_json="$(json_array "$redirects")"
  origin_json="$(json_array "$origins")"
  cat >"$out" <<JSON
{
  "clientId": "$cid",
  "name": "$name",
  "description": "Contact-Ops self-host client",
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
    "access.token.lifespan": "300"
  },
  "fullScopeAllowed": false,
  "protocol": "openid-connect"
}
JSON
}

get_client_id() {
  local client_name="$1"
  if [[ $DRY_RUN -eq 1 ]]; then
    printf 'dry-run-client-%s\n' "$client_name"
    return 0
  fi
  "${KCADM_ARGS[@]}" get clients -r "$KC_REALM" -q "clientId=$client_name" \
    --fields id --format csv --noquotes 2>/dev/null \
    | tr -d '\r' | head -n1 || true
}

upsert_client() {
  local client_name="$1" json_file="$2" existing_id
  existing_id="$(get_client_id "$client_name")"
  if [[ -n "$existing_id" && "$existing_id" != "dry-run-client-$client_name" ]]; then
    log "updating client: $client_name"
    if [[ $ROTATE_SECRETS -eq 1 && "$client_name" != "contact-ops-switch" && $DRY_RUN -eq 0 ]]; then
      "${KCADM_ARGS[@]}" create "clients/$existing_id/client-secret" -r "$KC_REALM" >/dev/null
    fi
    kc update "clients/$existing_id" -r "$KC_REALM" -f "$json_file" >/dev/null
    printf '%s\n' "$existing_id"
    return 0
  fi
  log "creating client: $client_name"
  kc create clients -r "$KC_REALM" -f "$json_file" >/dev/null
  get_client_id "$client_name"
}

ensure_mapper_for_client() {
  local client_id="$1" mapper_name="$2" mapper_file="$3" existing
  if [[ $DRY_RUN -eq 1 ]]; then
    log "would ensure mapper $mapper_name on client $client_id"
    return 0
  fi
  existing="$("${KCADM_ARGS[@]}" get "clients/$client_id/protocol-mappers/models" \
    -r "$KC_REALM" --fields id,name --format csv --noquotes 2>/dev/null \
    | csv_id_for_name "$mapper_name" || true)"
  [[ -n "$existing" ]] && return 0
  "${KCADM_ARGS[@]}" create "clients/$client_id/protocol-mappers/models" \
    -r "$KC_REALM" -f "$mapper_file" >/dev/null
}

write_user_attribute_mapper() {
  local attribute="$1" claim="$2" json_type="$3" out="$4"
  cat >"$out" <<JSON
{
  "name": "$claim",
  "protocol": "openid-connect",
  "protocolMapper": "oidc-usermodel-attribute-mapper",
  "consentRequired": false,
  "config": {
    "user.attribute": "$attribute",
    "claim.name": "$claim",
    "jsonType.label": "$json_type",
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

TMP_DIR="$(mktemp -d -t contact-ops-keycloak-XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT

# These are ordinary OIDC user-attribute mappers. No special client scope is
# required; absent attributes simply produce no tenant claim before onboarding.
write_user_attribute_mapper tenant_id tenant_id String "$TMP_DIR/mapper_tenant_id.json"
write_user_attribute_mapper tenant_slug tenant_slug String "$TMP_DIR/mapper_tenant_slug.json"
write_user_attribute_mapper tenant_hipaa tenant_hipaa boolean "$TMP_DIR/mapper_tenant_hipaa.json"
write_audience_mapper contact-ops-mcp "$TMP_DIR/mapper_audience.json"

attach_common_mappers() {
  local client_id="$1"
  ensure_mapper_for_client "$client_id" tenant_id "$TMP_DIR/mapper_tenant_id.json"
  ensure_mapper_for_client "$client_id" tenant_slug "$TMP_DIR/mapper_tenant_slug.json"
  ensure_mapper_for_client "$client_id" tenant_hipaa "$TMP_DIR/mapper_tenant_hipaa.json"
  ensure_mapper_for_client "$client_id" aud-contact-ops-mcp "$TMP_DIR/mapper_audience.json"
}

assign_default_scope() {
  local client_id="$1" scope
  for scope in pat:read pat:write; do
    assign_scope "$client_id" "$(get_scope_id "$scope")" default
  done
}

log "Ensuring OIDC clients"

write_client_json "$TMP_DIR/mcp.json" contact-ops-mcp "Contact-Ops MCP" \
  false true false false false "$MCP_ORIGIN" "" ""
MCP_ID="$(upsert_client contact-ops-mcp "$TMP_DIR/mcp.json")"
attach_common_mappers "$MCP_ID"
assign_default_scope "$MCP_ID"

write_client_json "$TMP_DIR/app.json" contact-ops-app "Contact-Ops Web App" \
  true false true true false "$APP_ORIGIN" \
  "$APP_ORIGIN/auth/callback,$APP_ORIGIN/auth/silent-renew,$DEV_ORIGIN/auth/callback,$DEV_ORIGIN/auth/silent-renew" \
  "$APP_ORIGIN,$DEV_ORIGIN"
APP_ID="$(upsert_client contact-ops-app "$TMP_DIR/app.json")"
attach_common_mappers "$APP_ID"
assign_default_scope "$APP_ID"

write_client_json "$TMP_DIR/carddav.json" contact-ops-carddav "Contact-Ops CardDAV" \
  false false false true true "$CARDDAV_ORIGIN" "" ""
CARDDAV_ID="$(upsert_client contact-ops-carddav "$TMP_DIR/carddav.json")"
attach_common_mappers "$CARDDAV_ID"
for scope in carddav:sync person:read person:write; do
  assign_scope "$CARDDAV_ID" "$(get_scope_id "$scope")" default
done

# These service clients are local placeholders for optional integrations. They
# carry the same local audience contract but no external federation permissions.
write_client_json "$TMP_DIR/publisher.json" contact-ops-publisher "Contact-Ops Publisher" \
  false false false false true "$MCP_ORIGIN" "" ""
PUBLISHER_ID="$(upsert_client contact-ops-publisher "$TMP_DIR/publisher.json")"
attach_common_mappers "$PUBLISHER_ID"

write_client_json "$TMP_DIR/bridge.json" contact-ops-bridge-inbound "Contact-Ops Bridge" \
  false false false false true "$MCP_ORIGIN" "" ""
BRIDGE_ID="$(upsert_client contact-ops-bridge-inbound "$TMP_DIR/bridge.json")"
attach_common_mappers "$BRIDGE_ID"

# The onboarding endpoint uses client credentials to write tenant attributes on
# the user. Give only this switch service account the minimum Keycloak admin
# roles needed by that endpoint.
write_client_json "$TMP_DIR/switch.json" contact-ops-switch "Contact-Ops Workspace Switch" \
  false false false false true "$MCP_ORIGIN" "" ""
SWITCH_ID="$(upsert_client contact-ops-switch "$TMP_DIR/switch.json")"
kc_set_client_secret "$SWITCH_ID" "$KEYCLOAK_SWITCH_CLIENT_SECRET"
# With fullScopeAllowed=false the realm-management roles granted below never
# reach the service-account token, and every admin call answers 403. This
# account holds only those roles, so full scope adds nothing else.
if [[ $DRY_RUN -eq 1 ]]; then
  log "would allow the switch client the full scope of its service account"
else
  "${KCADM_ARGS[@]}" update "clients/$SWITCH_ID" -r "$KC_REALM" \
    -s fullScopeAllowed=true >/dev/null
fi

if [[ $DRY_RUN -eq 1 ]]; then
  log "would grant the switch service account view-users, query-users, and manage-users"
else
  for role in view-users query-users manage-users; do
    "${KCADM_ARGS[@]}" add-roles -r "$KC_REALM" \
      --uusername "service-account-contact-ops-switch" \
      --cclientid realm-management --rolename "$role" >/dev/null 2>&1 \
      || die "could not grant realm-management/$role to workspace-switch service account" 5
  done
fi

# -----------------------------------------------------------------------------
# Bootstrap the first human admin in the application realm.
# -----------------------------------------------------------------------------
get_user_id() {
  local username="$1"
  if [[ $DRY_RUN -eq 1 ]]; then
    printf 'dry-run-user-%s\n' "$username"
    return 0
  fi
  "${KCADM_ARGS[@]}" get users -r "$KC_REALM" -q "username=$username" \
    --fields id --format csv --noquotes 2>/dev/null \
    | tr -d '\r' | head -n1 || true
}

log "Ensuring bootstrap admin user"
ADMIN_USER_ID="$(get_user_id "$KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME")"
if [[ "$ADMIN_USER_ID" == dry-run-user-* ]]; then
  log "would create/update bootstrap admin and assign ADMIN"
elif [[ -z "$ADMIN_USER_ID" ]]; then
  "${KCADM_ARGS[@]}" create users -r "$KC_REALM" \
    -s "username=$KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME" \
    -s "email=$KEYCLOAK_BOOTSTRAP_ADMIN_EMAIL" \
    -s firstName=Local -s lastName=Admin \
    -s enabled=true -s emailVerified=true >/dev/null
  ADMIN_USER_ID="$(get_user_id "$KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME")"
  [[ -z "$ADMIN_USER_ID" ]] && die "bootstrap admin user was not created" 5
else
  "${KCADM_ARGS[@]}" update "users/$ADMIN_USER_ID" -r "$KC_REALM" \
    -s "email=$KEYCLOAK_BOOTSTRAP_ADMIN_EMAIL" \
    -s enabled=true -s emailVerified=true >/dev/null
fi
kc_set_password "$ADMIN_USER_ID" "$KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD"
if [[ $DRY_RUN -eq 1 ]]; then
  log "would assign realm role ADMIN to the bootstrap admin"
else
  "${KCADM_ARGS[@]}" add-roles -r "$KC_REALM" --uid "$ADMIN_USER_ID" \
    --rolename ADMIN >/dev/null 2>&1 \
    || die "could not assign ADMIN to the bootstrap admin" 5
fi

log "Keycloak bootstrap complete; signup registrationAllowed=$SIGNUP_ENABLED"
log "The bootstrap admin password was not printed."
