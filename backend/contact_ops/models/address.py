import uuid as uuid_module
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Boolean, CHAR, DateTime, Enum, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base
from contact_ops.models.enums import AddressType, AddressVerifiedVia, GeoPrecision, enum_values


class PostalAddress(Base):
    __tablename__ = "postal_addresses"

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
    formatted: Mapped[str | None] = mapped_column(Text)
    po_box: Mapped[str | None] = mapped_column(Text)
    street_address: Mapped[str | None] = mapped_column(Text)
    extended_address: Mapped[str | None] = mapped_column(Text)
    locality: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    region_code: Mapped[str | None] = mapped_column(Text)
    postal_code: Mapped[str | None] = mapped_column(Text)
    country_name: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(CHAR(2))
    geo_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    geo_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    geo_precision: Mapped[GeoPrecision] = mapped_column(
        Enum(GeoPrecision, name="geo_precision", create_type=False, values_callable=enum_values),
        nullable=False,
        server_default=sa.text("'unknown'"),
    )
    type: Mapped[AddressType] = mapped_column(
        Enum(AddressType, name="address_type", create_type=False, values_callable=enum_values),
        nullable=False,
        server_default=sa.text("'other'"),
    )
    label: Mapped[str | None] = mapped_column(Text)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"))
    verified_via: Mapped[AddressVerifiedVia] = mapped_column(
        Enum(
            AddressVerifiedVia,
            name="address_verified_via",
            create_type=False,
            values_callable=enum_values,
        ),
        nullable=False,
        server_default=sa.text("'unverified'"),
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
