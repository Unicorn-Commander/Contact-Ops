# Contact-Ops

Contact-Ops is a self-hostable, agent-first contact and organization registry.
The supported stranger install is a local Docker Compose cell with Postgres,
Redis, Qdrant, FalkorDB, Garage, Keycloak, the API/MCP server, and the web UI.

## Quickstart

Run this block from the repository root on a fresh Linux host. It is the
complete local install path: it creates the `.env` file, generates every secret
at install time, starts the cell, checks the public health URL, signs in the
generated admin through the local OIDC realm, provisions that admin's workspace,
and issues a one-time agent PAT. The PAT is held in a shell variable and is not
printed.

Requirements: Docker Engine with the Compose plugin, `curl`, `openssl`, and
Python 3. The first build downloads the application dependencies and container
images.

```bash
set -euo pipefail

cp deploy/selfcontained/.env.selfcontained.template .env

pg_admin_password="$(openssl rand -hex 24)"
co_runtime_password="$(openssl rand -hex 24)"
kc_admin_password="$(openssl rand -hex 24)"
bootstrap_password="$(openssl rand -hex 24)"
switch_secret="$(openssl rand -hex 32)"
qdrant_api_key="$(openssl rand -hex 32)"
garage_rpc_secret="$(openssl rand -hex 32)"
garage_admin_token="$(openssl rand -hex 32)"
connector_key="$(python3 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"

python3 - "$pg_admin_password" "$co_runtime_password" "$kc_admin_password" \
  "$bootstrap_password" "$switch_secret" "$qdrant_api_key" \
  "$garage_rpc_secret" "$garage_admin_token" "$connector_key" <<'PY'
from pathlib import Path
import re
import sys

path = Path(".env")
text = path.read_text(encoding="utf-8")
values = {
    "PG_ADMIN_PASSWORD": sys.argv[1],
    "CO_RUNTIME_PASSWORD": sys.argv[2],
    "KC_ADMIN_PASS": sys.argv[3],
    "KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD": sys.argv[4],
    "KEYCLOAK_SWITCH_CLIENT_SECRET": sys.argv[5],
    "QDRANT_API_KEY": sys.argv[6],
    "GARAGE_RPC_SECRET": sys.argv[7],
    "GARAGE_ADMIN_TOKEN": sys.argv[8],
    "CONNECTOR_ENCRYPTION_KEY": sys.argv[9],
    "DATABASE_URL": f"postgresql+asyncpg://contact_ops_runtime:{sys.argv[2]}@contact-ops-postgres:5432/contact_ops_db",
    "AUDIT_DATABASE_URL": f"postgresql+asyncpg://contact_ops_runtime:{sys.argv[2]}@contact-ops-postgres:5432/contact_ops_db",
    "MIGRATION_DATABASE_URL": f"${CONTACT_OPS_DATABASE_URL}",
}
for key, value in values.items():
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    text, count = pattern.subn(lambda _: f"{key}={value}", text, count=1)
    if count != 1:
        raise SystemExit(f"missing template key: {key}")
path.write_text(text, encoding="utf-8")
path.chmod(0o600)
PY

docker compose -f docker-compose.selfhost.yml up -d --build
curl -fsS http://127.0.0.1:8501/health

# Load the generated values into this shell without printing them.
set -a
. ./.env
set +a

admin_token() {
  curl -fsS -X POST "$KEYCLOAK_ISSUER/protocol/openid-connect/token" \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode grant_type=password \
    --data-urlencode client_id=contact-ops-app \
    --data-urlencode username="$KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME" \
    --data-urlencode password="$KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD" \
    --data-urlencode scope='openid profile email' \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
}

# This is the documented admin sign-in/token route. The same username and
# password work in a browser at http://127.0.0.1:3000/.
access_token="$(admin_token)"
onboard_response="$(curl -fsS -X POST http://127.0.0.1:8501/api/auth/onboard \
  -H "Authorization: Bearer $access_token")"
printf '%s\n' "$onboard_response" | python3 -c \
  'import json,sys; body=json.load(sys.stdin); assert body["provisioned"] is True; print("admin workspace: " + body["tenant_slug"])'

# Re-mint after onboarding so the token contains the new tenant claim.
access_token="$(admin_token)"
pat_response="$(curl -fsS http://127.0.0.1:8501/mcp \
  -H "Authorization: Bearer $access_token" \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"issue_personal_access_token","arguments":{"display_name":"local-agent"}},"id":"pat-1"}')"
agent_pat="$(printf '%s' "$pat_response" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["result"]["structuredContent"]["token_plaintext"])')"
case "$agent_pat" in
  co_pat_*) printf 'agent PAT issued: %s...\n' "${agent_pat:0:7}" ;;
  *) printf '%s\n' "$pat_response" >&2; exit 1 ;;
esac
export CONTACT_OPS_PAT="$agent_pat"
```

Expected checks are a JSON response containing `"status":"healthy"`, an
`admin workspace: ...` line, and `agent PAT issued: co_pat_...`. Keep
`CONTACT_OPS_PAT` in the agent's local secret handling; it is shown only once by
the API and should not be copied into tickets, shell history, or source files.

Open `http://127.0.0.1:3000/` to use the UI. Sign in as the generated
`KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME` (default username: `admin`) with the
generated `KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD` from `.env`. The default
`SIGNUP_ENABLED=false` keeps self-registration off; set it to `true` in `.env`
before the first `docker compose ... up` if this cell should allow the local
Keycloak registration screen. No external account service is required.

The API health URL is `http://127.0.0.1:8501/health`. The OIDC issuer is
`http://contact-ops.localhost:8080/realms/contact-ops`; the `.localhost`
hostname resolves to loopback on standard Linux installations. The compose
file maps this issuer back through the Docker host for server-side onboarding.

## Development checks

The stranger quickstart is the supported install path. For code changes, run
the backend tests from `backend/` with the project's test dependencies and use
the static checks listed in the lane handoff when Docker is unavailable.

## License

The repository license must be resolved before public publication. This lane
does not change license files; the publication decision is recorded in the
installation-readiness handoff.
