import uuid as uuid_module
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base
from contact_ops.models.enums import SourceType, enum_values


class FieldProvenance(Base):
    __tablename__ = "field_provenance"

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "field_path"),
    )

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, nullable=False)
    field_path: Mapped[str] = mapped_column(Text, nullable=False)
    current_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    set_by_event_id: Mapped[uuid_module.UUID | None] = mapped_column(ForeignKey("action_event.event_id"))
    set_by_actor: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type", create_type=False, values_callable=enum_values),
        nullable=False,
    )
    source_record_id: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    established_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    history: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))
