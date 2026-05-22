import uuid as uuid_module
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base
from contact_ops.models.enums import TrustTier, VisibilityScope, enum_values


class AgentRegistry(Base):
    __tablename__ = "agent_registry"

    id: Mapped[uuid_module.UUID] = mapped_column(
        PGUUID,
        primary_key=True,
        server_default=sa.text("uuidv7_generate()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    owning_system: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=sa.text("'contact-ops'"),
    )
    owning_user_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    owning_tenant_id: Mapped[uuid_module.UUID | None] = mapped_column(ForeignKey("tenants.id"))
    scope_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa.text("'shared'"))
    visibility: Mapped[VisibilityScope] = mapped_column(
        Enum(VisibilityScope, name="visibility_scope", create_type=False, values_callable=enum_values),
        nullable=False,
        server_default=sa.text("'private'"),
    )
    declared_capabilities: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=sa.text("'{}'::text[]"),
    )
    current_trust_tier: Mapped[TrustTier] = mapped_column(
        Enum(TrustTier, name="trust_tier", create_type=False, values_callable=enum_values),
        nullable=False,
        server_default=sa.text("'propose_only'"),
    )
    trust_tier_overrides: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    calibration_history: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
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
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
