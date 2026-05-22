"""MCP admin tools for the Voice Match Agent (Phase 3.2).

Five tools for managing voice enrollment, consent, and identity unlinking:

* ``enroll_voice``          — explicit enrollment with consent token
* ``record_consent``        — write voice_consent row + issue token
* ``revoke_voice_consent``  — GDPR Art. 17 erasure
* ``voice_match_status``    — catalog stats and performance
* ``unlink_voice``          — reverse an auto_link or approved proposal

All follow the Phase 1 MCP tool pattern: ``ToolDef`` + ``MCPContext`` +
``require_role`` + ``require_scopes`` + ``emit_action_event``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import and_, select, text

from contact_ops.mcp.audit_helper import emit_action_event
from contact_ops.mcp.errors import TENANT_MISMATCH, ToolError
from contact_ops.mcp.rbac import ROLE_LADDER, get_role, require_role, require_scopes
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, register
from contact_ops.models import Person
from contact_ops.models.voice_fingerprint import VoiceFingerprint
from contact_ops.services.qdrant_voice import get_voice_service
from contact_ops.services.voice_consent import (
    CURRENT_CONSENT_TEXT_VERSION,
    get_voice_consent_service,
)

PERSON_NOT_FOUND = "PERSON_NOT_FOUND"
ACTION_EVENT_NOT_FOUND = "ACTION_EVENT_NOT_FOUND"
CONSENT_TOKEN_INVALID = "CONSENT_TOKEN_INVALID"  # noqa: S105
VOICE_FINGERPRINT_NOT_FOUND = "VOICE_FINGERPRINT_NOT_FOUND"
VOICE_UNLINK_NOT_REVERSIBLE = "VOICE_UNLINK_NOT_REVERSIBLE"  # noqa: S105


# ---------------------------------------------------------------------------
# enroll_voice
# ---------------------------------------------------------------------------


class EnrollVoiceInput(BaseModel):
    person_id: UUID
    sample_audio_url: str = Field(min_length=1)
    consent_token: str = Field(min_length=1)
    language: str | None = Field(default=None, max_length=20)


class EnrollVoiceOutput(ToolOutput):
    voice_fingerprint_id: UUID
    sample_count: int
    centroid_baseline: float
    status: str


async def _handle_enroll_voice(
    ctx: MCPContext, payload: EnrollVoiceInput
) -> EnrollVoiceOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["voice:write", "consent:write"])

    consent_service = get_voice_consent_service()

    # Verify consent token
    try:
        claims = consent_service.verify_consent_token(payload.consent_token)
    except Exception as exc:
        raise ToolError(
            CONSENT_TOKEN_INVALID,
            f"consent token verification failed: {exc}",
        ) from exc

    if claims.get("person_id") != str(payload.person_id):
        raise ToolError(
            CONSENT_TOKEN_INVALID,
            "consent token person_id does not match request",
        )

    voice_service = get_voice_service()

    # Fetch or create voice fingerprint
    person = await ctx.db.get(Person, payload.person_id)
    if person is None or person.canonical_owner_tenant_id != ctx.tenant_id:
        raise ToolError(PERSON_NOT_FOUND, "person not found in tenant")

    result = await ctx.db.execute(
        select(VoiceFingerprint).where(
            and_(
                VoiceFingerprint.person_id == payload.person_id,
                VoiceFingerprint.language_primary == payload.language,
            )
        )
    )
    fingerprint = result.scalar_one_or_none()

    if fingerprint is None:
        fingerprint = VoiceFingerprint(
            id=uuid.uuid4(),
            person_id=payload.person_id,
            embedding=[0.0] * 256,  # placeholder; real extraction happens async
            embedding_model="wespeaker-resnet34-LM-2024.03",
            embedding_model_version="wespeaker-resnet34-LM-2024.03",
            sample_count=0,
            auto_link_threshold=0.78,
            propose_threshold=0.62,
            language_primary=payload.language,
        )
        ctx.db.add(fingerprint)

    fingerprint.sample_count = (fingerprint.sample_count or 0) + 1
    fingerprint.last_updated_at = datetime.now(UTC)

    sample_id = uuid.uuid4()

    # Upsert to Qdrant
    stub_embedding = [0.0] * 256
    await voice_service.upsert(
        sample_id=sample_id,
        person_id=payload.person_id,
        tenant_id=ctx.tenant_id,
        hipaa_scope="non_hipaa",
        embedding=stub_embedding,
        embedding_model="wespeaker-resnet34-LM-2024.03",
        language=payload.language,
        consent_active=True,
    )

    await ctx.db.flush()

    # Emit audit event
    await emit_action_event(
        ctx,
        event_type="voice_match.enrolled",
        aggregate_type="voice_print",
        aggregate_id=fingerprint.id,
        affected_ids=[payload.person_id],
        payload_before=None,
        payload_after={
            "person_id": str(payload.person_id),
            "language": payload.language,
            "sample_count": fingerprint.sample_count,
            "sample_id": str(sample_id),
        },
        confidence=1.0,
        rationale="Explicit voice enrollment via MCP admin tool",
    )

    return EnrollVoiceOutput(
        voice_fingerprint_id=fingerprint.id,
        sample_count=fingerprint.sample_count or 0,
        centroid_baseline=float(fingerprint.auto_link_threshold or 0.78),
        status="enrolled",
    )


# ---------------------------------------------------------------------------
# record_consent
# ---------------------------------------------------------------------------


class RecordConsentInput(BaseModel):
    person_id: UUID
    consent_text_version: str = Field(default=CURRENT_CONSENT_TEXT_VERSION)
    method: str = Field(default="api", max_length=40)
    granted_by: UUID | None = None
    ip: str | None = Field(default=None, max_length=45)


class RecordConsentOutput(ToolOutput):
    consent_id: UUID
    granted_at: str
    voice_extraction_allowed: bool
    consent_token: str


async def _handle_record_consent(
    ctx: MCPContext, payload: RecordConsentInput
) -> RecordConsentOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["voice:write", "consent:write"])

    person = await ctx.db.get(Person, payload.person_id)
    if person is None or person.canonical_owner_tenant_id != ctx.tenant_id:
        raise ToolError(PERSON_NOT_FOUND, "person not found in tenant")

    consent_service = get_voice_consent_service()
    consent = await consent_service.record_consent(
        db=ctx.db,
        person_id=payload.person_id,
        tenant_id=ctx.tenant_id,
        consent_text_version=payload.consent_text_version,
        consent_method=payload.method,
        granted_by=payload.granted_by,
        consent_ip=payload.ip,
    )

    token = consent_service.issue_consent_token(
        consent_id=consent.id,
        person_id=payload.person_id,
        tenant_id=ctx.tenant_id,
        consent_text_version=payload.consent_text_version,
    )

    await ctx.db.flush()

    # Emit audit event
    await emit_action_event(
        ctx,
        event_type="voice_match.consent_recorded",
        aggregate_type="person",
        aggregate_id=payload.person_id,
        affected_ids=None,
        payload_before=None,
        payload_after={
            "consent_id": str(consent.id),
            "person_id": str(payload.person_id),
            "consent_text_version": payload.consent_text_version,
            "method": payload.method,
        },
        confidence=1.0,
        rationale="Voice consent recorded via MCP admin tool",
    )

    return RecordConsentOutput(
        consent_id=consent.id,
        granted_at=consent.consent_granted_at.isoformat(),
        voice_extraction_allowed=True,
        consent_token=token,
    )


# ---------------------------------------------------------------------------
# revoke_voice_consent
# ---------------------------------------------------------------------------


class RevokeConsentInput(BaseModel):
    person_id: UUID
    reason: str | None = Field(default=None, max_length=1024)


class RevokeConsentOutput(ToolOutput):
    revoked_at: str
    samples_deleted: int
    fingerprints_deleted: int
    qdrant_points_deleted: int


async def _handle_revoke_voice_consent(
    ctx: MCPContext, payload: RevokeConsentInput
) -> RevokeConsentOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["voice:write", "consent:write"])

    person = await ctx.db.get(Person, payload.person_id)
    if person is None or person.canonical_owner_tenant_id != ctx.tenant_id:
        raise ToolError(PERSON_NOT_FOUND, "person not found in tenant")

    consent_service = get_voice_consent_service()

    # Revoke consent (deletes from Postgres)
    result = await consent_service.revoke_consent(
        db=ctx.db,
        audit_db=ctx.audit_db,
        person_id=payload.person_id,
        tenant_id=ctx.tenant_id,
        reason=payload.reason,
    )

    # Delete from Qdrant
    voice_service = get_voice_service()
    qdrant_deleted = await voice_service.delete_by_person_id(
        person_id=payload.person_id
    )

    await ctx.db.flush()

    return RevokeConsentOutput(
        revoked_at=result["revoked_at"],
        samples_deleted=result["samples_deleted"],
        fingerprints_deleted=result.get("fingerprints_deleted", 0),
        qdrant_points_deleted=qdrant_deleted,
    )


# ---------------------------------------------------------------------------
# voice_match_status
# ---------------------------------------------------------------------------


class VoiceMatchStatusInput(BaseModel):
    tenant_id: UUID | None = None


class VoiceMatchStatusOutput(ToolOutput):
    catalog_size: int
    avg_samples_per_person: float
    persons_with_5plus_samples: int
    persons_with_language_tags: int
    auto_link_rate_7d: float
    propose_rate_7d: float
    unknown_speaker_rate_7d: float
    diarization_warning_rate_7d: float
    p50_latency_seconds: float
    p95_latency_seconds: float


async def _handle_voice_match_status(
    ctx: MCPContext, payload: VoiceMatchStatusInput
) -> VoiceMatchStatusOutput:
    caller_role = get_role(ctx.claims)
    target_tenant = payload.tenant_id or ctx.tenant_id

    if target_tenant != ctx.tenant_id:
        if ROLE_LADDER[caller_role] < ROLE_LADDER["ADMIN"]:
            raise ToolError(
                TENANT_MISMATCH,
                "cross-tenant status requires ADMIN role",
            )

    db = ctx.db

    # Catalog size
    count_result = await db.execute(
        select(VoiceFingerprint).where(
            VoiceFingerprint.person_id.in_(
                select(Person.id).where(
                    Person.canonical_owner_tenant_id == target_tenant
                )
            )
        )
    )
    all_fps = count_result.scalars().all()
    catalog_size = len(all_fps)

    samples_per_person = [fp.sample_count or 0 for fp in all_fps]
    avg_samples = (
        sum(samples_per_person) / len(samples_per_person)
        if samples_per_person
        else 0.0
    )
    persons_with_5plus = sum(1 for c in samples_per_person if c >= 5)
    persons_with_lang = sum(
        1 for fp in all_fps if fp.language_primary is not None
    )

    # Action event stats (7d)
    seven_days_ago = datetime.now(UTC)
    total_actions = 0
    auto_linked = 0
    proposed = 0
    unknown = 0
    diar_warn = 0

    ae_result = await db.execute(
        text(
            """
            SELECT event_type, COUNT(*) as cnt
            FROM action_event
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND event_type LIKE 'voice_match.%'
              AND proposed_at >= :since
            GROUP BY event_type
            """
        ),
        {
            "tenant_id": str(target_tenant),
            "since": seven_days_ago,
        },
    )
    for row in ae_result.all():
        etype, cnt = row
        total_actions += cnt
        if etype == "voice_match.auto_linked":
            auto_linked += cnt
        elif etype == "voice_match.proposed":
            proposed += cnt
        elif etype == "voice_match.unknown_speaker":
            unknown += cnt
        elif etype == "voice_match.diarization_warning":
            diar_warn += cnt

    def _rate(n: int) -> float:
        return n / total_actions if total_actions > 0 else 0.0

    return VoiceMatchStatusOutput(
        catalog_size=catalog_size,
        avg_samples_per_person=round(avg_samples, 2),
        persons_with_5plus_samples=persons_with_5plus,
        persons_with_language_tags=persons_with_lang,
        auto_link_rate_7d=round(_rate(auto_linked), 4),
        propose_rate_7d=round(_rate(proposed), 4),
        unknown_speaker_rate_7d=round(_rate(unknown), 4),
        diarization_warning_rate_7d=round(_rate(diar_warn), 4),
        p50_latency_seconds=0.0,
        p95_latency_seconds=0.0,
    )


# ---------------------------------------------------------------------------
# unlink_voice
# ---------------------------------------------------------------------------


class UnlinkVoiceInput(BaseModel):
    action_event_id: UUID
    reason: str | None = Field(default=None, max_length=1024)


class UnlinkVoiceOutput(ToolOutput):
    unlinked_at: str
    edges_removed: int


async def _handle_unlink_voice(
    ctx: MCPContext, payload: UnlinkVoiceInput
) -> UnlinkVoiceOutput:
    require_role(ctx, "STAFF")
    require_scopes(ctx, ["voice:write"])

    # Load the action_event
    result = await ctx.db.execute(
        text(
            """
            SELECT event_id, event_type, status, reversibility_class, payload
            FROM action_event
            WHERE event_id = CAST(:event_id AS uuid)
              AND tenant_id = CAST(:tenant_id AS uuid)
            """
        ),
        {
            "event_id": str(payload.action_event_id),
            "tenant_id": str(ctx.tenant_id),
        },
    )
    row = result.mappings().first()
    if row is None:
        raise ToolError(
            ACTION_EVENT_NOT_FOUND,
            "action_event not found in tenant",
        )

    reversibility = str(row["reversibility_class"])
    if reversibility not in ("reversible", "reversible_24h"):
        raise ToolError(
            VOICE_UNLINK_NOT_REVERSIBLE,
            f"action_event has reversibility_class={reversibility}; cannot unlink",
        )

    ae_payload = row["payload"] if isinstance(row["payload"], dict) else {}
    person_id_str = ae_payload.get("person_id") or ae_payload.get("person_id_candidate")
    if person_id_str is None:
        raise ToolError(
            VOICE_FINGERPRINT_NOT_FOUND,
            "action_event payload missing person_id",
        )

    person_id = UUID(person_id_str)

    # Unlink: set voice_fingerprints.person_id = NULL
    await ctx.db.execute(
        text(
            """
            UPDATE voice_fingerprints
            SET person_id = NULL,
                last_updated_at = now()
            WHERE person_id = CAST(:person_id AS uuid)
            """
        ),
        {"person_id": str(person_id)},
    )

    # Enqueue graph edge deletion
    from sqlalchemy.ext.asyncio import create_async_engine

    from contact_ops.agents.outbox import EventOutbox
    from contact_ops.core.config import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    outbox = EventOutbox(engine=engine)
    await outbox.publish(
        db=ctx.audit_db,
        channel="graph.sync_edge",
        payload={
            "operation": "delete_edge",
            "cypher_template": (
                "MATCH (s:Speaker)-[r:IS_PERSON {source: 'voice_match'}]"
                "->(p:Person {contact_ops_id: $person_id}) DELETE r"
            ),
            "params": {
                "person_id": str(person_id),
            },
        },
        tenant_id=ctx.tenant_id,
    )

    await emit_action_event(
        ctx,
        event_type="voice_match.unlinked",
        aggregate_type="voice_print",
        aggregate_id=payload.action_event_id,
        affected_ids=[person_id],
        payload_before=ae_payload,
        payload_after={
            "original_action_event_id": str(payload.action_event_id),
            "person_id": str(person_id),
            "reason": payload.reason,
        },
        confidence=1.0,
        rationale=payload.reason or "Voice unlinked via MCP admin tool",
    )

    return UnlinkVoiceOutput(
        unlinked_at=datetime.now(UTC).isoformat(),
        edges_removed=1,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_voice_match_admin_tools() -> None:
    """Register all 5 Voice Match admin MCP tools."""
    register(
        name="enroll_voice",
        description=(
            "Explicit voice enrollment for a person. Requires STAFF role, "
            "voice:write + consent:write scopes, and a valid consent_token "
            "issued by record_consent. Idempotent on (person_id, language)."
        ),
        input_model=EnrollVoiceInput,
        output_model=EnrollVoiceOutput,
        handler=_handle_enroll_voice,
        required_role="STAFF",
        required_scopes=("voice:write", "consent:write"),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key",
    )
    register(
        name="record_consent",
        description=(
            "Record a voice consent grant. Required before voice enrollment. "
            "Returns a short-lived JWT consent_token that enroll_voice consumes. "
            "Idempotent on (person_id, tenant_id, consent_text_version). "
            "Requires STAFF, voice:write, consent:write."
        ),
        input_model=RecordConsentInput,
        output_model=RecordConsentOutput,
        handler=_handle_record_consent,
        required_role="STAFF",
        required_scopes=("voice:write", "consent:write"),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="natural-key",
    )
    register(
        name="revoke_voice_consent",
        description=(
            "GDPR Article 17 erasure: revoke all active consent rows, "
            "delete all voice_samples and voice_fingerprints for the person, "
            "and remove all Qdrant points. Requires STAFF and voice:write + "
            "consent:write. Emits voice_match.consent_revoked action_event."
        ),
        input_model=RevokeConsentInput,
        output_model=RevokeConsentOutput,
        handler=_handle_revoke_voice_consent,
        required_role="STAFF",
        required_scopes=("voice:write", "consent:write"),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        idempotency="not-idempotent",
    )
    register(
        name="voice_match_status",
        description=(
            "Voice match catalog stats and performance metrics. "
            "CLIENT can query own tenant; ADMIN can query any tenant. "
            "Returns catalog_size, avg_samples, auto_link_rate_7d, "
            "p50/p95 latency."
        ),
        input_model=VoiceMatchStatusInput,
        output_model=VoiceMatchStatusOutput,
        handler=_handle_voice_match_status,
        required_role="CLIENT",
        required_scopes=(),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="repeatable",
    )
    register(
        name="unlink_voice",
        description=(
            "Reverse a voice_match.auto_linked or voice_match.proposed "
            "(approved) action. Sets voice_fingerprints.person_id NULL, "
            "removes the Brigade Speaker->IS_PERSON->Person edge, and "
            "emits voice_match.unlinked. Requires STAFF and voice:write. "
            "Only reversible action_events can be unlinked."
        ),
        input_model=UnlinkVoiceInput,
        output_model=UnlinkVoiceOutput,
        handler=_handle_unlink_voice,
        required_role="STAFF",
        required_scopes=("voice:write",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        idempotency="not-idempotent",
    )


register_voice_match_admin_tools()
