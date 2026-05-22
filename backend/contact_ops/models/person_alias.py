import uuid as uuid_module
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base


class PersonAlias(Base):
    __tablename__ = "person_alias"

    alias_id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    current_canonical_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
    )
    merged_at_event_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("action_event.event_id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
