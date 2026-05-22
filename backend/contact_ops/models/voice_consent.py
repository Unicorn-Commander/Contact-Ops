"""voice_consent table ORM mapping.

Schema authority: Phase 3 Design §3.8 and alembic/versions/0022_phase_3_voice_match.py.
GDPR Article 9 + BIPA-compliant voice biometric consent record.

Enforcement: the Voice Match Agent's pre-extract check refuses any embedding
write where there is no active row in voice_consent. The Qdrant payload filter
``consent_active = true`` is the second line of defense.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base


class VoiceConsent(Base):
    __tablename__ = "voice_consent"

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    person_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[uuid_module.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    consent_granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consent_revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    consent_text_version: Mapped[str] = mapped_column(Text, nullable=False)
    consent_method: Mapped[str] = mapped_column(Text, nullable=False)
    consent_ip: Mapped[str | None] = mapped_column(INET)
    granted_by: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )

    __table_args__ = (
        UniqueConstraint("person_id", "tenant_id", "consent_granted_at"),
    )
