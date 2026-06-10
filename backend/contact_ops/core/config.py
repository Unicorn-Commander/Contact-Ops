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
    APP_VERSION: str = "2.7.0"
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
