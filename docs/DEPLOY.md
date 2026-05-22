# Contact-Ops Deployment — bigboy host

**Live URL**: `https://mcp.contacts.magicunicorn.dev` (Phase 2; MCP API only)
**Future URL** (Phase 2.5 admin UI): `https://contacts.magicunicorn.dev`
**CardDAV endpoint** (Phase 2): `https://carddav.contacts.magicunicorn.dev/carddav/`
**Tag**: `phase-2-deployed-bigboy` on `main`
**Date**: 2026-05-22

## Host topology

- **Application host**: `magicunicorn` (Tailscale name; SSH `ssh magicunicorn`). Public IP `69.153.31.100`.
- **Postgres**: dedicated `contact-ops-postgres` container (image `pgvector/pgvector:pg16`) on the `unicorn-network` Docker network. Separate from the shared `unicorn-postgresql` because that one is alpine + lacks pgvector.
- **Qdrant**: shared `unicorn-qdrant` on `unicorn-network` (collections namespaced `contact_ops_*`).
- **Garage**: shared `unicorn-garage` on `unicorn-network` + `web` (S3 at port 3900, admin at 3903).
- **FalkorDB**: shared `unicorn-falkordb` on `unicorn-network` (Phase 4 will use it).
- **Keycloak**: `uchub-keycloak` on commander at `https://auth.unicorncommander.ai/realms/uchub`. Peer-federated with `unicorn-keycloak` on bigboy.
- **Traefik**: shared `traefik` on bigboy, certresolver `letsencrypt` with HTTP-01 challenge.

## DNS

3 records in Cloudflare's `magicunicorn.dev` zone, all DNS-only (gray cloud) pointing at `69.153.31.100`:

- `mcp.contacts.magicunicorn.dev` A 69.153.31.100
- `contacts.magicunicorn.dev` A 69.153.31.100
- `carddav.contacts.magicunicorn.dev` A 69.153.31.100

Why DNS-only? Cloudflare Universal SSL on the free plan only covers `*.magicunicorn.dev` (single-level wildcard). Three-level subdomains aren't covered → TLS handshake failure at edge. Going direct lets Traefik provide the cert.

Upgrade path: order Cloudflare Advanced Certificate ($10/month) for `*.contacts.magicunicorn.dev` and re-enable proxy (orange cloud) if edge DDoS/caching is wanted.

## Keycloak clients (uchub realm)

5 OIDC clients registered by `scripts/keycloak_bootstrap.sh`:

