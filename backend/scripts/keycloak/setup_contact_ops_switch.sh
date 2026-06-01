#!/usr/bin/env sh
# Reproducible Keycloak setup for the Contact-Ops workspace switcher (Phase 4.3).
#
# Creates the confidential `contact-ops-switch` service-account client. Its
# service account can set a user's `tenant_id`/`tenant_slug` attributes, which is
# how a workspace switch re-mints the user's token: the backend (using this
# client's credentials) updates the attribute, the SPA does a prompt=none silent
# re-auth, and the realm's existing User Attribute mappers stamp the new
# tenant_id/tenant_slug. (We use silent re-auth rather than token-exchange because
# bigboy's Keycloak is 25.0.6, where standard RFC 8693 token-exchange is not GA;
# silent re-auth is version-agnostic and needs no token-exchange permission setup.)
#
# MECHANISM-INDEPENDENT FACTS this relies on (verified 2026-06-01 on bigboy):
#   * realm `uchub`; client `contact-ops-app` maps tenant_id/tenant_slug from the
#     user attributes of the same name and stamps aud=contact-ops-mcp. The switch
#     just changes those attributes; no new mapper, no seeding.
#
# WHERE TO RUN: inside the Keycloak container, which has kcadm + admin creds in env.
#   ssh magicunicorn 'docker exec -i unicorn-keycloak sh' < setup_contact_ops_switch.sh
# Re-run idempotently. Apply to bigboy (auth.magicunicorn.dev) now; re-run against
# the commander instance (auth.unicorncommander.ai) when the public SaaS launches.
#
# AFTER RUNNING: put the printed secret in the Contact-Ops backend .env as
#   KEYCLOAK_SWITCH_CLIENT_ID=contact-ops-switch
#   KEYCLOAK_SWITCH_CLIENT_SECRET=<printed value>
#
# SECURITY NOTE: this grants the service account realm-management `manage-users`,
# which is broad (it can edit any user). `admin-fine-grained-authz` IS enabled on
# this KC, so a follow-up should scope the service account to manage only the
# tenant_id/tenant_slug attributes. The switch endpoint also decode-asserts the
# re-minted tenant_id == the membership-checked target, bounding the blast radius.
#
# REVERSIBLE: `kcadm delete clients/<id> -r uchub` removes the client + its SA.
set -eu

REALM=uchub
KC=/opt/keycloak/bin/kcadm.sh

"$KC" config credentials --server http://localhost:8080 --realm master \
  --user "$KEYCLOAK_ADMIN" --password "$KEYCLOAK_ADMIN_PASSWORD"

# 1. Create the confidential service-account client (skip if it already exists).
EXISTING=$("$KC" get clients -r "$REALM" -q clientId=contact-ops-switch --fields id 2>/dev/null | grep -oE '[0-9a-f-]{36}' | head -1 || true)
if [ -z "$EXISTING" ]; then
  "$KC" create clients -r "$REALM" \
    -s clientId=contact-ops-switch \
    -s enabled=true \
    -s publicClient=false \
    -s serviceAccountsEnabled=true \
    -s standardFlowEnabled=false \
    -s implicitFlowEnabled=false \
    -s directAccessGrantsEnabled=false \
    -s 'redirectUris=[]' \
    -s 'description=Backend service account for the Contact-Ops workspace switcher (sets tenant_id/tenant_slug on switch).'
  echo "created client contact-ops-switch"
else
  echo "client contact-ops-switch already exists ($EXISTING)"
fi

CID=$("$KC" get clients -r "$REALM" -q clientId=contact-ops-switch --fields id | grep -oE '[0-9a-f-]{36}' | head -1)

# 2. Grant the service account realm-management manage-users (to set attributes).
"$KC" add-roles -r "$REALM" \
  --uusername "service-account-contact-ops-switch" \
  --cclientid realm-management --rolename manage-users
echo "granted manage-users to service-account-contact-ops-switch"

# 3. Print the client secret (-> backend .env KEYCLOAK_SWITCH_CLIENT_SECRET).
echo "contact-ops-switch client secret:"
"$KC" get "clients/$CID/client-secret" -r "$REALM" --fields value
