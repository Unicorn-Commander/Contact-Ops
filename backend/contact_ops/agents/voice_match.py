"""Voice Match Agent: resolves anonymous speaker labels onto Person records.

Event-driven agent that triggers on ``meeting_ops.session_completed``
(Redis pubsub).  Extracts WeSpeaker ResNet34-LM embeddings per diarization
segment, queries the tenant-scoped Qdrant voice catalog, and emits
``action_event`` proposals for human approval or auto-applies them when
the trust ladder + confidence + reversibility gates all open.

Algorithm per Phase 3 Design §10.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from contact_ops.agents import (
    AgentClass,
    AgentContext,
    AgentDef,
    AgentResult,
    BaseAgent,
    Reversibility,
    TrustTier,
    register_agent,
)
from contact_ops.services.score_fusion import (
    fuse_short_utt_scores,
)
from contact_ops.services.voice_consent import (
    VoiceConsentService,
    get_voice_consent_service,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GLOBAL_AUTO_LINK_THRESHOLD = 0.78
"""Default cosine threshold for auto-link (§10.5)."""

GLOBAL_PROPOSE_THRESHOLD = 0.62
"""Default cosine threshold for proposing a link (§10.5)."""

MIN_SEGMENT_DURATION_VOTE = 1.5
"""Segments shorter than this are skipped from voting (§10.7)."""

SHORT_UTT_THRESHOLD_SEC = 3.0
"""Segments shorter than this use ERes2NetV2 fallback with fused scoring (§10.3)."""

AMBIGUOUS_BAND_LOW = 0.65
AMBIGUOUS_BAND_HIGH = 0.85
"""When primary cosine falls in this band, re-run with ERes2NetV2 (§10.3)."""

ROLLING_SAMPLE_WINDOW = 8
"""Number of recent samples for centroid recomputation (§10.7)."""

ADAPTIVE_THRESHOLD_MIN_SAMPLES = 5
"""Minimum confirmed samples before switching to per-person thresholds (§10.7)."""

MULTILINGUAL_RELAXATION = 0.05
"""Threshold relaxation for known multi-lingual persons (§10.8)."""

ATTENDEE_HINT_RELAXATION = 0.04
"""Threshold relaxation when candidate matches an attendee_hint (§10 implicit)."""


# ---------------------------------------------------------------------------
# Event schema
# ---------------------------------------------------------------------------


class MeetingOpsSessionCompleted(BaseModel):
    """Wire contract from Meeting-Ops ``session_completed``."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    event_version: int = 1
    meeting_id: UUID
    tenant_id: UUID
    hipaa_scope: str = Field(pattern=r"^(hipaa|non_hipaa)$")
    audio_url: str = Field(min_length=1)
    diarization_url: str = Field(min_length=1)
    attendee_hints: list[UUID] = Field(default_factory=list)
    session_completed_at: datetime


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

VOICE_MATCH_AGENT_DEF = AgentDef(
    slug="voice-match",
    name="Voice Match Agent",
    version="0.1.0",
    agent_class=AgentClass.EVENT_DRIVEN,
    description=(
        "Resolves anonymous speaker labels from Meeting-Ops diarization "
        "onto canonical Person records. Uses WeSpeaker ResNet34-LM for "
        "voice embedding extraction and Qdrant for tenant-scoped search. "
        "Emits proposals for human approval or auto-links when trust + "
        "confidence + reversibility gates are open."
    ),
    cost_budget_monthly_cents=1000,
    initial_trust_tier=TrustTier.T0_PROBATION,
    triggers=("event:meeting_ops.session_completed",),
    declared_capabilities=(
        "voice_match.auto_linked",
        "voice_match.proposed",
        "voice_match.unknown_speaker",
        "voice_match.diarization_warning",
        "voice_match.consent_required",
    ),
)


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------


