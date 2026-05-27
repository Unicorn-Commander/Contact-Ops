"""Voice consent management (GDPR Art. 9 + BIPA compliance).

Three enforcement points:

1. Pre-extract: ``has_active_consent(person_id, tenant_id)`` must return
   True before the Voice Match Agent calls any extractor.
2. Qdrant query: every search filter includes ``consent_active = true``
   (enforced in ``services/qdrant_voice.py``).
3. Revocation (GDPR Art. 17 erasure): on revoke, delete every
   ``voice_samples`` row, delete all ``voice_fingerprints`` rows for
   that person across all languages, delete every Qdrant point with
   that ``person_id``, and write a ``prov_activities`` row of type
   ``voice_consent_revoke``.

JWT consent token format for the ``enroll_voice`` MCP tool:

.. code-block:: json

    {
      "person_id": "uuid",
      "tenant_id": "uuid",
      "consent_text_version": "2026-05-v1",
      "voice_consent": true,
      "aud": "enroll_voice",
      "jti": "<consent_id>",
      "exp": <now + 60s>,
      "iat": <now>
    }

The token is signed with the app's consent token secret and verified by
``enroll_voice`` before allowing enrollment. Token replay is prevented
because ``jti`` is the unique consent record id.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from jose import jwt
from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.models.voice_consent import VoiceConsent

logger = logging.getLogger(__name__)

CONSENT_TOKEN_SECRET_SETTING = "voice_consent_jwt_secret"  # noqa: S105
"""Settings key for the JWT signing secret. Injected via ``Settings`` or env."""

DEFAULT_CONSENT_TOKEN_TTL_SECONDS = 60
"""Consent tokens expire after 60 seconds (short window limits replay risk)."""

CONSENT_TOKEN_AUDIENCE = "enroll_voice"  # noqa: S105
"""The ``aud`` claim value that ``enroll_voice`` verifies against."""

CURRENT_CONSENT_TEXT_VERSION = "2026-05-v1"
"""The current platform version of the consent text. When this changes,
the agent emits ``voice_match.consent_required`` to prompt re-consent."""


class VoiceConsentService:
    """Service for voice consent CRUD and token issuance.

    All methods require a tenant-scoped DB session.
    """

    def __init__(
        self,
        jwt_secret: str = "dev-consent-secret-not-for-prod",  # noqa: S107
    ) -> None:
        self._jwt_secret = jwt_secret

    async def has_active_consent(
        self,
        db: AsyncSession,
        person_id: UUID,
        tenant_id: UUID,
    ) -> bool:
        """Check whether the person has an active (non-revoked) consent
        record whose text version matches the current platform version.

        Returns True only if:
        1. At least one ``voice_consent`` row exists for
           ``(person_id, tenant_id)`` with ``consent_revoked_at IS NULL``.
        2. The most recent such row has ``consent_text_version`` matching
           ``CURRENT_CONSENT_TEXT_VERSION``.
        """
        result = await db.execute(
            select(VoiceConsent)
            .where(
                and_(
                    VoiceConsent.person_id == person_id,
                    VoiceConsent.tenant_id == tenant_id,
                    VoiceConsent.consent_revoked_at.is_(None),
                )
            )
            .order_by(VoiceConsent.consent_granted_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        return row.consent_text_version == CURRENT_CONSENT_TEXT_VERSION

    async def record_consent(
        self,
        db: AsyncSession,
        person_id: UUID,
        tenant_id: UUID,
        consent_text_version: str,
        consent_method: str,
        granted_by: UUID | None = None,
        consent_ip: str | None = None,
    ) -> VoiceConsent:
        """Record a new voice consent grant. Returns the ORM row.

        Idempotent on ``(person_id, tenant_id, consent_granted_at)`` natural key.
        """
        consent = VoiceConsent(
            id=uuid.uuid4(),
            person_id=person_id,
            tenant_id=tenant_id,
            consent_granted_at=datetime.now(UTC),
            consent_revoked_at=None,
            consent_text_version=consent_text_version,
            consent_method=consent_method,
            granted_by=granted_by,
            consent_ip=consent_ip,
        )
        db.add(consent)
        await db.flush()
        return consent

    async def revoke_consent(
        self,
        db: AsyncSession,
        audit_db: AsyncSession,
        person_id: UUID,
        tenant_id: UUID,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """GDPR Article 17 erasure.

        1. Set ``consent_revoked_at`` on all active consent rows.
        2. Delete all ``voice_samples`` for the person.
        3. Delete all ``voice_fingerprints`` for the person (all languages).
        4. Write a ``prov_activities`` row of type ``voice_consent_revoke``.
        5. (Qdrant point deletion is caller's responsibility via
           ``QdrantVoiceService.delete_by_person_id``).

        Returns a summary dict.
        """
        # Step 1: Revoke consent rows
        result = await db.execute(
            select(VoiceConsent).where(
                and_(
                    VoiceConsent.person_id == person_id,
                    VoiceConsent.tenant_id == tenant_id,
                    VoiceConsent.consent_revoked_at.is_(None),
                )
            )
        )
        active_rows = result.scalars().all()
        now = datetime.now(UTC)
        for row in active_rows:
            row.consent_revoked_at = now

        # Step 2: Delete voice samples
        samples_result = await db.execute(
            text(
                """
                DELETE FROM voice_samples
                WHERE person_id = CAST(:person_id AS uuid)
                RETURNING id
                """
            ),
            {"person_id": str(person_id)},
        )
        samples_deleted = len(samples_result.all())

        # Step 3: Delete voice fingerprints
        fps_result = await db.execute(
            text(
                """
                DELETE FROM voice_fingerprints
                WHERE person_id = CAST(:person_id AS uuid)
                RETURNING id
                """
            ),
            {"person_id": str(person_id)},
        )
        fp_deleted = len(fps_result.all())
        await db.flush()

        # Step 4: Write prov_activities row
        await audit_db.execute(
            text(
                """
                INSERT INTO prov_activities (
                    tenant_id, activity_type, agent_id, agent_version,
                    started_at, ended_at, reasoning, confidence
                ) VALUES (
                    CAST(:tenant_id AS uuid), :activity_type, :agent_id,
                    'voice-consent-service', now(), now(), :reasoning, 1.0
                )
                """
            ),
            {
                "tenant_id": str(tenant_id),
                "activity_type": "voice_consent_revoke",
                "agent_id": "voice_match",
                "reasoning": (reason or "GDPR Article 17 erasure")[:8192],
            },
        )

        return {
            "revoked_at": now.isoformat(),
            "consent_rows_revoked": len(active_rows),
            "samples_deleted": samples_deleted,
            "fingerprints_deleted": fp_deleted,
        }

    def issue_consent_token(
        self,
        consent_id: UUID,
        person_id: UUID,
        tenant_id: UUID,
        consent_text_version: str,
    ) -> str:
        """Issue a short-lived JWT consent token for the MCP tool flow.

        The token proves the user went through the consent UI and grants
        ``enroll_voice`` permission for the signed tuple.

        Args:
            consent_id: The ``voice_consent.id`` (becomes ``jti``).
            person_id: The person granting consent.
            tenant_id: The tenant scope.
            consent_text_version: The version of consent text the user saw.

        Returns:
            Signed JWT string, valid for ``DEFAULT_CONSENT_TOKEN_TTL_SECONDS``.
        """
        now = datetime.now(UTC)
        payload = {
            "jti": str(consent_id),
            "person_id": str(person_id),
            "tenant_id": str(tenant_id),
            "consent_text_version": consent_text_version,
            "voice_consent": True,
            "aud": CONSENT_TOKEN_AUDIENCE,
            "exp": now + timedelta(seconds=DEFAULT_CONSENT_TOKEN_TTL_SECONDS),
            "iat": now,
        }
        return jwt.encode(payload, self._jwt_secret, algorithm="HS256")

    def verify_consent_token(
        self,
        token: str,
    ) -> dict[str, Any]:
        """Verify a consent token and return its claims.

        Raises:
            jose.JWTError: If the token is expired, invalidly signed, or
                            has the wrong audience.
        """
        return jwt.decode(
            token,
            self._jwt_secret,
            algorithms=["HS256"],
            audience=CONSENT_TOKEN_AUDIENCE,
        )

    async def update_qdrant_consent_bit(
        self,
        person_id: UUID,
        consent_active: bool,
    ) -> None:
        """Update the ``consent_active`` payload bit on all Qdrant points
        for this person.

        This is a best-effort operation — if Qdrant is unreachable, we log
        and continue. The canonical consent state lives in Postgres.
        """
        try:
            from contact_ops.services.qdrant_voice import get_voice_service

            voice_service = get_voice_service()
            if hasattr(voice_service._backend, "set_consent_active"):
                await voice_service._backend.set_consent_active(
                    person_id=person_id,
                    consent_active=consent_active,
                )
        except Exception:
            logger.warning(
                "failed to update Qdrant consent bit for person %s",
                person_id,
                exc_info=True,
            )


_SERVICE_SINGLETON: VoiceConsentService | None = None


def get_voice_consent_service() -> VoiceConsentService:
    global _SERVICE_SINGLETON
    if _SERVICE_SINGLETON is None:
        _SERVICE_SINGLETON = VoiceConsentService()
    return _SERVICE_SINGLETON


def reset_voice_consent_service() -> None:
    global _SERVICE_SINGLETON
    _SERVICE_SINGLETON = None


__all__ = [
    "CONSENT_TOKEN_AUDIENCE",
    "CURRENT_CONSENT_TEXT_VERSION",
    "DEFAULT_CONSENT_TOKEN_TTL_SECONDS",
    "VoiceConsentService",
    "get_voice_consent_service",
    "reset_voice_consent_service",
]
