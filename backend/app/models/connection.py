"""
Connection model for the catalogue_connections table.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Connection(Base):
    """
    Represents a relationship between two entities in the catalogue.

    Tracks connections like: person works_at company, person owns company,
    company acquired_by company, etc.
    """

    __tablename__ = "catalogue_connections"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    from_entity_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    from_entity_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    to_entity_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    to_entity_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    relationship_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )
    source_app: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    source_url: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    connection_metadata: Mapped[Optional[dict]] = mapped_column(
        "metadata",  # Keep DB column name as "metadata"
        JSONB,
        nullable=True,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )

    # Indexes
    __table_args__ = (
        Index("ix_catalogue_connections_from", "from_entity_type", "from_entity_id"),
        Index("ix_catalogue_connections_to", "to_entity_type", "to_entity_id"),
        Index("ix_catalogue_connections_relationship", "relationship_type"),
        Index("ix_catalogue_connections_source_app", "source_app"),
    )

    def __repr__(self) -> str:
        return f"<Connection(id={self.id}, {self.from_entity_type}:{self.from_entity_id} -{self.relationship_type}-> {self.to_entity_type}:{self.to_entity_id})>"
