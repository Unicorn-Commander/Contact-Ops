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
    APP_VERSION: str = "0.1.0"
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

    # Brigade ecosystem registry
    BRIGADE_REGISTRY_URL: str = "https://brigade.magicunicorn.dev/api/registry/services"
    BRIGADE_SERVICE_TOKEN: str = ""

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
