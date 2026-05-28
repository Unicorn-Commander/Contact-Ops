from __future__ import annotations

import uuid as uuid_module
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[uuid_module.UUID] = mapped_column(
        PGUUID,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid_module.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    author_type: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
