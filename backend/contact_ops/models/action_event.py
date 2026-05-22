import uuid as uuid_module
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base
from contact_ops.models.enums import ActorType, EntityKind, EventStatus, enum_values


class ActionEvent(Base):
    __tablename__ = "action_event"

    event_id: Mapped[uuid_module.UUID] = mapped_column(
        PGUUID,
        primary_key=True,
        server_default=sa.text("uuidv7_generate()"),
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa.text("1"))
    tenant_id: Mapped[uuid_module.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    aggregate_type: Mapped[EntityKind] = mapped_column(
        Enum(EntityKind, name="entity_kind", create_type=False, values_callable=enum_values),
        nullable=False,
    )
    aggregate_id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, nullable=False)
    affected_ids: Mapped[list[uuid_module.UUID]] = mapped_column(
        ARRAY(PGUUID),
        nullable=False,
        server_default=sa.text("'{}'::uuid[]"),
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    actor: Mapped[dict] = mapped_column(JSONB, nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(
        Enum(ActorType, name="actor_type", create_type=False, values_callable=enum_values),
        nullable=False,
    )
    human_authority: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    evidence: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus, name="event_status", create_type=False, values_callable=enum_values),
        nullable=False,
        server_default=sa.text("'proposed'"),
    )
    proposed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    reverted_by_event_id: Mapped[uuid_module.UUID | None] = mapped_column(
        ForeignKey("action_event.event_id", ondelete="SET NULL")
    )
    supersedes_event_id: Mapped[uuid_module.UUID | None] = mapped_column(
        ForeignKey("action_event.event_id", ondelete="SET NULL")
    )
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    prev_event_hash: Mapped[bytes | None] = mapped_column(BYTEA)
    signature: Mapped[bytes | None] = mapped_column(BYTEA)
