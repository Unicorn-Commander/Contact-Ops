import uuid as uuid_module
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base
from contact_ops.models.enums import ConsentBasis, enum_values


class PersonTenantMembership(Base):
    __tablename__ = "person_tenant_membership"

    person_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa.text("'visible'"))
    notes: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=sa.text("'{}'::text[]"),
    )
    custom_attrs: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    consent_basis: Mapped[ConsentBasis | None] = mapped_column(
        Enum(ConsentBasis, name="consent_basis", create_type=False, values_callable=enum_values)
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    added_by: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
