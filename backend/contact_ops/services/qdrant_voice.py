"""Qdrant-backed speaker-embedding service.

Owns the ``contact_ops_person_voice`` collection (256-dim cosine vectors).
Each sample stored as a point keyed by the voice_sample UUID with a payload
of ``{tenant_id, person_id, embedding_model}``. The ``tenant_id`` field is
the lateral fence enforced on every search.

In dev / test environments where Qdrant is not running, a no-op backend is
used so tools can be exercised without standing up a Qdrant container. The
caller cannot distinguish backends from the return type; a no-op search
returns an empty list.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from contact_ops.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

CONTACT_OPS_PERSON_VOICE_COLLECTION = "contact_ops_person_voice"
EMBEDDING_DIM = 256


@dataclass(frozen=True)
class VoiceMatch:
    person_id: uuid.UUID
    sample_id: uuid.UUID
    score: float
    embedding_model: str


class QdrantVoiceBackend(Protocol):
    async def upsert(
        self,
        *,
        sample_id: uuid.UUID,
        person_id: uuid.UUID,
        tenant_id: uuid.UUID,
        embedding: list[float],
        embedding_model: str,
    ) -> None: ...

    async def search(
        self,
        *,
        embedding: list[float],
        tenant_id: uuid.UUID,
        limit: int,
        exclude_person_ids: list[uuid.UUID] | None,
    ) -> list[VoiceMatch]: ...

    async def delete(self, *, sample_id: uuid.UUID) -> None: ...


class NullVoiceBackend:
    """Fallback backend used when Qdrant is unreachable.

    Logs at debug level and returns empty results; never raises. Tests rely
    on this so they don't need a running Qdrant container.
    """

    async def upsert(
        self,
        *,
        sample_id: uuid.UUID,
        person_id: uuid.UUID,
        tenant_id: uuid.UUID,
        embedding: list[float],
        embedding_model: str,
    ) -> None:
        logger.debug(
            "null voice backend: upsert ignored (sample=%s person=%s)",
            sample_id,
            person_id,
        )

    async def search(
        self,
        *,
        embedding: list[float],
        tenant_id: uuid.UUID,
        limit: int,
        exclude_person_ids: list[uuid.UUID] | None,
    ) -> list[VoiceMatch]:
        logger.debug("null voice backend: search returned empty")
        return []

    async def delete(self, *, sample_id: uuid.UUID) -> None:
        logger.debug("null voice backend: delete ignored (sample=%s)", sample_id)


@dataclass
class QdrantVoiceBackendImpl:
    """Real Qdrant backend. Lazily constructs the client and ensures the
    collection exists on first call."""

    url: str
    api_key: str | None
    _client: Any = None
    _collection_ready: bool = False

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        # Imported lazily so tests without qdrant-client transitive failures
        # don't blow up at import time.
        from qdrant_client import AsyncQdrantClient

        self._client = AsyncQdrantClient(url=self.url, api_key=self.api_key or None)
        return self._client

    async def _ensure_collection(self) -> None:
        if self._collection_ready:
            return
        from qdrant_client.http import models as qm

        client = await self._ensure_client()
        existing = await client.get_collections()
        names = {c.name for c in existing.collections}
        if CONTACT_OPS_PERSON_VOICE_COLLECTION not in names:
            await client.create_collection(
                collection_name=CONTACT_OPS_PERSON_VOICE_COLLECTION,
                vectors_config=qm.VectorParams(
                    size=EMBEDDING_DIM, distance=qm.Distance.COSINE
                ),
            )
            await client.create_payload_index(
                collection_name=CONTACT_OPS_PERSON_VOICE_COLLECTION,
                field_name="tenant_id",
                field_schema=qm.PayloadSchemaType.KEYWORD,
            )
            await client.create_payload_index(
                collection_name=CONTACT_OPS_PERSON_VOICE_COLLECTION,
                field_name="person_id",
                field_schema=qm.PayloadSchemaType.KEYWORD,
            )
        self._collection_ready = True

    async def upsert(
        self,
        *,
        sample_id: uuid.UUID,
        person_id: uuid.UUID,
        tenant_id: uuid.UUID,
        embedding: list[float],
        embedding_model: str,
    ) -> None:
        from qdrant_client.http import models as qm

        await self._ensure_collection()
        client = await self._ensure_client()
        await client.upsert(
            collection_name=CONTACT_OPS_PERSON_VOICE_COLLECTION,
            points=[
                qm.PointStruct(
                    id=str(sample_id),
                    vector=list(embedding),
                    payload={
                        "tenant_id": str(tenant_id),
                        "person_id": str(person_id),
                        "embedding_model": embedding_model,
                    },
                )
            ],
        )

    async def search(
        self,
        *,
        embedding: list[float],
        tenant_id: uuid.UUID,
        limit: int,
        exclude_person_ids: list[uuid.UUID] | None,
    ) -> list[VoiceMatch]:
        from qdrant_client.http import models as qm

        await self._ensure_collection()
        client = await self._ensure_client()
        must: list[Any] = [
            qm.FieldCondition(
                key="tenant_id", match=qm.MatchValue(value=str(tenant_id))
            )
        ]
        must_not: list[Any] = []
        if exclude_person_ids:
            must_not.append(
                qm.FieldCondition(
                    key="person_id",
                    match=qm.MatchAny(
                        any=[str(pid) for pid in exclude_person_ids]
                    ),
                )
            )
        results = await client.search(
            collection_name=CONTACT_OPS_PERSON_VOICE_COLLECTION,
            query_vector=list(embedding),
            limit=limit,
            query_filter=qm.Filter(must=must, must_not=must_not or None),
            with_payload=True,
        )
        out: list[VoiceMatch] = []
        for point in results:
            payload = point.payload or {}
            try:
                out.append(
                    VoiceMatch(
                        person_id=uuid.UUID(payload["person_id"]),
                        sample_id=uuid.UUID(str(point.id)),
                        score=float(point.score or 0.0),
                        embedding_model=str(payload.get("embedding_model", "")),
                    )
                )
            except (KeyError, ValueError):
                continue
        return out

    async def delete(self, *, sample_id: uuid.UUID) -> None:
        from qdrant_client.http import models as qm

        client = await self._ensure_client()
        await client.delete(
            collection_name=CONTACT_OPS_PERSON_VOICE_COLLECTION,
            points_selector=qm.PointIdsList(points=[str(sample_id)]),
        )


class QdrantVoiceService:
    """Public facade over the configured voice-embedding backend."""

    def __init__(self, backend: QdrantVoiceBackend) -> None:
        self._backend = backend

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> QdrantVoiceService:
        settings = settings or get_settings()
        if not settings.QDRANT_URL or settings.ENV == "test":
            return cls(backend=NullVoiceBackend())
        try:
            backend: QdrantVoiceBackend = QdrantVoiceBackendImpl(
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY or None,
            )
        except Exception:  # pragma: no cover -- defensive
            logger.warning(
                "qdrant client construction failed; falling back to null backend",
                exc_info=True,
            )
            backend = NullVoiceBackend()
        return cls(backend=backend)

    async def upsert(
        self,
        *,
        sample_id: uuid.UUID,
        person_id: uuid.UUID,
        tenant_id: uuid.UUID,
        embedding: list[float],
        embedding_model: str,
    ) -> None:
        if len(embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"voice embedding must have {EMBEDDING_DIM} dimensions, got {len(embedding)}"
            )
        await self._backend.upsert(
            sample_id=sample_id,
            person_id=person_id,
            tenant_id=tenant_id,
            embedding=embedding,
            embedding_model=embedding_model,
        )

    async def search(
        self,
        *,
        embedding: list[float],
        tenant_id: uuid.UUID,
        limit: int = 10,
        exclude_person_ids: list[uuid.UUID] | None = None,
    ) -> list[VoiceMatch]:
        if len(embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"voice embedding must have {EMBEDDING_DIM} dimensions, got {len(embedding)}"
            )
        return await self._backend.search(
            embedding=embedding,
            tenant_id=tenant_id,
            limit=limit,
            exclude_person_ids=exclude_person_ids,
        )

    async def delete(self, *, sample_id: uuid.UUID) -> None:
        await self._backend.delete(sample_id=sample_id)


_SERVICE_SINGLETON: QdrantVoiceService | None = None


def get_voice_service() -> QdrantVoiceService:
    global _SERVICE_SINGLETON
    if _SERVICE_SINGLETON is None:
        _SERVICE_SINGLETON = QdrantVoiceService.from_settings()
    return _SERVICE_SINGLETON


def reset_voice_service() -> None:
    global _SERVICE_SINGLETON
    _SERVICE_SINGLETON = None


__all__ = [
    "CONTACT_OPS_PERSON_VOICE_COLLECTION",
    "EMBEDDING_DIM",
    "NullVoiceBackend",
    "QdrantVoiceBackend",
    "QdrantVoiceBackendImpl",
    "QdrantVoiceService",
    "VoiceMatch",
    "get_voice_service",
    "reset_voice_service",
]
