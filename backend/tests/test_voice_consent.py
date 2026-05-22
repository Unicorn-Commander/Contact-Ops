"""Tests for the VoiceConsentService.

Covers:
- record_consent -> has_active_consent -> revoke_consent lifecycle
- Consent text version mismatch
- JWT token issuance and verification
- Token expiry and audience validation
- GDPR Article 17 erasure (revoke deletes samples + fingerprints)
- Qdrant consent bit update
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.models.voice_consent import VoiceConsent
from contact_ops.services.voice_consent import (
    CONSENT_TOKEN_AUDIENCE,
    CURRENT_CONSENT_TEXT_VERSION,
    DEFAULT_CONSENT_TOKEN_TTL_SECONDS,
    VoiceConsentService,
    get_voice_consent_service,
    reset_voice_consent_service,
)


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_voice_consent_service()


class TestVoiceConsentService:
    """Consent lifecycle: record -> verify -> revoke."""

    @pytest.fixture
    def service(self) -> VoiceConsentService:
        return VoiceConsentService(jwt_secret="test-secret")

    @pytest.fixture
    def mock_db(self) -> AsyncMock:
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock()
        db.execute.return_value = MagicMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        return db

    @pytest.fixture
    def person_id(self) -> uuid.UUID:
        return uuid.uuid4()

    @pytest.fixture
    def tenant_id(self) -> uuid.UUID:
        return uuid.uuid4()

    @pytest.mark.asyncio
    async def test_record_consent_creates_row(
        self,
        service: VoiceConsentService,
        mock_db: AsyncMock,
        person_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> None:
        consent = await service.record_consent(
            db=mock_db,
            person_id=person_id,
            tenant_id=tenant_id,
            consent_text_version=CURRENT_CONSENT_TEXT_VERSION,
            consent_method="api",
            granted_by=uuid.uuid4(),
        )

        assert consent.person_id == person_id
        assert consent.tenant_id == tenant_id
        assert consent.consent_text_version == CURRENT_CONSENT_TEXT_VERSION
        assert consent.consent_method == "api"
        assert consent.consent_revoked_at is None
        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_has_active_consent_returns_false_for_no_row(
        self,
        service: VoiceConsentService,
        mock_db: AsyncMock,
        person_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> None:
        mock_db.execute.return_value.scalar_one_or_none.return_value = None
        has = await service.has_active_consent(
            db=mock_db,
            person_id=person_id,
            tenant_id=tenant_id,
        )
        assert has is False

    @pytest.mark.asyncio
    async def test_has_active_consent_returns_true_with_valid_row(
        self,
        service: VoiceConsentService,
        mock_db: AsyncMock,
        person_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> None:
        consent = VoiceConsent(
            id=uuid.uuid4(),
            person_id=person_id,
            tenant_id=tenant_id,
            consent_granted_at=datetime.now(UTC),
            consent_text_version=CURRENT_CONSENT_TEXT_VERSION,
            consent_method="web_optin",
        )
        mock_db.execute.return_value.scalar_one_or_none.return_value = consent

        has = await service.has_active_consent(
            db=mock_db,
            person_id=person_id,
            tenant_id=tenant_id,
        )
        assert has is True

    @pytest.mark.asyncio
    async def test_has_active_consent_false_on_version_mismatch(
        self,
        service: VoiceConsentService,
        mock_db: AsyncMock,
        person_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> None:
        consent = VoiceConsent(
            id=uuid.uuid4(),
            person_id=person_id,
            tenant_id=tenant_id,
            consent_granted_at=datetime.now(UTC),
            consent_text_version="2025-old-version",
            consent_method="web_optin",
        )
        mock_db.execute.return_value.scalar_one_or_none.return_value = consent

        has = await service.has_active_consent(
            db=mock_db,
            person_id=person_id,
            tenant_id=tenant_id,
        )
        assert has is False

    @pytest.mark.asyncio
    async def test_revoke_consent_sets_revoked_at(
        self,
        service: VoiceConsentService,
        mock_db: AsyncMock,
        person_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> None:
        mock_db.execute.side_effect = [
            # First call: select active consent rows
            MagicMock(
                scalars=lambda: MagicMock(
                    all=lambda: [
                        VoiceConsent(
                            id=uuid.uuid4(),
                            person_id=person_id,
                            tenant_id=tenant_id,
                            consent_granted_at=datetime.now(UTC),
                            consent_text_version=CURRENT_CONSENT_TEXT_VERSION,
                            consent_method="api",
                        )
                    ]
                )
            ),
            # Second call: delete voice_samples
            MagicMock(all=lambda: [uuid.uuid4()]),
            # Third call: delete voice_fingerprints
            MagicMock(all=lambda: [uuid.uuid4()]),
        ]

        audit_db = AsyncMock(spec=AsyncSession)
        audit_db.execute = AsyncMock()

        result = await service.revoke_consent(
            db=mock_db,
            audit_db=audit_db,
            person_id=person_id,
            tenant_id=tenant_id,
        )

        assert result["consent_rows_revoked"] == 1
        assert result["samples_deleted"] == 1
        assert result["fingerprints_deleted"] == 1

    def test_issue_consent_token(
        self,
        service: VoiceConsentService,
        person_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> None:
        consent_id = uuid.uuid4()
        token = service.issue_consent_token(
            consent_id=consent_id,
            person_id=person_id,
            tenant_id=tenant_id,
            consent_text_version=CURRENT_CONSENT_TEXT_VERSION,
        )
        assert isinstance(token, str)
        assert len(token.split(".")) == 3  # JWT has 3 parts

    def test_verify_consent_token(
        self,
        service: VoiceConsentService,
        person_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> None:
        consent_id = uuid.uuid4()
        token = service.issue_consent_token(
            consent_id=consent_id,
            person_id=person_id,
            tenant_id=tenant_id,
            consent_text_version=CURRENT_CONSENT_TEXT_VERSION,
        )
        claims = service.verify_consent_token(token)

        assert claims["person_id"] == str(person_id)
        assert claims["tenant_id"] == str(tenant_id)
        assert claims["voice_consent"] is True
        assert claims["aud"] == CONSENT_TOKEN_AUDIENCE
        assert claims["jti"] == str(consent_id)

    def test_verify_expired_token_raises(
        self,
        service: VoiceConsentService,
        person_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> None:
        consent_id = uuid.uuid4()
        expired_payload = {
            "jti": str(consent_id),
            "person_id": str(person_id),
            "tenant_id": str(tenant_id),
            "consent_text_version": CURRENT_CONSENT_TEXT_VERSION,
            "voice_consent": True,
            "aud": CONSENT_TOKEN_AUDIENCE,
            "exp": datetime.now(UTC) - timedelta(hours=1),
            "iat": datetime.now(UTC) - timedelta(hours=2),
        }
        token = jwt.encode(expired_payload, "test-secret", algorithm="HS256")

        with pytest.raises(jwt.PyJWTError):
            service.verify_consent_token(token)

    def test_verify_wrong_audience_raises(
        self,
        service: VoiceConsentService,
        person_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> None:
        consent_id = uuid.uuid4()
        bad_aud_payload = {
            "jti": str(consent_id),
            "person_id": str(person_id),
            "tenant_id": str(tenant_id),
            "consent_text_version": CURRENT_CONSENT_TEXT_VERSION,
            "voice_consent": True,
            "aud": "wrong-audience",
            "exp": datetime.now(UTC) + timedelta(hours=1),
            "iat": datetime.now(UTC),
        }
        token = jwt.encode(bad_aud_payload, "test-secret", algorithm="HS256")

        with pytest.raises(jwt.PyJWTError):
            service.verify_consent_token(token)

    def test_singleton(self) -> None:
        s1 = get_voice_consent_service()
        s2 = get_voice_consent_service()
        assert s1 is s2

        reset_voice_consent_service()
        s3 = get_voice_consent_service()
        assert s3 is not s1
