"""
Source model for the catalogue_sources table.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Source(Base):
    """
    Represents a data source record for entity tracking.

    Tracks which applications contributed data to each entity
    (company, contact, or email) and what data they provided.
    """

    __tablename__ = "catalogue_sources"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    entity_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )
    entity_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    source_app: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )
    source_record_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    source_url: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    contributed_data: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )

    # Indexes
    __table_args__ = (
        Index("ix_catalogue_sources_entity", "entity_type", "entity_id"),
        Index("ix_catalogue_sources_source_app_entity", "source_app", "entity_type"),
        Index("ix_catalogue_sources_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Source(id={self.id}, app='{self.source_app}', entity='{self.entity_type}:{self.entity_id}')>"
