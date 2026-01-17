"""
Configuration settings for Center Deep Data Intel API.

Uses pydantic-settings for environment variable parsing with validation.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    All settings have sensible defaults for development but should be
    overridden in production via environment variables.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    APP_NAME: str = "Center Deep Data Intel"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://unicorn:CCCzgFZlaDb0JSL1xdPAzwGO@unicorn-postgresql:5432/dataintel_db"

    # Redis
    REDIS_URL: str = "redis://unicorn-redis:6379/5"

    # Security
    API_KEY_HEADER: str = "Authorization"
    STANDALONE_MODE: bool = False

    # SMTP Verification
    SMTP_TIMEOUT: int = 10
    SMTP_CONCURRENCY: int = 10

    # Phone Verification
    VERIPHONE_API_KEY: str = ""  # Optional: 1,000 free verifications/month

    # Keycloak JWT validation (when not in standalone mode)
    KEYCLOAK_REALM_URL: str = ""
    KEYCLOAK_CLIENT_ID: str = ""

    # Keycloak Admin (for subscription lookup)
    KEYCLOAK_ADMIN_URL: str = "http://uchub-keycloak:8080"
    KEYCLOAK_ADMIN_SECRET: str = ""

    # Lago Billing
    LAGO_API_URL: str = ""
    LAGO_API_KEY: str = ""

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.DEBUG or self.STANDALONE_MODE


@lru_cache
def get_settings() -> Settings:
    """
    Get cached settings instance.

    Uses lru_cache to avoid re-parsing environment variables on every call.
    """
    return Settings()
