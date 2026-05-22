import uuid as uuid_module
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base


class Fact(Base):
    __tablename__ = "facts"

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    tenant_id: Mapped[uuid_module.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    subject_kind: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, nullable=False)
    predicate: Mapped[str] = mapped_column(Text, nullable=False)
    object_kind: Mapped[str] = mapped_column(Text, nullable=False)
    object_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    object_ref_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    source_id: Mapped[uuid_module.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_fact_id: Mapped[uuid_module.UUID | None] = mapped_column(ForeignKey("facts.id"))
    human_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"))
    verified_by_actor_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
