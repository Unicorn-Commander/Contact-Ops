"""Voice-sample MCP tools (Phase 2 Codex 1).

Same two-step upload flow as photos, with one key difference: the speaker
embedding is the load-bearing artefact, not the audio bytes. Tools accept a
pre-computed 256-dim embedding (from Meeting-Ops' Parakeet pipeline) inline,
so audio upload is optional. The Qdrant index is updated on confirm.

``match_speaker`` is read-only and does not write any link; callers (Meeting-
Ops, the future Voice Match Agent) decide what to do with the candidates.
"""
# ruff: noqa: I001

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import (
    PERSON_NOT_FOUND,
    VALIDATION_FAILED,
    ToolError,
)
from contact_ops.mcp.rbac import require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, load_person, register
from contact_ops.models import MediaAsset, Person, Tenant, VoiceSample
from contact_ops.models.enums import RetentionClass
from contact_ops.services.qdrant_voice import (
    EMBEDDING_DIM,
    QdrantVoiceService,
    get_voice_service,
)
from contact_ops.services.storage import get_storage_service

VOICE_SAMPLE_NOT_FOUND = "VOICE_SAMPLE_NOT_FOUND"
UPLOAD_NOT_PRESENT = "UPLOAD_NOT_PRESENT"
EMBEDDING_DIMENSION_MISMATCH = "EMBEDDING_DIMENSION_MISMATCH"
EMBEDDING_REQUIRED = "EMBEDDING_REQUIRED"

ALLOWED_AUDIO_TYPES = (
    "audio/wav",
    "audio/mpeg",
    "audio/mp4",
    "audio/m4a",
    "audio/ogg",
    "audio/flac",
    "audio/opus",
)
VOICE_BUCKET_KIND = "voice"
DEFAULT_EMBEDDING_MODEL = "parakeet-tdt-1.1b-speaker-256d"

MATCH_SCOPES = ("tenant", "my_tenants", "all_visible")


def _ext_for_content_type(content_type: str) -> str:
    mapping = {
        "audio/wav": "wav",
        "audio/mpeg": "mp3",
        "audio/mp4": "m4a",
        "audio/m4a": "m4a",
        "audio/ogg": "ogg",
        "audio/flac": "flac",
        "audio/opus": "opus",
    }
    return mapping.get(content_type, "bin")


def _deterministic_stub_embedding(seed: str) -> list[float]:
    """Repeatable 256-dim L2-normalised embedding from a seed string.

    Used when callers don't supply one and Meeting-Ops Parakeet isn't wired in
    yet (Phase 4 work). Not pretending to be a real speaker embedding — it's a
    deterministic placeholder so the rest of the pipeline can be exercised.
    """
    rng_state = hashlib.sha512(seed.encode("utf-8")).digest()
    out: list[float] = []
    while len(out) < EMBEDDING_DIM:
        rng_state = hashlib.sha512(rng_state).digest()
        for i in range(0, len(rng_state), 2):
            if len(out) >= EMBEDDING_DIM:
                break
            sample = int.from_bytes(rng_state[i : i + 2], "big")
            out.append((sample / 65535.0) * 2.0 - 1.0)
    norm = sum(x * x for x in out) ** 0.5 or 1.0
    return [x / norm for x in out]


async def _tenant_slug(ctx: MCPContext) -> str:
    tenant = await ctx.db.get(Tenant, ctx.tenant_id)
    if tenant is None:
        raise ToolError(VALIDATION_FAILED, "caller tenant not found")
    return tenant.slug


def _voice_sample_payload(sample: VoiceSample) -> dict[str, Any]:
    return {
        "voice_sample_id": str(sample.id),
        "asset_id": str(sample.asset_id),
        "person_id": str(sample.person_id) if sample.person_id else None,
        "embedding_model": sample.embedding_model,
        "duration_seconds": (
            float(sample.duration_seconds) if sample.duration_seconds else None
        ),
        "captured_at": sample.captured_at,
        "meeting_id": str(sample.meeting_id) if sample.meeting_id else None,
        "speaker_label": sample.speaker_label,
    }


# ---------------------------------------------------------------------------
# upload_voice_sample (prepare)
# ---------------------------------------------------------------------------


class UploadVoiceSampleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: uuid.UUID
    content_type: Literal[ALLOWED_AUDIO_TYPES] | None = None  # type: ignore[valid-type]
    byte_size: int = Field(ge=1, le=200 * 1024 * 1024)
    sha256_hex: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    duration_seconds: float = Field(gt=0, le=3600)
    meeting_id: uuid.UUID | None = None
    speaker_label: str | None = Field(default=None, max_length=60)
    captured_at: datetime | None = None
    embedding: list[float] | None = Field(
        default=None, min_length=EMBEDDING_DIM, max_length=EMBEDDING_DIM
    )
    embedding_model: str | None = Field(default=None, max_length=80)


class UploadVoiceSampleOutput(ToolOutput):
    voice_sample_id: uuid.UUID
    asset_id: uuid.UUID
    bucket: str
    object_key: str
    upload_url: str
    upload_url_expires_seconds: int
    embedding_provided: bool
    status: str


async def upload_voice_sample(
    ctx: MCPContext, req: UploadVoiceSampleInput
) -> UploadVoiceSampleOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["media:write", "voice:write"])
    person = await load_person(ctx, req.person_id)
    if person.canonical_owner_tenant_id != ctx.tenant_id:
        raise ToolError(PERSON_NOT_FOUND, "person not in caller's tenant")

    slug = await _tenant_slug(ctx)
    storage = get_storage_service()
    asset_id = uuid.uuid4()
    sample_id = uuid.uuid4()
    content_type = req.content_type or "audio/wav"
    bucket = storage.voice_bucket(slug)
    object_key = (
        f"{req.person_id}/{asset_id}.{_ext_for_content_type(content_type)}"
    )

    sha_bytes = bytes.fromhex(req.sha256_hex) if req.sha256_hex else bytes(32)
    asset = MediaAsset(
        id=asset_id,
        tenant_id=ctx.tenant_id,
        bucket=bucket,
        object_key=object_key,
        content_type=content_type,
        byte_size=req.byte_size,
        sha256=sha_bytes,
        kind=VOICE_BUCKET_KIND,
        duration_ms=int(req.duration_seconds * 1000),
        captured_at=req.captured_at,
        retention_class=RetentionClass.operational_2y,
    )
    embedding_to_persist = req.embedding or _deterministic_stub_embedding(
        f"{req.person_id}:{asset_id}"
    )
    sample = VoiceSample(
        id=sample_id,
        asset_id=asset_id,
        person_id=req.person_id,
        embedding=embedding_to_persist,
        embedding_model=req.embedding_model or DEFAULT_EMBEDDING_MODEL,
        duration_seconds=Decimal(str(round(req.duration_seconds, 2))),
        captured_at=req.captured_at or datetime.now().astimezone(),
        meeting_id=req.meeting_id,
        speaker_label=req.speaker_label,
    )
    ctx.db.add(asset)
    ctx.db.add(sample)
    await ctx.db.flush()

    upload_url = await storage.presigned_put_url(
        bucket=bucket, key=object_key, content_type=content_type
    )
    return UploadVoiceSampleOutput(
        voice_sample_id=sample_id,
        asset_id=asset_id,
        bucket=bucket,
        object_key=object_key,
        upload_url=upload_url,
        upload_url_expires_seconds=storage.DEFAULT_PUT_TTL_SECONDS,
        embedding_provided=req.embedding is not None,
        status="pending_upload",
    )


# ---------------------------------------------------------------------------
# confirm_voice_upload
# ---------------------------------------------------------------------------


class ConfirmVoiceUploadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_sample_id: uuid.UUID
    sha256_hex: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    skip_storage_check: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)


class ConfirmVoiceUploadOutput(ToolOutput):
    voice_sample_id: uuid.UUID
    asset_id: uuid.UUID
    embedding_model: str
    embedding_dim: int
    qdrant_indexed: bool
    status: str
    event_id: uuid.UUID | None = None


