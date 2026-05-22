"""voice_fingerprints table ORM mapping.

Schema authority: alembic/versions/0005_media.py. The aggregated speaker
embedding per person, used as the canonical voice identity that ``persons``
points at via ``voice_fingerprint_id``.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base


class VoiceFingerprint(Base):
    __tablename__ = "voice_fingerprints"

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    person_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), nullable=False
    )
    embedding: Mapped[object] = mapped_column(Vector(256), nullable=False)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    sample_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa.text("0")
    )
    total_duration_seconds: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, server_default=sa.text("0")
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
