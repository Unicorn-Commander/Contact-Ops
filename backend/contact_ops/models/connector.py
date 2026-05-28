from __future__ import annotations

import uuid as uuid_module
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base


class ConnectorConfig(Base):
    __tablename__ = "connector_configs"

    id: Mapped[uuid_module.UUID] = mapped_column(
        PGUUID,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid_module.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    configured_by: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=sa.text("'configured'"),
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    configured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    last_pull_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_pull_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ConnectorRun(Base):
    __tablename__ = "connector_runs"

    id: Mapped[uuid_module.UUID] = mapped_column(
        PGUUID,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid_module.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    connector_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("connector_configs.id", ondelete="CASCADE"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa.text("'running'"))
    parsed_count: Mapped[int] = mapped_column(Integer, server_default=sa.text("0"))
    proposed_count: Mapped[int] = mapped_column(Integer, server_default=sa.text("0"))
    deduped_count: Mapped[int] = mapped_column(Integer, server_default=sa.text("0"))
    skipped_count: Mapped[int] = mapped_column(Integer, server_default=sa.text("0"))
    error_message: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str] = mapped_column(Text, nullable=False)
