import uuid as uuid_module
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Numeric, SmallInteger, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base
from contact_ops.models.enums import LineType, PhoneType, enum_values


class Phone(Base):
    __tablename__ = "phones"

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
    e164: Mapped[str] = mapped_column(Text, nullable=False)
    extension: Mapped[str | None] = mapped_column(Text)
    type: Mapped[PhoneType] = mapped_column(
        Enum(PhoneType, name="phone_type", create_type=False, values_callable=enum_values),
        nullable=False,
        server_default=sa.text("'other'"),
    )
    label: Mapped[str | None] = mapped_column(Text)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"))
    is_sms_capable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"))
    is_whatsapp: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"))
    is_signal: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"))
    is_imessage: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"))
    opted_out_sms: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"))
    do_not_call: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"))
    carrier: Mapped[str | None] = mapped_column(Text)
    line_type: Mapped[LineType] = mapped_column(
        Enum(LineType, name="line_type", create_type=False, values_callable=enum_values),
        nullable=False,
        server_default=sa.text("'unknown'"),
    )
    country_code: Mapped[int | None] = mapped_column(SmallInteger)
    national_number: Mapped[str | None] = mapped_column(Text)
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
