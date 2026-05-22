"""media_assets table ORM mapping.

Schema authority: alembic/versions/0005_media.py.
`kind` is a CHECK-constrained text column, not a PG enum, so we keep it as a
plain Python ``str`` Literal on the way in and ``Text`` on the way out.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base
from contact_ops.models.enums import RetentionClass, enum_values

MEDIA_ASSET_KINDS = (
    "photo",
    "voice",
    "video",
    "business_card",
    "vcard",
    "document",
    "evidence",
    "logo",
    "generic",
)


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    tenant_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False
    )
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exif: Mapped[dict | None] = mapped_column(JSONB)
    retention_class: Mapped[RetentionClass] = mapped_column(
        Enum(
            RetentionClass,
            name="retention_class",
            create_type=False,
            values_callable=enum_values,
        ),
        nullable=False,
        server_default=sa.text("'operational_2y'"),
    )
    legal_hold: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
