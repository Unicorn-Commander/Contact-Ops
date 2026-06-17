"""Configuration settings for Contact-Ops.

Pydantic-settings driven. All settings come from environment variables. No
production-grade defaults that contain real secrets; required vars fail loudly
at startup if unset.

See Contact-Ops-MCP-Design.md §3 (System Topology) for what each setting maps to.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Contact-Ops settings loaded from environment.

    Every required setting must be provided via env var (or `.env` file) at
    process start; otherwise the app refuses to start.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    APP_NAME: str = "Contact-Ops"
    APP_VERSION: str = "2.7.3"
    ENV: Literal["dev", "test", "staging", "prod"] = "dev"
    DEBUG: bool = False
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Database — required. No default password. Connects as `contact_ops_app` role.
    DATABASE_URL: str = Field(
        ...,
        description="Async DSN for the contact_ops_app role, e.g. "
        "postgresql+asyncpg://contact_ops_app:PASS@host:5432/contact_ops_db",
    )

    # Audit database — same instance, separate role (`contact_ops_audit`) per
    # design doc §4.1.9. Defaults to DATABASE_URL if unset (acceptable for dev;
    # in prod operators should set this to a DSN that authenticates as the
    # audit role).
    AUDIT_DATABASE_URL: str | None = None

    # Migration database — synchronous DSN used by Alembic. Connects as a
    # migration superuser so it can ALTER TABLE, CREATE EXTENSION, etc.
    MIGRATION_DATABASE_URL: str | None = None

    # Redis
    REDIS_URL: str = "redis://unicorn-redis:6379/5"

    # Contact import connectors. Generate Fernet key with:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    CONNECTOR_ENCRYPTION_KEY: str = ""
    M365_CLIENT_ID: str = ""
    M365_CLIENT_SECRET: str = ""
    M365_REDIRECT_URI: str = ""
    # Single-tenant apps (sign-in-audience AzureADMyOrg) must use the tenant
    # GUID in the OAuth URLs; /common is multi-tenant-only after 2018-10-15
    # (AADSTS50194). Defaults to "common" so multi-tenant apps still work.
    M365_TENANT_ID: str = "common"
    GMAIL_CLIENT_ID: str = ""
    GMAIL_CLIENT_SECRET: str = ""
    GMAIL_REDIRECT_URI: str = ""
    FRONTEND_URL: str = "https://contacts.magicunicorn.dev"
    # Public self-serve signup (P-00075). DORMANT by default + BACKEND-driven (the
    # SPA fetches /api/auth/signup-config at the login screen, so flipping signup
    # on is a backend env change, NOT a frontend rebuild). SIGNUP_MODE: "suite" =
    # accounts + subscriptions are owned by the Unicorn Commander suite, so CO
    # only surfaces "sign in" + a link to SUITE_SIGNUP_URL (the product cell on
    # unicorncommander.ai); "standalone" = CO's own Keycloak self-registration
    # (self-host / direct-CO). First-login auto-provision (POST /api/auth/onboard)
    # already creates a free-tier personal workspace, so signup is just the
    # surface + (Aaron-side) enabling registration on the realm.
    SIGNUP_ENABLED: bool = False
    SIGNUP_MODE: Literal["suite", "standalone"] = "suite"
    SUITE_SIGNUP_URL: str = ""

    # Tenant resolution
    TENANT_GUC_NAME: str = "app.tenant_id"
    # Actor identity GUC. Set alongside TENANT_GUC_NAME on every RLS-subject
    # session so the DB layer can attribute writes to a uc_uid (and so future
    # uc_uid-aware RLS policies have the value available). Phase 4.0a only sets
    # it; no policy references it yet.
    UC_UID_GUC_NAME: str = "app.uc_uid"

    # --- Phase 4.3 workspace-access controls ---
    # azp/client_id allow-list (design §5): only these Keycloak clients may
    # present a tenant_id claim, so a rogue uchub client cannot forge a
    # workspace binding. Warn-only until TENANT_CLAIM_AZP_ENFORCE is flipped on
    # (so real azp values are observed in logs before any hard rejection).
    TENANT_CLAIM_ALLOWED_AZP: list[str] = [
        "contact-ops-app",
        "contact-ops-switch",
        "contact-ops-mcp",
    ]
    TENANT_CLAIM_AZP_ENFORCE: bool = False
    # Membership gate (design §4.4): when True, a human Keycloak session must
    # have an active user_tenant_membership for its tenant or the request is
    # refused. MUST stay False until memberships are seeded, else it locks
    # everyone out; flipped on at activation alongside the seed.
    MEMBERSHIP_GATE_ENFORCED: bool = False
    # Entitlement gate (P-00075 §4 monetization): per-tool plan-tier enforcement
    # at MCP dispatch. The free/paid line is "gate on compute" — cheap work
    # (reads, MCP/UI CRUD, deterministic dedup) is free; our-GPU autonomous
    # agents (ML dedup, enrichment, voice-match, bulk import, connectors) are
    # pro; compliance/SSO/federation are enterprise. The policy lives in
    # contact_ops.mcp.entitlement (single source of truth, also feeds the
    # Brigade manifest). Shadow-first: with this False the gate only LOGS what
    # it WOULD deny (entitlement_would_deny) and allows the call — flip True to
    # enforce. Mirrors the MEMBERSHIP_GATE_ENFORCED rollout caution: a mis-tagged
    # free tool would otherwise hard-lock a paying tenant out of work they expect.
    ENTITLEMENT_ENFORCED: bool = False
    # Deployment mode. "self_host" = an open-source operator running their own
    # cell on their own hardware; entitlement gating is BYPASSED entirely (their
    # server, their data — gating it is pointless and user-hostile). "hosted" =
    # our multi-tenant SaaS where tiers are enforced. Self-host sovereignty is
    # the suite's offline-license story, not a per-tool paywall.
    DEPLOYMENT_MODE: Literal["hosted", "self_host"] = "hosted"
    # Observability (P-00075 ops). Sentry is DORMANT until SENTRY_DSN is set —
    # the entire error-tracking path no-ops with an empty DSN (mirrors the OTLP
    # gating in agents/observability/otel.py). send_default_pii is hardcoded
    # False in init_sentry (NOT a flag): this is multi-tenant contact PII under
    # RLS, so it must never leave the box. APM/profiling default OFF (0.0 sample)
    # until deliberately raised. HTTP request metrics are cheap + safe so they're
    # ON by default; they label by route TEMPLATE (never raw path) to bound
    # cardinality and never carry tenant ids.
    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0
    SENTRY_PROFILES_SAMPLE_RATE: float = 0.0
    METRICS_HTTP_ENABLED: bool = True
    # Boot RLS self-test probe tenants. Under the runtime role the self-test
    # cannot enumerate tenants (RLS hides them with no GUC bound), so it skips.
    # Provide >=2 known tenant ids here so it can actively assert cross-tenant
    # isolation at boot. Empty -> falls back to enumerating tenants (works under
    # a privileged/dev role). Safe if stale: a wrong id yields a trivially-
    # passing test, never a false refuse-to-boot.
    RLS_SELFTEST_TENANT_IDS: list[str] = []

    # Keycloak — uchub realm
    KEYCLOAK_ISSUER: str = Field(
        default="",
        description="The exact `iss` claim Keycloak puts on tokens, e.g. "
        "https://auth.unicorncommander.ai/realms/uchub. Used verbatim for "
        "issuer verification; do NOT append /openid-connect.",
    )
    KEYCLOAK_JWKS_URL: str = Field(
        default="",
        description="JWKS endpoint. Typically "
        "{KEYCLOAK_ISSUER}/protocol/openid-connect/certs. If left blank, "
        "derived from KEYCLOAK_ISSUER at startup.",
    )
    KEYCLOAK_CLIENT_ID: str = "contact-ops-mcp"
    KEYCLOAK_AUDIENCE: str | None = None
    JWKS_CACHE_TTL_SECONDS: int = 300
    JWT_ALLOWED_ALGORITHMS: list[str] = ["RS256"]

    # --- Phase 4.3 workspace-switch (silent re-auth) confidential client ---
    # The backend uses this confidential client's client-credentials grant to set
    # a user's tenant_id/tenant_slug attributes via the Keycloak Admin REST API
    # on a workspace switch; the SPA then does a prompt=none signinSilent so the
    # existing contact-ops-app User Attribute mappers stamp the new claims. The
    # token URL and Admin REST base are derived from KEYCLOAK_ISSUER at call time
    # (see api/auth.py: _kc_token_url / _kc_admin_user_url) — no separate base URL
    # setting is needed. Created by scripts/keycloak/setup_contact_ops_switch.sh.
    # When KEYCLOAK_SWITCH_CLIENT_SECRET is empty the switch endpoint is INERT
    # (returns 503), so deploying this code before the KC client exists is safe.
    KEYCLOAK_SWITCH_CLIENT_ID: str = "contact-ops-switch"
    KEYCLOAK_SWITCH_CLIENT_SECRET: str = ""

    # OBO trust — which upstream client IDs are allowed to act on behalf of
    # users via RFC 8693 token exchange. Empty by default; ops sets this.
    TRUSTED_MCP_CLIENTS: list[str] = []

    # Service mode — TRUE means JWT validation is bypassed and a fake
    # standalone user is used. Refuses to enable in `prod`.
    STANDALONE_MODE: bool = False
    STANDALONE_TENANT_ID: str = "00000000-0000-0000-0000-000000000001"
    STANDALONE_USER_ID: str = "standalone-user"

    # CORS — empty by default. Set to a list of origins in dev/prod via env.
    CORS_ORIGINS: list[str] = []
    CORS_ALLOW_CREDENTIALS: bool = False

    # Qdrant
    QDRANT_URL: str = "http://unicorn-qdrant:6333"
    QDRANT_API_KEY: str = ""

    # FalkorDB — on bigboy (per Brigade pattern). Tenancy mode + per-org
    # graph naming match Meeting-Ops convention.
    FALKORDB_URL: str = "redis://unicorn-falkordb:6379"
    FALKORDB_GRAPH_MODE: Literal["shared", "per_org_graph", "per_org_instance"] = (
        "per_org_graph"
    )
    FALKORDB_GRAPH_PREFIX: str = "contact_ops__"

    # Garage object storage
    GARAGE_ENDPOINT: str = "http://unicorn-garage:3900"
    GARAGE_ADMIN_ENDPOINT: str = "http://unicorn-garage:3903"
    GARAGE_ACCESS_KEY: str = ""
    GARAGE_SECRET_KEY: str = ""
    # Garage admin-API bearer token (v1). Promoted from a getattr in
    # garage_lifecycle.py to a real field. Empty = the control plane cannot call
    # the admin API, so auto-provision no-ops.
    GARAGE_ADMIN_TOKEN: str = ""
    # Per-tenant storage auto-provisioning (P-00075). DORMANT by default: with
    # this False, tenant signup never touches Garage (a self-served tenant's
    # photo/voice buckets simply aren't created until the flag is flipped + the
    # backfill CLI is run). It also no-ops when DEPLOYMENT_MODE=self_host (the
    # operator owns Garage), in STANDALONE/local-fs mode, when the admin token is
    # absent, or when GARAGE_ACCESS_KEY is still the placeholder sentinel.
    GARAGE_AUTO_PROVISION: bool = False
    # shared_key (default, NO schema change): grant the existing shared
    # GARAGE_ACCESS_KEY on each per-tenant bucket. per_tenant_key (dormant/future)
    # mints a dedicated key per tenant (needs a secret store + the 0042
    # garage_access_key_id column — not wired in v1).
    GARAGE_PROVISION_MODE: Literal["shared_key", "per_tenant_key"] = "shared_key"
    # Transactional email (P-00075). DORMANT by default: with POSTMARK_SERVER_TOKEN
    # unset OR EMAIL_SENDING_ENABLED false, the provider is a Noop that logs
    # `email_would_send` and never calls out (mirrors keycloak_admin.is_configured
    # + the entitlement shadow-first posture). Scope is APP-generated mail (member
    # invites, GDPR/DSR notices) — account verify/reset are Keycloak's job. EMAIL_FROM
    # MUST be a confirmed Postmark Sender Signature before flipping sending on, or
    # every send 422s. Reuse the suite's ONE shared Postmark server token.
    POSTMARK_SERVER_TOKEN: str = ""
    EMAIL_FROM: str = "REMOVED"
    EMAIL_SENDING_ENABLED: bool = False
    EMAIL_MESSAGE_STREAM: str = "outbound"
    # When false (default) invite_workspace_member keeps the USER_NOT_REGISTERED
    # hard-reject for brand-new emails; true enables pending-invite emails (the
    # pending-invitations table lands with the Signup milestone).
    EMAIL_INVITES_ALLOW_PENDING: bool = False
    # Internet-exposure hardening (P-00075). Four independent middlewares/guards,
    # ALL dormant by default (each *_ENABLED=False; main.py only mounts the ones
    # that are on) + shadow-first where they can block, so flipping them on is a
    # config exercise. See contact_ops/middleware/{security_headers,body_size,
    # rate_limit}.py + contact_ops/security/ssrf.py.
    # -- security response headers --
    SECURITY_HEADERS_ENABLED: bool = False
    SECURITY_HEADERS_SHADOW_MODE: bool = True  # log would-set, attach nothing
    SECURITY_HSTS_ENABLED: bool = False  # never pin HTTPS in dev/self-host over http
    CONTENT_SECURITY_POLICY: str = "default-src 'none'; frame-ancestors 'none'"
    # -- request body size cap --
    BODY_SIZE_LIMIT_ENABLED: bool = False
    BODY_SIZE_SHADOW_MODE: bool = True  # log would-413, allow through
    MAX_REQUEST_BODY_BYTES: int = 10485760  # 10 MiB global default
    BODY_SIZE_PATH_OVERRIDES: dict[str, int] = {}  # path-prefix -> larger cap (bulk import)
    # -- rate limiting (Redis-backed) --
    RATE_LIMIT_ENABLED: bool = False
    RATE_LIMIT_SHADOW: bool = True  # log would-block, allow
    RATE_LIMIT_DEFAULT: str = "120/minute"
    TRUSTED_PROXY_IPS: list[str] = []  # Traefik IP/CIDR; only then trust X-Forwarded-For
    # -- SSRF egress guard --
    SSRF_GUARD_ENABLED: bool = False
    SSRF_SHADOW: bool = True  # log ssrf_would_block, allow — observe internal targets first
    SSRF_HTTPS_ONLY: bool = False  # suite uses http:// internal URLs
    SSRF_ALLOW_PRIVATE_NETWORKS: bool = False  # block private once enabled; allowlist re-permits
    # Suite-internal egress hosts that bypass the IP check (so federation/CardDAV
    # to private targets keeps working when the guard is enforced).
    SSRF_ALLOWED_HOSTS: list[str] = []
    # Billing / metering (P-00075). Suite-credits + Pro-allowance model: CO's
    # metered GPU ops (the entitlement _PRO_TOOLS) emit usage to the shared suite
    # Lago and check a per-plan quota. DORMANT by default: BILLING_PROVIDER
    # 'manual' is a Noop (logs billing_would_emit, zero external calls); the Lago
    # provider also self-disables when LAGO_API_URL/KEY are blank. The quota gate
    # is shadow-first (BILLING_QUOTA_ENFORCED false → log quota_would_deny, allow)
    # and self_host bypasses both metering AND quota. Plan numbers live in
    # contact_ops/billing/catalog.py (tune later); the tier model is reused from
    # entitlement.py, never forked.
    BILLING_PROVIDER: Literal["manual", "lago", "federated"] = "manual"
    LAGO_API_URL: str = ""  # e.g. http://unicorn-lago-api:3000 (internal)
    LAGO_API_KEY: str = ""  # CO-scoped suite Lago key; empty = Lago leg inert
    BILLING_QUOTA_ENFORCED: bool = False

    # GDPR compliance engine (P-00075). DORMANT master switch for the Art.17
    # erasure + Art.15 export tools. With COMPLIANCE_ENGINE_ENABLED False the
    # erase tool takes the immediate glass-break path (still honest); True turns
    # on the two-phase tombstone (mark + grace, the sweep purges later) so the
    # returned undo window is REAL. ERASURE_GRACE_DAYS is that window;
    # EXPORT_SIGNED_URL_TTL_SECONDS bounds the Garage download link lifetime.
    COMPLIANCE_ENGINE_ENABLED: bool = False
    ERASURE_GRACE_DAYS: int = 30
    EXPORT_SIGNED_URL_TTL_SECONDS: int = 900
    # GDPR compliance — retention sweep (Art.17 grace-purge + Art.5(1)(e)
    # storage limitation). DORMANT by default, mirroring the entitlement/billing
    # "present but OFF" posture. RETENTION_SWEEP_ENABLED gates the daily Celery
    # beat task (contact_ops.agents.retention_sweep) at the top level: with it
    # False the task logs `retention_sweep_disabled` and touches nothing.
    # RETENTION_SWEEP_SHADOW (default True) is the second safety: even with the
    # sweep ENABLED, shadow mode only LOGS `retention_would_purge` with counts
    # and purges NOTHING — flip it False to actually hard-purge. This two-flag
    # rollout matches MEMBERSHIP_GATE_ENFORCED / ENTITLEMENT_ENFORCED: observe in
    # logs first, enforce second. The sweep enumerates tenants via the admin
    # (BYPASSRLS) MIGRATION_DATABASE_URL then purges RLS-bound under the app role;
    # it NEVER touches legal_hold rows or the non-expiring retention classes.
    RETENTION_SWEEP_ENABLED: bool = False
    RETENTION_SWEEP_SHADOW: bool = True
    # Per-class TTL (days) for the retention-ageing path, measured from
    # last_interaction_at (falling back to created_at). 'ephemeral_30d' → 30,
    # 'operational_2y' → 730. 'legal_hold' / 'hipaa_6y' / 'indefinite' are
    # NON-EXPIRING and excluded from the sweep entirely (hard invariant, not a
    # tunable). Defaults match the class names so the numbers are self-evident;
    # they're settings only so an operator can lengthen a grace window without a
    # code change.
    RETENTION_EPHEMERAL_TTL_DAYS: int = 30
    RETENTION_OPERATIONAL_TTL_DAYS: int = 730
    # Max persons purged per tenant per tick. Bounds blast radius + keeps each
    # daily run cheap; the backlog drains over subsequent days. Raise for a
    # one-off catch-up, then lower again.
    RETENTION_SWEEP_BATCH_LIMIT: int = 500

    # Data Intel federation (Phase 3+; stubs only in Phase 0)
    DATA_INTEL_BASE_URL: str = ""
    DATA_INTEL_CLIENT_ID: str = "contact-ops-publisher"
    DATA_INTEL_TOKEN_EXCHANGE_URL: str = ""

    # Brigade ecosystem registry (per the federation integration spec the
    # ecosystem standardizes on brigade.unicorncommander.ai as the gateway).
    BRIGADE_REGISTRY_URL: str = "https://brigade.unicorncommander.ai/api/registry/services"
    BRIGADE_SERVICE_TOKEN: str = ""
    # Brigade-JWT verifier inputs (used by the verifier the follow-up Codex
    # prompt ships). expected_audience MUST match what Brigade signs tokens
    # with for THIS app; pick a stable string and never change it.
    BRIGADE_TRUSTED_ISSUER: str = "https://brigade.unicorncommander.ai"
    # Multi-issuer trust (CSV). When set, Contact-Ops accepts Brigade JWTs from
    # ANY listed issuer (e.g. a customer broker + the dogfood broker during a
    # sovereign-federation migration) — each verified against its OWN JWKS and
    # bound to its own keys, so a token's signing key MUST belong to the issuer
    # it claims (see brigade_jwt_verifier: the iss-key-mismatch guard blocks one
    # trusted broker from impersonating another). Empty -> falls back to the
    # singular BRIGADE_TRUSTED_ISSUER. Inert unless set: backward compatible.
    BRIGADE_TRUSTED_ISSUERS: str = ""
    BRIGADE_JWKS_URL: str = "https://brigade.unicorncommander.ai/.well-known/jwks.json"
    BRIGADE_EXPECTED_AUDIENCE: str = "contact-ops"

    # Seed data
    SEED_OWNER_USER_ID: str = "00000000-0000-0000-0000-000000000001"

    @field_validator("KEYCLOAK_JWKS_URL", mode="before")
    @classmethod
    def _derive_jwks_url(cls, v: str, info) -> str:  # type: ignore[no-untyped-def]
        if v:
            return v
        issuer = info.data.get("KEYCLOAK_ISSUER", "")
        if issuer:
            return f"{issuer.rstrip('/')}/protocol/openid-connect/certs"
        return ""

    @field_validator("KEYCLOAK_AUDIENCE", mode="before")
    @classmethod
    def _default_audience(cls, v: str | None, info) -> str:  # type: ignore[no-untyped-def]
        if v:
            return v
        return info.data.get("KEYCLOAK_CLIENT_ID", "contact-ops-mcp")

    @model_validator(mode="after")
    def _fail_closed_standalone(self) -> Settings:
        if self.STANDALONE_MODE and self.ENV == "prod":
            raise ValueError(
                "STANDALONE_MODE=true is not permitted when ENV=prod. "
                "Set STANDALONE_MODE=false or change ENV."
            )
        return self

    @model_validator(mode="after")
    def _require_keycloak_in_non_standalone(self) -> Settings:
        if not self.STANDALONE_MODE and not self.KEYCLOAK_ISSUER:
            raise ValueError(
                "KEYCLOAK_ISSUER is required when STANDALONE_MODE=false. "
                "Set it to your realm URL, e.g. "
                "https://auth.unicorncommander.ai/realms/uchub"
            )
        return self

    @model_validator(mode="after")
    def _require_connector_key_in_non_standalone(self) -> Settings:
        if not self.STANDALONE_MODE and self.ENV != "test" and not self.CONNECTOR_ENCRYPTION_KEY:
            raise ValueError(
                "CONNECTOR_ENCRYPTION_KEY is required when STANDALONE_MODE=false. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            )
        return self

    @model_validator(mode="after")
    def _default_audit_url(self) -> Settings:
        if self.AUDIT_DATABASE_URL is None:
            self.AUDIT_DATABASE_URL = self.DATABASE_URL
        return self

    @property
    def is_development(self) -> bool:
        return self.ENV in ("dev", "test")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
