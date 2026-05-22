"""Qdrant-backed speaker-embedding service with HIPAA fence.

Phase 2 shipped the basic Qdrant voice service. Phase 3.2 adds:

* HIPAA fence: every search filters on ``tenant_id + hipaa_scope +
  consent_active``. Callers cannot bypass — the function signature
  requires ``tenant_id`` and ``hipaa_scope``, and the search filter
  is built inside the service.
* Consent-aware: ``consent_active`` is a required payload filter on every
  search. Points with ``consent_active=false`` are never returned.
* HNSW isolation: ``m=0, payload_m=16`` so per-tenant subgraphs are
  physically separate in the HNSW index (Qdrant multi-tenancy best
  practice).
* GDPR erasure: ``delete_by_person_id`` removes all points for a person
  across all languages in one call.
* Language-tagged centroids: optional ``language`` filter on search.
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
    language: str | None = None


class QdrantVoiceBackend(Protocol):
    async def upsert(
        self,
        *,
        sample_id: uuid.UUID,
        person_id: uuid.UUID,
        tenant_id: uuid.UUID,
        hipaa_scope: str,
        embedding: list[float],
        embedding_model: str,
        language: str | None = None,
        consent_active: bool = True,
    ) -> None: ...

    async def search(
        self,
        *,
        embedding: list[float],
        tenant_id: uuid.UUID,
        hipaa_scope: str,
        limit: int,
        language: str | None = None,
        exclude_person_ids: list[uuid.UUID] | None = None,
    ) -> list[VoiceMatch]: ...

    async def delete(self, *, sample_id: uuid.UUID) -> None: ...

    async def delete_by_person_id(self, *, person_id: uuid.UUID) -> int: ...

    async def set_consent_active(
        self, *, person_id: uuid.UUID, consent_active: bool
    ) -> None: ...


class NullVoiceBackend:
    """Fallback backend used when Qdrant is unreachable.

    Logs at debug level and returns empty results; never raises.
    """

    async def upsert(
        self,
        *,
        sample_id: uuid.UUID,
        person_id: uuid.UUID,
        tenant_id: uuid.UUID,
        hipaa_scope: str,
        embedding: list[float],
        embedding_model: str,
        language: str | None = None,
        consent_active: bool = True,
    ) -> None:
        logger.debug(
            "null voice backend: upsert ignored (sample=%s person=%s)",
            sample_id, person_id,
        )

    async def search(
        self,
        *,
        embedding: list[float],
        tenant_id: uuid.UUID,
        hipaa_scope: str,
        limit: int,
        language: str | None = None,
        exclude_person_ids: list[uuid.UUID] | None = None,
    ) -> list[VoiceMatch]:
        logger.debug("null voice backend: search returned empty")
        return []

    async def delete(self, *, sample_id: uuid.UUID) -> None:
        logger.debug("null voice backend: delete ignored (sample=%s)", sample_id)

    async def delete_by_person_id(self, *, person_id: uuid.UUID) -> int:
        logger.debug(
            "null voice backend: delete_by_person_id ignored (person=%s)", person_id
        )
        return 0

    async def set_consent_active(
        self, *, person_id: uuid.UUID, consent_active: bool
    ) -> None:
        logger.debug(
            "null voice backend: set_consent_active ignored (person=%s active=%s)",
            person_id, consent_active,
        )


@dataclass
class QdrantVoiceBackendImpl:
    """Real Qdrant backend with HIPAA fence and HNSW tenant isolation."""

    url: str
    api_key: str | None
    _client: Any = None
    _collection_ready: bool = False

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
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
                hnsw_config=qm.HnswConfigDiff(
                    m=0,
                    payload_m=16,
                ),
                on_disk_payload=True,
            )
            await client.create_payload_index(
                collection_name=CONTACT_OPS_PERSON_VOICE_COLLECTION,
                field_name="tenant_id",
                field_schema=qm.KeywordIndexParams(
                    type="keyword", is_tenant=True,
                ),
            )
            await client.create_payload_index(
                collection_name=CONTACT_OPS_PERSON_VOICE_COLLECTION,
                field_name="hipaa_scope",
                field_schema=qm.PayloadSchemaType.KEYWORD,
            )
            await client.create_payload_index(
                collection_name=CONTACT_OPS_PERSON_VOICE_COLLECTION,
                field_name="consent_active",
                field_schema=qm.PayloadSchemaType.BOOL,
            )
            await client.create_payload_index(
                collection_name=CONTACT_OPS_PERSON_VOICE_COLLECTION,
                field_name="person_id",
                field_schema=qm.PayloadSchemaType.KEYWORD,
            )
            await client.create_payload_index(
                collection_name=CONTACT_OPS_PERSON_VOICE_COLLECTION,
                field_name="language_primary",
                field_schema=qm.PayloadSchemaType.KEYWORD,
            )
        self._collection_ready = True

    async def upsert(
        self,
        *,
        sample_id: uuid.UUID,
        person_id: uuid.UUID,
        tenant_id: uuid.UUID,
        hipaa_scope: str,
        embedding: list[float],
        embedding_model: str,
        language: str | None = None,
        consent_active: bool = True,
    ) -> None:
        from qdrant_client.http import models as qm

        await self._ensure_collection()
        client = await self._ensure_client()
        payload: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "person_id": str(person_id),
            "hipaa_scope": hipaa_scope,
            "consent_active": consent_active,
            "embedding_model": embedding_model,
        }
        if language is not None:
            payload["language_primary"] = language
        await client.upsert(
            collection_name=CONTACT_OPS_PERSON_VOICE_COLLECTION,
            points=[
                qm.PointStruct(
                    id=str(sample_id),
                    vector=list(embedding),
                    payload=payload,
                )
            ],
        )

    async def search(
        self,
        *,
        embedding: list[float],
        tenant_id: uuid.UUID,
        hipaa_scope: str,
        limit: int,
        language: str | None = None,
        exclude_person_ids: list[uuid.UUID] | None = None,
    ) -> list[VoiceMatch]:
        from qdrant_client.http import models as qm

        await self._ensure_collection()
        client = await self._ensure_client()
        must: list[Any] = [
            qm.FieldCondition(
                key="tenant_id", match=qm.MatchValue(value=str(tenant_id))
            ),
            qm.FieldCondition(
                key="hipaa_scope", match=qm.MatchValue(value=hipaa_scope)
            ),
            qm.FieldCondition(
                key="consent_active", match=qm.MatchValue(value=True)
            ),
        ]
        if language is not None:
            must.append(
                qm.FieldCondition(
                    key="language_primary", match=qm.MatchValue(value=language)
                )
            )
        must_not: list[Any] = []
        if exclude_person_ids:
            must_not.append(
                qm.FieldCondition(
                    key="person_id",
                    match=qm.MatchAny(any=[str(pid) for pid in exclude_person_ids]),
                )
            )
        results = await client.search(
            collection_name=CONTACT_OPS_PERSON_VOICE_COLLECTION,
            query_vector=list(embedding),
            limit=limit,
            query_filter=qm.Filter(must=must, must_not=must_not or None),
            search_params=qm.SearchParams(hnsw_ef=128, exact=False),
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
                        language=payload.get("language_primary"),
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

    async def delete_by_person_id(self, *, person_id: uuid.UUID) -> int:
        from qdrant_client.http import models as qm

        client = await self._ensure_client()
        result = await client.delete(
            collection_name=CONTACT_OPS_PERSON_VOICE_COLLECTION,
            points_selector=qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="person_id",
                        match=qm.MatchValue(value=str(person_id)),
                    )
                ]
            ),
        )
        return getattr(result, "deleted_count", 0)

    async def set_consent_active(
        self, *, person_id: uuid.UUID, consent_active: bool
    ) -> None:
        from qdrant_client.http import models as qm

        client = await self._ensure_client()
        await client.set_payload(
            collection_name=CONTACT_OPS_PERSON_VOICE_COLLECTION,
            payload={"consent_active": consent_active},
            points=qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="person_id",
                        match=qm.MatchValue(value=str(person_id)),
                    )
                ]
            ),
        )


class QdrantVoiceService:
    """Public facade over the configured voice-embedding backend.

    Enforces the HIPAA fence at the service boundary: ``search()``
    requires ``tenant_id`` and ``hipaa_scope``, which are forwarded
    to the backend's hard-coded filter. Callers cannot bypass.
    """

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
        except Exception:
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
        hipaa_scope: str = "non_hipaa",
        embedding: list[float],
        embedding_model: str,
        language: str | None = None,
        consent_active: bool = True,
    ) -> None:
        if len(embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"voice embedding must have {EMBEDDING_DIM} dimensions, got {len(embedding)}"
            )
        await self._backend.upsert(
            sample_id=sample_id,
            person_id=person_id,
            tenant_id=tenant_id,
            hipaa_scope=hipaa_scope,
            embedding=embedding,
            embedding_model=embedding_model,
            language=language,
            consent_active=consent_active,
        )

    async def search(
        self,
        *,
        embedding: list[float],
        tenant_id: uuid.UUID,
        hipaa_scope: str,
        limit: int = 10,
        language: str | None = None,
        exclude_person_ids: list[uuid.UUID] | None = None,
    ) -> list[VoiceMatch]:
        if len(embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"voice embedding must have {EMBEDDING_DIM} dimensions, got {len(embedding)}"
            )
        return await self._backend.search(
            embedding=embedding,
            tenant_id=tenant_id,
            hipaa_scope=hipaa_scope,
            limit=limit,
            language=language,
            exclude_person_ids=exclude_person_ids,
        )

    async def delete(self, *, sample_id: uuid.UUID) -> None:
        await self._backend.delete(sample_id=sample_id)

    async def delete_by_person_id(self, *, person_id: uuid.UUID) -> int:
        return await self._backend.delete_by_person_id(person_id=person_id)

    async def set_consent_active(
        self, *, person_id: uuid.UUID, consent_active: bool
    ) -> None:
        await self._backend.set_consent_active(
            person_id=person_id, consent_active=consent_active
        )


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
