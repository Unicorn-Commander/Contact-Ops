"""
API Key model for the api_keys table.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ApiKey(Base):
    """
    Represents an API key for application authentication.

    Manages API keys with rate limiting, usage tracking,
    and expiration support.
    """

    __tablename__ = "api_keys"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    key_prefix: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        index=True,
    )
    key_hash: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )
    app_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    tier: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="standard",
    )
    rate_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1000,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
    )
    request_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )

    # Indexes
    __table_args__ = (
        Index("ix_api_keys_is_active", "is_active"),
        Index("ix_api_keys_tier", "tier"),
        Index("ix_api_keys_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<ApiKey(id={self.id}, app='{self.app_name}', active={self.is_active})>"

    @property
    def is_expired(self) -> bool:
        """Check if the API key has expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    @property
    def is_valid(self) -> bool:
        """Check if the API key is valid (active and not expired)."""
        return self.is_active and not self.is_expired
