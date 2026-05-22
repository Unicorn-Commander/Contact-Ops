import uuid as uuid_module
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import CITEXT, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base
from contact_ops.models.enums import EmailDeliverability, EmailType, enum_values


class Email(Base):
    __tablename__ = "emails"

    id: Mapped[uuid_module.UUID] = mapped_column(
        PGUUID,
        primary_key=True,
        server_default=sa.text("uuidv7_generate()"),
    )
    person_id: Mapped[uuid_module.UUID | None] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE")
    )
    organization_id: Mapped[uuid_module.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    address: Mapped[str] = mapped_column(CITEXT, nullable=False)
    type: Mapped[EmailType] = mapped_column(
        Enum(EmailType, name="email_type", create_type=False, values_callable=enum_values),
        nullable=False,
        server_default=sa.text("'other'"),
    )
    label: Mapped[str | None] = mapped_column(Text)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"))
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deliverability_status: Mapped[EmailDeliverability] = mapped_column(
        Enum(
            EmailDeliverability,
            name="email_deliverability",
            create_type=False,
            values_callable=enum_values,
        ),
        nullable=False,
        server_default=sa.text("'unknown'"),
    )
    last_bounced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bounce_reason: Mapped[str | None] = mapped_column(Text)
    opted_out: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"))
    source_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 3),
        nullable=False,
        server_default=sa.text("1.000"),
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