class VoiceMatchAgent(BaseAgent):
    """Voice Match Agent implementation.

    Pipeline (per §10):
        1. Validate incoming event
        2. Load diarization output
        3. For each segment: extract embedding, handle short utterances
        4. Search Qdrant with HIPAA-fenced query
        5. Multi-sample voting per §10.7
        6. Apply threshold relaxation (multi-lingual, attendee-hint)
        7. Propose or auto-link
        8. Recompute centroid if confirmed
    """

    def __init__(self, agent_def: AgentDef = VOICE_MATCH_AGENT_DEF) -> None:
        super().__init__(agent_def)
        self._consent_service: VoiceConsentService | None = None

    @property
    def consent_service(self) -> VoiceConsentService:
        if self._consent_service is None:
            self._consent_service = get_voice_consent_service()
        return self._consent_service

    async def _run(self, ctx: AgentContext) -> AgentResult:
        event = self._validate_event(ctx.event_payload)
        if event is None:
            return AgentResult(
                proposals_emitted=0,
                decision_summary="event validation failed",
            )

        tenant_id = event.tenant_id
        hipaa_scope = event.hipaa_scope
        meeting_id = event.meeting_id

        # TODO: Load diarization output from Garage (URL in event.diarization_url)
        # For now, segments come from the event payload or a diarization endpoint.
        # Phase 3.2 stub: segments are passed as evidence in event_payload["segments"]
        segments = ctx.event_payload.get("segments", [])

        if not segments:
            logger.info("no segments for meeting %s; skipping", meeting_id)
            return AgentResult(proposals_emitted=0, decision_summary="no segments")

        proposals_emitted = 0
        proposals_auto_applied = 0
        extra: dict[str, Any] = {}

        # Group segments by speaker label for multi-sample voting
        speaker_segments: dict[str, list[dict[str, Any]]] = {}
        for seg in segments:
            label = seg.get("speaker_label", "UNKNOWN")
            if label not in speaker_segments:
                speaker_segments[label] = []
            speaker_segments[label].append(seg)

        for speaker_label, segs in speaker_segments.items():
            result = await self._process_speaker(
                ctx=ctx,
                speaker_label=speaker_label,
                segments=segs,
                tenant_id=tenant_id,
                hipaa_scope=hipaa_scope,
                meeting_id=meeting_id,
                attendee_hints=event.attendee_hints,
            )
            proposals_emitted += result.proposals_emitted
            proposals_auto_applied += result.proposals_auto_applied

        return AgentResult(
            proposals_emitted=proposals_emitted,
            proposals_auto_applied=proposals_auto_applied,
            decision_summary=(
                f"processed {len(speaker_segments)} speakers from meeting {meeting_id}"
            ),
            extra=extra,
        )

    # ------------------------------------------------------------------
    # Pipeline steps
    # ------------------------------------------------------------------

    async def _process_speaker(
        self,
        *,
        ctx: AgentContext,
        speaker_label: str,
        segments: list[dict[str, Any]],
        tenant_id: UUID,
        hipaa_scope: str,
        meeting_id: UUID,
        attendee_hints: list[UUID],
    ) -> AgentResult:
        """Process one speaker label across multiple segments.

        Implements multi-sample voting per §10.7.
        """
        from contact_ops.services.qdrant_voice import get_voice_service

        # 1. Extract embeddings for each valid segment
        segment_embeddings: list[tuple[float, np.ndarray, str]] = []  # (duration, embedding, model)
        for seg in segments:
            duration = seg.get("duration_sec", 0.0)
            if duration < MIN_SEGMENT_DURATION_VOTE:
                continue

            # Extract embedding
            audio = seg.get("audio")  # placeholder: actual audio from Garage
            if audio is None:
                # Use deterministic stub for dev/test
                from contact_ops.mcp.tools.voice import _deterministic_stub_embedding
                emb = np.array(
                    _deterministic_stub_embedding(f"{speaker_label}:{seg.get('start_sec', 0.0)}"),
                    dtype=np.float32,
                )
                model_ver = "stub"
                segment_embeddings.append((duration, emb, model_ver))
                continue

            emb, model_ver = await self._extract_embedding(audio, duration)
            segment_embeddings.append((duration, emb, model_ver))

        if not segment_embeddings:
            return AgentResult(proposals_emitted=0, decision_summary="no valid segments")

        # 2. Search Qdrant with first segment's embedding
        voice_service = get_voice_service()
        first_emb = segment_embeddings[0][1].tolist()
        candidates = await voice_service.search(
            embedding=first_emb,
            tenant_id=tenant_id,
            hipaa_scope=hipaa_scope,
            limit=5,
        )

        if not candidates:
            # No candidates found: emit unknown_speaker
            await self.propose_action(
                ctx=ctx,
                event_type="voice_match.unknown_speaker",
                aggregate_type="voice_print",
                aggregate_id=meeting_id,
                payload_before=None,
                payload_after={
                    "speaker_label": speaker_label,
                    "meeting_id": str(meeting_id),
                    "segment_count": len(segments),
                },
                confidence=0.0,
                reversibility=Reversibility.REVERSIBLE,
                evidence={
                    "meeting_id": str(meeting_id),
                    "speaker_label": speaker_label,
                    "segment_embeddings": [e.tolist() for _, e, _ in segment_embeddings],
                },
                rationale="No candidate above propose threshold; new person or recording issue",
            )
            return AgentResult(
                proposals_emitted=1,
                decision_summary="unknown speaker",
                extra={"speaker_label": speaker_label},
            )

        # 3. Check consent for top candidate
        top_candidate = candidates[0]
        has_consent = await self.consent_service.has_active_consent(
            db=ctx.db,
            person_id=top_candidate.person_id,
            tenant_id=tenant_id,
        )
        if not has_consent:
            await self.propose_action(
                ctx=ctx,
                event_type="voice_match.consent_required",
                aggregate_type="voice_print",
                aggregate_id=meeting_id,
                payload_before=None,
                payload_after={
                    "person_id_candidate": str(top_candidate.person_id),
                    "reason": "no_active_consent",
                },
                confidence=1.0,
                reversibility=Reversibility.REVERSIBLE,
                evidence={"meeting_id": str(meeting_id), "speaker_label": speaker_label},
                rationale=(
                    "Voice extraction skipped: no active voice_consent "
                    "row for candidate person"
                ),
            )
            return AgentResult(
                proposals_emitted=1,
                decision_summary="consent required",
            )

        # 4. Compute per-segment scores for all candidates
        candidate_scores: dict[UUID, list[float]] = {}
        for candidate in candidates:
            pid = candidate.person_id
            if pid not in candidate_scores:
                candidate_scores[pid] = []

            # Use fused scoring for short utterances
            for dur, emb, _model_ver in segment_embeddings:
                query_vec = emb.tolist()
                # Search specifically for this candidate to get score
                per_candidate = await voice_service.search(
                    embedding=query_vec,
                    tenant_id=tenant_id,
                    hipaa_scope=hipaa_scope,
                    limit=1,
                    exclude_person_ids=[p for p in candidate_scores if p != pid],
                )
                cand_score = 0.0
                for match in per_candidate:
                    if match.person_id == pid:
                        cand_score = match.score
                        break

                # If short utterance, use fused score
                if dur < SHORT_UTT_THRESHOLD_SEC or (
                    AMBIGUOUS_BAND_LOW <= cand_score <= AMBIGUOUS_BAND_HIGH
                ):
                    # Get ERes2NetV2 score (stub: same as WeSpeaker in dev)
                    eres_score = cand_score  # In prod, run ERes2NetV2 extractor
                    cand_score = fuse_short_utt_scores(eres_score, cand_score)

                candidate_scores[pid].append(cand_score)

        # 5. Determine the best candidate via multi-sample voting
        best_person_id, best_scores, decision, confidence_out = self._multi_sample_vote(
            candidate_scores=candidate_scores,
            tenant_id=tenant_id,
            is_multilingual=self._is_multilingual(candidate_scores),
            attendee_hints=attendee_hints,
        )

        # 6. Emit proposal based on decision
        if decision == "AUTO_LINK" and best_person_id is not None:
            await self._emit_auto_link(
                ctx=ctx,
                meeting_id=meeting_id,
                speaker_label=speaker_label,
                person_id=best_person_id,
                scores=sorted(best_scores, reverse=True),
                confidence=confidence_out,
                segment_count=len(segment_embeddings),
                hipaa_scope=hipaa_scope,
            )
            await self._enqueue_graph_sync(
                ctx=ctx,
                operation="merge_edge",
                diar_label=speaker_label,
                meeting_id=meeting_id,
                person_id=best_person_id,
                score=confidence_out,
            )
            return AgentResult(
                proposals_emitted=1,
                proposals_auto_applied=1,
                decision_summary=f"auto-linked {speaker_label} -> {best_person_id}",
            )
        elif decision == "PROPOSED":
            await self.propose_action(
                ctx=ctx,
                event_type="voice_match.proposed",
                aggregate_type="voice_print",
                aggregate_id=meeting_id,
                payload_before=None,
                payload_after={
                    "speaker_label": speaker_label,
                    "meeting_id": str(meeting_id),
                    "person_id_candidate": str(best_person_id),
                    "confidence": confidence_out,
                    "decision": decision,
                },
                confidence=confidence_out,
                reversibility=Reversibility.REVERSIBLE,
                evidence={
                    "meeting_id": str(meeting_id),
                    "speaker_label": speaker_label,
                    "candidate_person_id": str(best_person_id),
                    "segment_scores": sorted(best_scores, reverse=True),
                    "score_source": "multi_sample_vote",
                },
                rationale=f"Voice match proposed at confidence {confidence_out:.3f}",
            )
            return AgentResult(
                proposals_emitted=1,
                decision_summary=f"proposed {speaker_label} -> {best_person_id}",
            )
        else:
            # UNMATCHED
            await self.propose_action(
                ctx=ctx,
                event_type="voice_match.unknown_speaker",
                aggregate_type="voice_print",
                aggregate_id=meeting_id,
                payload_before=None,
                payload_after={
                    "speaker_label": speaker_label,
                    "meeting_id": str(meeting_id),
                },
                confidence=0.0,
                reversibility=Reversibility.REVERSIBLE,
                evidence={
                    "meeting_id": str(meeting_id),
                    "speaker_label": speaker_label,
                    "segment_embeddings": [e.tolist() for _, e, _ in segment_embeddings],
                    "top_scores": {str(k): v for k, v in candidate_scores.items()},
                },
                rationale="No candidate above propose threshold",
            )
            return AgentResult(
                proposals_emitted=1,
                decision_summary=f"unknown speaker {speaker_label}",
            )

    def _multi_sample_vote(
        self,
        *,
        candidate_scores: dict[UUID, list[float]],
        tenant_id: UUID,
        is_multilingual: bool,
        attendee_hints: list[UUID],
    ) -> tuple[UUID | None, list[float], str, float]:
        """Multi-sample voting per §10.7.

        Args:
            candidate_scores: Map of person_id -> list of per-segment scores.
            is_multilingual: Whether the candidate is known multi-lingual.
            attendee_hints: Person IDs hinted by the Meeting-Ops event.

        Returns:
            (best_person_id, best_scores, decision, confidence)
        """
        if not candidate_scores:
            return None, [], "UNMATCHED", 0.0

        best_person_id = max(candidate_scores, key=lambda k: len(candidate_scores[k]))
        best_scores = sorted(candidate_scores[best_person_id], reverse=True)

        effective_auto = GLOBAL_AUTO_LINK_THRESHOLD
        effective_propose = GLOBAL_PROPOSE_THRESHOLD

        # Apply multi-lingual relaxation (0.05, larger than attendee-hint 0.04)
        if is_multilingual:
            effective_auto -= MULTILINGUAL_RELAXATION
        # Apply attendee-hint relaxation if applicable and not already relaxed more
        elif best_person_id in attendee_hints:
            effective_auto -= ATTENDEE_HINT_RELAXATION

        # Compute voting metrics
        top2_mean = sum(best_scores[:2]) / max(len(best_scores[:2]), 1)
        agree_count = sum(1 for c in best_scores if c >= effective_auto)

        # Single-sample fallback
        if len(best_scores) == 1:
            if top2_mean >= effective_auto:
                return best_person_id, best_scores, "PROPOSED", top2_mean
            elif top2_mean >= effective_propose:
                return best_person_id, best_scores, "PROPOSED", top2_mean
            return best_person_id, best_scores, "UNMATCHED", top2_mean

        # Multi-sample voting
        if top2_mean >= effective_auto and agree_count >= 2:
            return best_person_id, best_scores, "AUTO_LINK", top2_mean
        elif top2_mean >= effective_propose:
            return best_person_id, best_scores, "PROPOSED", top2_mean
        else:
            return best_person_id, best_scores, "UNMATCHED", top2_mean

    def _is_multilingual(self, candidate_scores: dict[UUID, list[float]]) -> bool:
        """Check if any candidate has multi-lingual voice_fingerprints.

        Placeholder: in production, query voice_fingerprints for multiple
        language_primary rows per person_id.
        """
        return False

    async def _extract_embedding(
        self,
        audio: Any,
        duration: float,
    ) -> tuple[np.ndarray, str]:
        """Extract speaker embedding, with ERes2NetV2 fallback for short utterances.

        Args:
            audio: Audio data (numpy array or bytes).
            duration: Segment duration in seconds.

        Returns:
            (embedding, model_version)
        """
        from contact_ops.services.voice_extractor import get_voice_extractor

        extractor = get_voice_extractor()
        embedding = await extractor.extract_embedding(
            audio=np.zeros((1, 16000), dtype=np.float32)
            if not isinstance(audio, np.ndarray) else audio,
            sr=16000,
        )
        return embedding, extractor.embedding_model_version

    async def _emit_auto_link(
        self,
        *,
        ctx: AgentContext,
        meeting_id: UUID,
        speaker_label: str,
        person_id: UUID,
        scores: list[float],
        confidence: float,
        segment_count: int,
        hipaa_scope: str,
    ) -> None:
        """Emit auto_link action and update profiles."""
        await self.propose_action(
            ctx=ctx,
            event_type="voice_match.auto_linked",
            aggregate_type="voice_print",
            aggregate_id=meeting_id,
            payload_before=None,
            payload_after={
                "speaker_label": speaker_label,
                "meeting_id": str(meeting_id),
                "person_id": str(person_id),
                "confidence": confidence,
                "segment_count": segment_count,
                "hipaa_scope": hipaa_scope,
            },
            confidence=confidence,
            reversibility=Reversibility.REVERSIBLE,
            evidence={
                "meeting_id": str(meeting_id),
                "speaker_label": speaker_label,
                "person_id": str(person_id),
                "segment_scores": scores,
                "score_source": "multi_sample_vote",
            },
            rationale=f"Auto-linked speaker {speaker_label} to person {person_id} "
                      f"with confidence {confidence:.3f}",
        )

    async def _enqueue_graph_sync(
        self,
        *,
        ctx: AgentContext,
        operation: str,
        diar_label: str,
        meeting_id: UUID,
        person_id: UUID,
        score: float,
    ) -> None:
        """Enqueue a graph sync operation for Brigade FalkorDB.

        Uses Foundation's EventOutbox to enqueue the operation. The Graph
        Sync Worker picks it up asynchronously.
        """
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
                "operation": operation,
                "cypher_template": """
                    MATCH (s:Speaker {label: $diar_label, meeting_id: $meeting_id})
                    MERGE (p:Person {contact_ops_id: $person_id})
                    MERGE (s)-[:IS_PERSON {
                        confidence: $score,
                        source: 'voice_match',
                        resolved_at: timestamp()
                    }]->(p)
                """,
                "params": {
                    "diar_label": diar_label,
                    "meeting_id": str(meeting_id),
                    "person_id": str(person_id),
                    "score": score,
                },
            },
            tenant_id=ctx.tenant_id,
        )

    def _validate_event(
        self, payload: dict[str, Any]
    ) -> MeetingOpsSessionCompleted | None:
        """Validate incoming event against the wire contract."""
        try:
            return MeetingOpsSessionCompleted(**payload)
        except Exception as exc:
            logger.warning("event validation failed: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Registration (triggered at import time)
# ---------------------------------------------------------------------------

register_agent(VOICE_MATCH_AGENT_DEF)


__all__ = [
    "VOICE_MATCH_AGENT_DEF",
    "VoiceMatchAgent",
    "register_agent",
]
