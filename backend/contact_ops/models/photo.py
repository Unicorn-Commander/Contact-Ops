"""photos table ORM mapping.

Schema authority: alembic/versions/0005_media.py. Photos are person-only; the
table has no organization_id column. Organization logos are referenced via
``organizations.logo_asset_id`` which points directly at a media_asset.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base


class Photo(Base):
    __tablename__ = "photos"

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    asset_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), nullable=False
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    source_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    face_embedding: Mapped[object | None] = mapped_column(Vector(512))
    face_bbox: Mapped[dict | None] = mapped_column(JSONB)
    quality_score: Mapped[float | None] = mapped_column(Float)
    is_redacted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
