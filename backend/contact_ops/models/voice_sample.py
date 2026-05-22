"""voice_samples table ORM mapping.

Schema authority: alembic/versions/0005_media.py. One row per captured audio
clip with a Parakeet (or compatible) 256-dim speaker embedding. Linked to
``voice_fingerprints`` once the sample is admitted into the aggregate.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base


class VoiceSample(Base):
    __tablename__ = "voice_samples"

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    asset_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[uuid_module.UUID | None] = mapped_column(
        ForeignKey("persons.id", ondelete="SET NULL")
    )
    voice_fingerprint_id: Mapped[uuid_module.UUID | None] = mapped_column(
        ForeignKey("voice_fingerprints.id")
    )
    embedding: Mapped[object] = mapped_column(Vector(256), nullable=False)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float)
    snr_db: Mapped[float | None] = mapped_column(Float)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meeting_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    speaker_label: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