| Client ID | Type | Use |
|---|---|---|
| `contact-ops-mcp` | bearer-only | MCP server (validates incoming JWTs, doesn't issue) |
| `contact-ops-app` | public + PKCE | Phase 2.5 human UI |
| `contact-ops-carddav` | confidential | CardDAV adapter (uses custom app-password table, not this client directly) |
| `contact-ops-publisher` | confidential + service account | Outbound to Data Intel, RFC 8693 token exchange |
| `contact-ops-bridge-inbound` | confidential + service account | Inbound enrichment from Data Intel |

Client secrets live in bigboy's `.env` (not in repo).

## Deploy procedure

```bash
# 1. SSH to bigboy
ssh magicunicorn

# 2. Pull latest
cd /home/muut/contact-ops
git checkout main
git pull

# 3. Build the backend image
docker compose -f docker-compose.prod.yml build contact-ops-backend

# 4. Run migrations (one-shot)
docker compose -f docker-compose.prod.yml run --rm contact-ops-backend alembic upgrade head

# 5. Start the backend
docker compose -f docker-compose.prod.yml up -d contact-ops-backend

# 6. Verify
docker exec contact-ops-backend curl -fsS http://localhost:8501/health
curl -fsS --resolve mcp.contacts.magicunicorn.dev:443:69.153.31.100 https://mcp.contacts.magicunicorn.dev/health
```

## Bootstrap (first time on a new host)

```bash
# Create the contact_ops_db Postgres (on contact-ops-postgres container)
docker volume create contact-ops-pgdata
docker run -d --name contact-ops-postgres \
  --restart unless-stopped \
  --network unicorn-network \
  -e POSTGRES_USER=contact_ops_admin \
  -e POSTGRES_PASSWORD="<generate-strong-password>" \
  -e POSTGRES_DB=contact_ops_db \
  -v contact-ops-pgdata:/var/lib/postgresql/data \
  pgvector/pgvector:pg16

# Bootstrap Keycloak clients (from commander or wherever uchub-keycloak runs)
cd /home/muut/contact-ops
KC_SERVER_URL=https://auth.unicorncommander.ai \
KC_REALM=uchub \
KC_ADMIN_USER=admin \
KC_ADMIN_PASS='<keycloak-admin>' \
  bash scripts/keycloak_bootstrap.sh

# Fetch client secrets for the .env
docker exec -i uchub-keycloak /opt/keycloak/bin/kcadm.sh get clients/<id>/client-secret -r uchub
```

## Configuration files (NOT in repo)

- `/home/muut/contact-ops/.env` on bigboy — 69 lines, contains:
  - `DATABASE_URL` for contact-ops-postgres
  - Keycloak issuer + JWKS URL + client secrets
  - Garage endpoint + access/secret keys
  - Data Intel federation client secret
  - CardDAV client secret
  - Bridge-inbound client secret
  - CORS origins

## Known operational gotchas

1. **Cloudflare proxy ON for 3-level subdomains** = TLS handshake failure. Keep gray-cloud.
2. **Local DNS cache** on macOS/iOS can hold the old (proxied) IP for 5-10 min after flipping. `dscacheutil -flushcache` (macOS) helps.
3. **`unicorn-postgresql` does NOT have pgvector** (alpine image). Always route Contact-Ops to `contact-ops-postgres`.
4. **Keycloak bootstrap script needs `docker cp` to ship JSON files into the `uchub-keycloak` container** (patched in-place; eventually fold this into the script).
5. **Traefik discovers the container only when it's on the `web` Docker network**. Compose attaches both `unicorn-network` and `web`; if one is missing, Traefik silently ignores.

## Smoke tests

```bash
# Health
curl -fsS https://mcp.contacts.magicunicorn.dev/health
# Returns: {"status":"healthy","service":"Contact-Ops","version":"0.2.0","timestamp":"..."}

# Unauthenticated MCP — should 401
curl -i -X POST https://mcp.contacts.magicunicorn.dev/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
# Returns: 401 with WWW-Authenticate: Bearer error="invalid_token", error_description="missing bearer token"

# Service-account token mint (publisher client)
TOKEN=$(curl -s -X POST https://auth.unicorncommander.ai/realms/uchub/protocol/openid-connect/token \
  -d "grant_type=client_credentials" \
  -d "client_id=contact-ops-publisher" \
  -d "client_secret=$PUBLISHER_SECRET" \
  -d "scope=person:read org:read" | jq -r .access_token)
echo "Token: ${TOKEN:0:30}..."

# Authenticated MCP — service account doesn't have tenant_id yet, will 401
# (Phase 2.5 admin UI logs in as a real user with tenant_id claim and then this works)
```

## Rollback

```bash
# Stop the backend
docker compose -f docker-compose.prod.yml down contact-ops-backend

# (Postgres + Keycloak clients stay; data is preserved)

# Roll back to a prior tag if needed
git checkout phase-1-complete  # or phase-0-complete
docker compose -f docker-compose.prod.yml build contact-ops-backend
docker compose -f docker-compose.prod.yml up -d contact-ops-backend
```

## Backups

Not yet wired. Phase 3 TODO:

- `contact-ops-postgres` volume `contact-ops-pgdata` — daily `pg_dump` to `~/backups/contact-ops/`
- Garage buckets — Garage's built-in replication if multi-node, otherwise rclone to off-host
- `.env` — encrypted backup to a separate location