async def confirm_voice_upload(
    ctx: MCPContext, req: ConfirmVoiceUploadInput
) -> ConfirmVoiceUploadOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["media:write", "voice:write"])

    sample = await ctx.db.get(VoiceSample, req.voice_sample_id)
    if sample is None:
        raise ToolError(VOICE_SAMPLE_NOT_FOUND, "voice sample not found")
    asset = await ctx.db.get(MediaAsset, sample.asset_id)
    if asset is None or asset.tenant_id != ctx.tenant_id:
        raise ToolError(
            VOICE_SAMPLE_NOT_FOUND, "voice sample asset not found in tenant"
        )

    if not req.skip_storage_check:
        storage = get_storage_service()
        head = await storage.head(bucket=asset.bucket, key=asset.object_key)
        if head is None:
            raise ToolError(
                UPLOAD_NOT_PRESENT,
                "no audio object found at the presigned-upload location",
                hint="re-PUT the bytes before calling confirm_voice_upload",
            )
        actual_size = int(head.get("size") or 0)
        if actual_size <= 0:
            raise ToolError(
                VALIDATION_FAILED,
                f"uploaded audio has invalid size: {actual_size}",
            )
        asset.byte_size = actual_size
    asset.sha256 = bytes.fromhex(req.sha256_hex)

    qdrant_indexed = False
    if sample.person_id is not None:
        embedding = (
            list(sample.embedding) if sample.embedding is not None else []
        )
        try:
            voice_service = get_voice_service()
            await voice_service.upsert(
                sample_id=sample.id,
                person_id=sample.person_id,
                tenant_id=ctx.tenant_id,
                embedding=embedding,
                embedding_model=sample.embedding_model,
            )
            qdrant_indexed = True
        except ValueError as exc:
            raise ToolError(
                EMBEDDING_DIMENSION_MISMATCH,
                str(exc),
            ) from exc
        except Exception:  # pragma: no cover -- backend best-effort
            qdrant_indexed = False
    await ctx.db.flush()
    event_id = await emit_action_event(
        ctx,
        event_type="voice_sample.confirm",
        aggregate_type="person",
        aggregate_id=sample.person_id or uuid.uuid4(),
        affected_ids=[sample.person_id] if sample.person_id else [],
        payload_before=None,
        payload_after={
            **_voice_sample_payload(sample),
            "qdrant_indexed": qdrant_indexed,
        },
        confidence=req.confidence,
    )
    return ConfirmVoiceUploadOutput(
        voice_sample_id=sample.id,
        asset_id=asset.id,
        embedding_model=sample.embedding_model,
        embedding_dim=EMBEDDING_DIM,
        qdrant_indexed=qdrant_indexed,
        status="applied",
        event_id=event_id,
    )


# ---------------------------------------------------------------------------
# list_voice_samples
# ---------------------------------------------------------------------------


class ListVoiceSamplesInput(BaseModel):
    person_id: uuid.UUID
    limit: int = Field(default=10, ge=1, le=50)


class ListVoiceSamplesOutput(ToolOutput):
    items: list[dict[str, Any]]
    count: int


async def list_voice_samples(
    ctx: MCPContext, req: ListVoiceSamplesInput
) -> ListVoiceSamplesOutput:
    require_role(ctx, "CLIENT")
    require_scopes(ctx, ["voice:read"])
    person = await ctx.db.get(Person, req.person_id)
    if person is None or person.canonical_owner_tenant_id != ctx.tenant_id:
        raise ToolError(PERSON_NOT_FOUND, "person not in caller's tenant")

    rows = (
        (
            await ctx.db.execute(
                select(VoiceSample, MediaAsset)
                .join(MediaAsset, MediaAsset.id == VoiceSample.asset_id)
                .where(VoiceSample.person_id == req.person_id)
                .order_by(VoiceSample.captured_at.desc())
                .limit(req.limit)
            )
        )
        .all()
    )
    storage = get_storage_service()
    items: list[dict[str, Any]] = []
    for sample, asset in rows:
        download_url = await storage.presigned_get_url(
            bucket=asset.bucket, key=asset.object_key
        )
        items.append(
            {
                **_voice_sample_payload(sample),
                "content_type": asset.content_type,
                "byte_size": asset.byte_size,
                "download_url": download_url,
                "download_url_expires_seconds": storage.DEFAULT_GET_TTL_SECONDS,
            }
        )
    return ListVoiceSamplesOutput(items=items, count=len(items))


# ---------------------------------------------------------------------------
# match_speaker
# ---------------------------------------------------------------------------


class MatchSpeakerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    embedding: list[float] = Field(min_length=EMBEDDING_DIM, max_length=EMBEDDING_DIM)
    embedding_model: str = Field(min_length=1, max_length=80)
    scope: Literal[MATCH_SCOPES] = "tenant"  # type: ignore[valid-type]
    min_score: float = Field(default=0.65, ge=0, le=1)
    limit: int = Field(default=5, ge=1, le=20)
    exclude_person_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)


class MatchSpeakerOutput(ToolOutput):
    matches: list[dict[str, Any]]
    count: int
    strong_threshold: float
    candidate_threshold: float


async def match_speaker(
    ctx: MCPContext, req: MatchSpeakerInput
) -> MatchSpeakerOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["voice:match"])

    voice_service = get_voice_service()
    raw_matches = await voice_service.search(
        embedding=list(req.embedding),
        tenant_id=ctx.tenant_id,
        limit=req.limit * 4,  # over-fetch for threshold filtering
        exclude_person_ids=list(req.exclude_person_ids or []),
    )
    above_min = [m for m in raw_matches if m.score >= req.min_score]
    matches: list[dict[str, Any]] = []
    for m in above_min[: req.limit]:
        person = await ctx.db.get(Person, m.person_id)
        person_summary: dict[str, Any] | None = None
        if person is not None and person.canonical_owner_tenant_id == ctx.tenant_id:
            person_summary = {
                "person_id": str(person.id),
                "display_name": person.display_name,
                "etag": person.etag,
            }
        if person_summary is None:
            continue
        tier = (
            "strong"
            if m.score >= 0.85
            else ("candidate" if m.score >= 0.65 else "suggest_new")
        )
        matches.append(
            {
                "person_id": str(m.person_id),
                "sample_id": str(m.sample_id),
                "score": m.score,
                "tier": tier,
                "person_summary": person_summary,
                "embedding_model": m.embedding_model,
            }
        )

    return MatchSpeakerOutput(
        matches=matches,
        count=len(matches),
        strong_threshold=0.85,
        candidate_threshold=0.65,
    )


# ---------------------------------------------------------------------------
# Registrations
# ---------------------------------------------------------------------------


def register_voice_tools() -> None:
    register(
        name="upload_voice_sample",
        description=(
            "Reserve a voice_sample + media_asset row and return a presigned "
            "PUT URL. Accepts an optional pre-computed 256-dim embedding from "
            "Meeting-Ops; falls back to a deterministic placeholder. Requires "
            "STAFF and media:write + voice:write."
        ),
        input_model=UploadVoiceSampleInput,
        output_model=UploadVoiceSampleOutput,
        handler=upload_voice_sample,
        required_role="STAFF",
        required_scopes=("media:write", "voice:write"),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        idempotency="none",
    )
    register(
        name="confirm_voice_upload",
        description=(
            "Verify the uploaded audio exists, record sha256/size, upsert "
            "the speaker embedding into Qdrant, and emit the "
            "voice_sample.confirm action_event. Requires STAFF and "
            "media:write + voice:write."
        ),
        input_model=ConfirmVoiceUploadInput,
        output_model=ConfirmVoiceUploadOutput,
        handler=confirm_voice_upload,
        required_role="STAFF",
        required_scopes=("media:write", "voice:write"),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        idempotency="natural-key",
    )
    register(
        name="list_voice_samples",
        description=(
            "List voice samples for a person with presigned GET URLs. "
            "Requires CLIENT and voice:read."
        ),
        input_model=ListVoiceSamplesInput,
        output_model=ListVoiceSamplesOutput,
        handler=list_voice_samples,
        required_role="CLIENT",
        required_scopes=("voice:read",),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        idempotency="none",
    )
    register(
        name="match_speaker",
        description=(
            "Resolve a 256-dim speaker embedding against the tenant's "
            "voice index. Returns ranked candidates with cosine scores and "
            "tier (strong / candidate / suggest_new). Requires STAFF and "
            "voice:match."
        ),
        input_model=MatchSpeakerInput,
        output_model=MatchSpeakerOutput,
        handler=match_speaker,
        required_role="STAFF",
        required_scopes=("voice:match",),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        idempotency="none",
    )


register_voice_tools()


_ = (QdrantVoiceService, EMBEDDING_REQUIRED)
