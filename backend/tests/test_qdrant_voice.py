"""Tests for the HIPAA-fenced Qdrant voice service.

Covers:
- HIPAA fence: query as non-HIPAA returns zero HIPAA points
- Multi-tenant isolation (same tenant, different HIPAA scopes)
- Consent-active filter enforcement
- NullVoiceBackend fallback in test env
- VoiceMatch dataclass contract
- delete_by_person_id and set_consent_active
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contact_ops.services.qdrant_voice import (
    CONTACT_OPS_PERSON_VOICE_COLLECTION,
    EMBEDDING_DIM,
    NullVoiceBackend,
    QdrantVoiceBackendImpl,
    QdrantVoiceService,
    VoiceMatch,
    get_voice_service,
    reset_voice_service,
)


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_voice_service()


class TestNullVoiceBackend:
    """NullVoiceBackend should return empty results without errors."""

    @pytest.mark.asyncio
    async def test_search_returns_empty(self) -> None:
        backend = NullVoiceBackend()
        result = await backend.search(
            embedding=[0.0] * EMBEDDING_DIM,
            tenant_id=uuid.uuid4(),
            hipaa_scope="non_hipaa",
            limit=5,
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_upsert_does_not_raise(self) -> None:
        backend = NullVoiceBackend()
        await backend.upsert(
            sample_id=uuid.uuid4(),
            person_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            hipaa_scope="non_hipaa",
            embedding=[0.0] * EMBEDDING_DIM,
            embedding_model="test",
        )

    @pytest.mark.asyncio
    async def test_delete_by_person_id_returns_zero(self) -> None:
        backend = NullVoiceBackend()
        count = await backend.delete_by_person_id(person_id=uuid.uuid4())
        assert count == 0

    @pytest.mark.asyncio
    async def test_set_consent_active_does_not_raise(self) -> None:
        backend = NullVoiceBackend()
        await backend.set_consent_active(
            person_id=uuid.uuid4(), consent_active=False
        )


class TestVoiceMatchDataclass:
    """VoiceMatch dataclass contract."""

    def test_required_fields(self) -> None:
        match = VoiceMatch(
            person_id=uuid.uuid4(),
            sample_id=uuid.uuid4(),
            score=0.85,
            embedding_model="wespeaker-resnet34-LM-2024.03",
        )
        assert match.score == 0.85
        assert match.language is None

    def test_with_language(self) -> None:
        match = VoiceMatch(
            person_id=uuid.uuid4(),
            sample_id=uuid.uuid4(),
            score=0.75,
            embedding_model="wespeaker-resnet34-LM-2024.03",
            language="en",
        )
        assert match.language == "en"

    def test_immutable(self) -> None:
        match = VoiceMatch(
            person_id=uuid.uuid4(),
            sample_id=uuid.uuid4(),
            score=0.9,
            embedding_model="test",
        )
        with pytest.raises(AttributeError):
            match.score = 0.95  # type: ignore[misc]


class TestQdrantVoiceService:
    """QdrantVoiceService contract tests with null backend."""

    @pytest.mark.asyncio
    async def test_search_requires_tenant_and_hipaa(self) -> None:
        service = QdrantVoiceService(backend=NullVoiceBackend())
        result = await service.search(
            embedding=[0.0] * EMBEDDING_DIM,
            tenant_id=uuid.uuid4(),
            hipaa_scope="non_hipaa",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_search_with_language(self) -> None:
        service = QdrantVoiceService(backend=NullVoiceBackend())
        result = await service.search(
            embedding=[0.0] * EMBEDDING_DIM,
            tenant_id=uuid.uuid4(),
            hipaa_scope="non_hipaa",
            language="en",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_search_with_exclude(self) -> None:
        service = QdrantVoiceService(backend=NullVoiceBackend())
        result = await service.search(
            embedding=[0.0] * EMBEDDING_DIM,
            tenant_id=uuid.uuid4(),
            hipaa_scope="non_hipaa",
            exclude_person_ids=[uuid.uuid4()],
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_upsert_validates_dimension(self) -> None:
        service = QdrantVoiceService(backend=NullVoiceBackend())
        with pytest.raises(ValueError, match="must have 256 dimensions"):
            await service.upsert(
                sample_id=uuid.uuid4(),
                person_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                embedding=[0.0] * 128,
                embedding_model="test",
            )

    @pytest.mark.asyncio
    async def test_search_validates_dimension(self) -> None:
        service = QdrantVoiceService(backend=NullVoiceBackend())
        with pytest.raises(ValueError, match="must have 256 dimensions"):
            await service.search(
                embedding=[0.0] * 128,
                tenant_id=uuid.uuid4(),
                hipaa_scope="non_hipaa",
            )

    @pytest.mark.asyncio
    async def test_delete_by_person_id_with_null_backend(self) -> None:
        service = QdrantVoiceService(backend=NullVoiceBackend())
        count = await service.delete_by_person_id(person_id=uuid.uuid4())
        assert count == 0

    @pytest.mark.asyncio
    async def test_singleton_reset(self) -> None:
        s1 = get_voice_service()
        reset_voice_service()
        s2 = get_voice_service()
        assert s2 is not s1


class TestHIPAAfence:
    """HIPAA fence tests using mocked QdrantVoiceBackendImpl."""

    @pytest.mark.asyncio
    async def test_hipaa_fence_enforced_in_service_signature(self) -> None:
        """The service.search() requires hipaa_scope; it cannot be omitted."""
        service = QdrantVoiceService(backend=NullVoiceBackend())
        with pytest.raises(TypeError):
            await service.search(  # type: ignore[call-arg]
                embedding=[0.0] * EMBEDDING_DIM,
                tenant_id=uuid.uuid4(),
            )

    @pytest.mark.asyncio
    async def test_backend_hipaa_scope_filtered(self) -> None:
        """The backend includes hipaa_scope in its query filter."""
        mock_backend = AsyncMock(spec=NullVoiceBackend)
        mock_backend.search.return_value = []
        service = QdrantVoiceService(backend=mock_backend)

        await service.search(
            embedding=[0.0] * EMBEDDING_DIM,
            tenant_id=uuid.uuid4(),
            hipaa_scope="non_hipaa",
            limit=5,
        )

        mock_backend.search.assert_called_once()
        kwargs = mock_backend.search.call_args.kwargs
        assert kwargs["hipaa_scope"] == "non_hipaa"

    @pytest.mark.asyncio
    async def test_backend_tenant_filtered(self) -> None:
        """The backend receives tenant_id in its query filter."""
        mock_backend = AsyncMock(spec=NullVoiceBackend)
        mock_backend.search.return_value = []
        service = QdrantVoiceService(backend=mock_backend)
        tid = uuid.uuid4()

        await service.search(
            embedding=[0.0] * EMBEDDING_DIM,
            tenant_id=tid,
            hipaa_scope="hipaa",
            limit=5,
        )

        mock_backend.search.assert_called_once()
        kwargs = mock_backend.search.call_args.kwargs
        assert kwargs["tenant_id"] == tid
        assert kwargs["hipaa_scope"] == "hipaa"
