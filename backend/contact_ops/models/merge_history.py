import uuid as uuid_module
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base


class MergeHistory(Base):
    __tablename__ = "merge_history"

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    tenant_id: Mapped[uuid_module.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    kept_id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, nullable=False)
    removed_id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    signals: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    performed_by_actor_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    performed_event_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("action_event.event_id"),
        nullable=False,
    )
    reversible_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reverted_event_id: Mapped[uuid_module.UUID | None] = mapped_column(
        ForeignKey("action_event.event_id"),
    )
    merge_activity_id: Mapped[uuid_module.UUID | None] = mapped_column(ForeignKey("prov_activities.id"))
    merge_confidence: Mapped[float | None] = mapped_column(sa.Float)
    can_unmerge: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("true"))
    evidence_pack_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
