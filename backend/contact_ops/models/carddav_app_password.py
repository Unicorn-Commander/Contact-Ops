"""ORM model for the per-device CardDAV app-password table."""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import ARRAY, INET, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base


class CarddavAppPassword(Base):
    """A bcrypt-hashed app password bound to (tenant_id, user_id, device_label).

    The plaintext is shown ONCE at generation time and never persisted.
    All verification reads the ``password_hash`` column. Revocation is a
    soft-delete via ``revoked_at`` so audit history is preserved.
    """

    __tablename__ = "carddav_app_passwords"

    id: Mapped[uuid_module.UUID] = mapped_column(
        PGUUID,
        primary_key=True,
        server_default=sa.text("uuidv7_generate()"),
    )
    tenant_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    # uc_uid string — matches the JWT `sub`/`uc_uid` claim that the
    # CardDAV client supplies as the HTTP Basic username.
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    device_label: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    last_4_chars: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=sa.text("ARRAY['carddav:read','carddav:write']::text[]"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    created_by_actor_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_user_agent: Mapped[str | None] = mapped_column(Text)
    # asyncpg ships INET as ipaddress.IPv*Address — model as string for simplicity.
    last_used_ip: Mapped[str | None] = mapped_column(INET)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_actor_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
