"""acl_grant table ORM mapping.

Schema authority: alembic/versions/0018_acl_grant.py (added by Phase 2).
Per-record share grants. Narrower than tenant membership; allows a user (or
a peer tenant) read/write/merge access to a specific person or organization
without granting access to the wider tenant view.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base

ACL_SUBJECT_KINDS = ("person", "org")
ACL_SCOPES = ("read", "write", "merge")


class AclGrant(Base):
    __tablename__ = "acl_grant"

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    tenant_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    subject_kind: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, nullable=False)
    grantee_user_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    grantee_tenant_id: Mapped[uuid_module.UUID | None] = mapped_column(
        ForeignKey("tenants.id")
    )
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    granted_by_actor_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_actor_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
