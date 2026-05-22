"""organization_tenant_membership table ORM mapping.

Schema authority: alembic/versions/0004_membership_contact_attributes.py. Per-
tenant shadow of a canonical organization. Mirrors PersonTenantMembership.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base


class OrganizationTenantMembership(Base):
    __tablename__ = "organization_tenant_membership"

    organization_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    visibility: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sa.text("'visible'")
    )
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
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    added_by: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
