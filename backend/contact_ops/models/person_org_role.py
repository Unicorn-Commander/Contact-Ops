import uuid as uuid_module
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base
from contact_ops.models.enums import EmploymentType, RoleType, SeniorityLevel, enum_values


class PersonOrgRole(Base):
    __tablename__ = "person_org_role"

    id: Mapped[uuid_module.UUID] = mapped_column(
        PGUUID,
        primary_key=True,
        server_default=sa.text("uuidv7_generate()"),
    )
    person_id: Mapped[uuid_module.UUID] = mapped_column(ForeignKey("persons.id", ondelete="CASCADE"))
    organization_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    role_type: Mapped[RoleType] = mapped_column(
        Enum(RoleType, name="role_type", create_type=False, values_callable=enum_values),
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(Text)
    seniority: Mapped[SeniorityLevel] = mapped_column(
        Enum(
            SeniorityLevel,
            name="seniority_level",
            create_type=False,
            values_callable=enum_values,
        ),
        nullable=False,
        server_default=sa.text("'unknown'"),
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.text("false"))
    started_at: Mapped[date | None] = mapped_column(Date)
    ended_at: Mapped[date | None] = mapped_column(Date)
    employment_type: Mapped[EmploymentType | None] = mapped_column(
        Enum(
            EmploymentType,
            name="employment_type",
            create_type=False,
            values_callable=enum_values,
        )
    )
    ownership_percent: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    equity_class: Mapped[str | None] = mapped_column(Text)
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
