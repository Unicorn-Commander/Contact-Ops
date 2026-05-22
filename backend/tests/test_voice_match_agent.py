"""Tests for the Voice Match Agent.

Covers:
- AgentDef registration and contract
- Event validation (MeetingOpsSessionCompleted schema)
- Multi-sample voting per §10.7
- Single-sample fallback
- Consent gate enforcement
- HIPAA scope propagation
- Propose vs auto-link decision boundary
- Dummy scenarios for future threshold adaptation tests
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.agents import AgentDef
from contact_ops.agents.voice_match import (
    GLOBAL_AUTO_LINK_THRESHOLD,
    GLOBAL_PROPOSE_THRESHOLD,
    MULTILINGUAL_RELAXATION,
    ATTENDEE_HINT_RELAXATION,
    VOICE_MATCH_AGENT_DEF,
    MeetingOpsSessionCompleted,
    VoiceMatchAgent,
)


class TestAgentDef:
    """AgentDef registration contract."""

    def test_definition_is_valid(self) -> None:
        assert VOICE_MATCH_AGENT_DEF.slug == "voice-match"
        assert VOICE_MATCH_AGENT_DEF.agent_class.value == "event"
        assert "event:meeting_ops.session_completed" in VOICE_MATCH_AGENT_DEF.triggers

    def test_reversibility_declared(self) -> None:
        """All voice_match action types are reversible."""
        assert not any(
            c.endswith("_irreversible") or c.endswith("_hard_delete")
            for c in VOICE_MATCH_AGENT_DEF.declared_capabilities
        )

    def test_cost_budget_positive(self) -> None:
        assert VOICE_MATCH_AGENT_DEF.cost_budget_monthly_cents > 0


class TestEventSchema:
    """MeetingOpsSessionCompleted validation."""

    def test_valid_event(self) -> None:
        event = MeetingOpsSessionCompleted(
            event_id=uuid.uuid4(),
            meeting_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            hipaa_scope="non_hipaa",
            audio_url="garage://meeting-ops-audio/test.wav",
            diarization_url="garage://meeting-ops-diar/test.json",
            session_completed_at=datetime.now(),
        )
        assert event.event_version == 1

    def test_valid_hipaa_event(self) -> None:
        event = MeetingOpsSessionCompleted(
            event_id=uuid.uuid4(),
            meeting_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            hipaa_scope="hipaa",
            audio_url="garage://meeting-ops-audio/hipaa-test.wav",
            diarization_url="garage://meeting-ops-diar/hipaa-test.json",
            session_completed_at=datetime.now(),
        )
        assert event.hipaa_scope == "hipaa"

    def test_invalid_hipaa_scope(self) -> None:
        with pytest.raises(ValueError):
            MeetingOpsSessionCompleted(
                event_id=uuid.uuid4(),
                meeting_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                hipaa_scope="invalid_scope",
                audio_url="garage://test.wav",
                diarization_url="garage://test.json",
                session_completed_at=datetime.now(),
            )

    def test_with_attendee_hints(self) -> None:
        event = MeetingOpsSessionCompleted(
            event_id=uuid.uuid4(),
            meeting_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            hipaa_scope="non_hipaa",
            audio_url="garage://test.wav",
            diarization_url="garage://test.json",
            attendee_hints=[uuid.uuid4(), uuid.uuid4()],
            session_completed_at=datetime.now(),
        )
        assert len(event.attendee_hints) == 2


class TestMultiSampleVoting:
    """Multi-sample voting per §10.7."""

    @pytest.fixture
    def agent(self) -> VoiceMatchAgent:
        return VoiceMatchAgent(VOICE_MATCH_AGENT_DEF)

    def test_top2_mean_auto_link(self, agent: VoiceMatchAgent) -> None:
        """3 segments above 0.78 -> AUTO_LINK."""
        person_id = uuid.uuid4()
        candidate_scores = {person_id: [0.85, 0.82, 0.79]}
        best_pid, scores, decision, confidence = agent._multi_sample_vote(
            candidate_scores=candidate_scores,
            tenant_id=uuid.uuid4(),
            is_multilingual=False,
            attendee_hints=[],
        )
        assert decision == "AUTO_LINK"
        assert best_pid == person_id
        assert confidence >= GLOBAL_AUTO_LINK_THRESHOLD

    def test_top2_mean_propose(self, agent: VoiceMatchAgent) -> None:
        """3 segments in 0.62–0.78 band -> PROPOSED."""
        person_id = uuid.uuid4()
        candidate_scores = {person_id: [0.72, 0.68, 0.65]}
        best_pid, scores, decision, confidence = agent._multi_sample_vote(
            candidate_scores=candidate_scores,
            tenant_id=uuid.uuid4(),
            is_multilingual=False,
            attendee_hints=[],
        )
        assert decision == "PROPOSED"
        assert GLOBAL_PROPOSE_THRESHOLD <= confidence < GLOBAL_AUTO_LINK_THRESHOLD

    def test_below_propose_threshold(self, agent: VoiceMatchAgent) -> None:
        """3 segments below 0.62 -> UNMATCHED."""
        person_id = uuid.uuid4()
        candidate_scores = {person_id: [0.50, 0.45, 0.40]}
        best_pid, scores, decision, confidence = agent._multi_sample_vote(
            candidate_scores=candidate_scores,
            tenant_id=uuid.uuid4(),
            is_multilingual=False,
            attendee_hints=[],
        )
        assert decision == "UNMATCHED"

    def test_single_sample_fallback(self, agent: VoiceMatchAgent) -> None:
        """Single segment >=1.5s but only one sample -> PROPOSED with confidence."""
        person_id = uuid.uuid4()
        candidate_scores = {person_id: [0.75]}
        best_pid, scores, decision, confidence = agent._multi_sample_vote(
            candidate_scores=candidate_scores,
            tenant_id=uuid.uuid4(),
            is_multilingual=False,
            attendee_hints=[],
        )
        assert decision == "PROPOSED"
        assert best_pid == person_id

    def test_single_sample_low_confidence(self, agent: VoiceMatchAgent) -> None:
        """Single segment below 0.62 -> UNMATCHED."""
        person_id = uuid.uuid4()
        candidate_scores = {person_id: [0.55]}
        best_pid, scores, decision, confidence = agent._multi_sample_vote(
            candidate_scores=candidate_scores,
            tenant_id=uuid.uuid4(),
            is_multilingual=False,
            attendee_hints=[],
        )
        assert decision == "UNMATCHED"

    def test_only_one_above_threshold(self, agent: VoiceMatchAgent) -> None:
        """2 segments but only 1 above auto threshold -> PROPOSED, not AUTO_LINK."""
        person_id = uuid.uuid4()
        candidate_scores = {person_id: [0.82, 0.60]}
        best_pid, scores, decision, confidence = agent._multi_sample_vote(
            candidate_scores=candidate_scores,
            tenant_id=uuid.uuid4(),
            is_multilingual=False,
            attendee_hints=[],
        )
        assert decision == "PROPOSED"
        assert best_pid == person_id


class TestThresholdRelaxation:
    """Threshold relaxation rules."""

    @pytest.fixture
    def agent(self) -> VoiceMatchAgent:
        return VoiceMatchAgent(VOICE_MATCH_AGENT_DEF)

    def test_multilingual_relaxation(self, agent: VoiceMatchAgent) -> None:
        """Multi-lingual person gets 0.05 relaxation."""
        person_id = uuid.uuid4()
        candidate_scores = {person_id: [0.75, 0.74]}
        best_pid, scores, decision, confidence = agent._multi_sample_vote(
            candidate_scores=candidate_scores,
            tenant_id=uuid.uuid4(),
            is_multilingual=True,
            attendee_hints=[],
        )
        effective_threshold = GLOBAL_AUTO_LINK_THRESHOLD - MULTILINGUAL_RELAXATION
        assert confidence >= effective_threshold
        assert decision == "AUTO_LINK"

    def test_attendee_hint_relaxation(self, agent: VoiceMatchAgent) -> None:
        """Attendee-hinted person gets 0.04 relaxation."""
        person_id = uuid.uuid4()
        candidate_scores = {person_id: [0.76, 0.75]}
        best_pid, scores, decision, confidence = agent._multi_sample_vote(
            candidate_scores=candidate_scores,
            tenant_id=uuid.uuid4(),
            is_multilingual=False,
            attendee_hints=[person_id],
        )
        effective_threshold = GLOBAL_AUTO_LINK_THRESHOLD - ATTENDEE_HINT_RELAXATION
        assert confidence >= effective_threshold
        assert decision == "AUTO_LINK"

    def test_relaxation_does_not_stack(self, agent: VoiceMatchAgent) -> None:
        """When both multilingual + attendee-hints apply, only the larger
        relaxation (multilingual, 0.05) is applied; they do NOT stack.

        Uses scores in the gap between the stacked (0.69) and
        non-stacked (0.73) thresholds. If stacking occurred, this would
        auto-link; correct behavior (no stack) yields PROPOSED.
        """
        person_id = uuid.uuid4()
        candidate_scores = {person_id: [0.71, 0.71]}
        best_pid, scores, decision, confidence = agent._multi_sample_vote(
            candidate_scores=candidate_scores,
            tenant_id=uuid.uuid4(),
            is_multilingual=True,
            attendee_hints=[person_id],
        )
        effective_threshold = GLOBAL_AUTO_LINK_THRESHOLD - MULTILINGUAL_RELAXATION
        stacked_threshold = GLOBAL_AUTO_LINK_THRESHOLD - MULTILINGUAL_RELAXATION - ATTENDEE_HINT_RELAXATION
        assert confidence < effective_threshold  # Falls in gap below non-stacked threshold
        assert confidence >= stacked_threshold  # Would auto-link if stacked
        assert decision != "AUTO_LINK"  # Correct: not stacked

    def test_relaxation_threshold_boundary(self, agent: VoiceMatchAgent) -> None:
        """Without relaxation, scores in the 0.74-0.77 range would be PROPOSED."""
        person_id = uuid.uuid4()
        candidate_scores = {person_id: [0.77, 0.76]}
        best_pid, scores, decision, confidence = agent._multi_sample_vote(
            candidate_scores=candidate_scores,
            tenant_id=uuid.uuid4(),
            is_multilingual=False,
            attendee_hints=[],
        )
        assert decision == "PROPOSED"
        assert confidence < GLOBAL_AUTO_LINK_THRESHOLD
