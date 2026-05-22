import uuid as uuid_module
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[uuid_module.UUID] = mapped_column(
        PGUUID,
        primary_key=True,
        server_default=sa.text("uuidv7_generate()"),
    )
    tenant_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    display: Mapped[str] = mapped_column(Text, nullable=False)
    color: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    parent_tag_id: Mapped[uuid_module.UUID | None] = mapped_column(ForeignKey("tags.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
