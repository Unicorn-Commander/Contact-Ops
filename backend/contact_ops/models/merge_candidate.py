import uuid as uuid_module
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base


class MergeCandidate(Base):
    __tablename__ = "merge_candidate"

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    tenant_id: Mapped[uuid_module.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    person_a_id: Mapped[uuid_module.UUID] = mapped_column(ForeignKey("persons.id"), nullable=False)
    person_b_id: Mapped[uuid_module.UUID] = mapped_column(ForeignKey("persons.id"), nullable=False)
    score: Mapped[float] = mapped_column(sa.Float, nullable=False)
    score_breakdown: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    blocking_keys: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=sa.text("'{}'::text[]"))
    last_scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_eligible_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(Text)
