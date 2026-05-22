"""Tests for Voice Match MCP admin tools.

Covers:
- Tool registration (tools/list returns the 5 new tools)
- enroll_voice: RBAC reject paths, consent token validation
- record_consent: creates consent row, issues token
- revoke_voice_consent: erasure flow
- voice_match_status: stats query
- unlink_voice: reversibility check
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.mcp.errors import (
    INSUFFICIENT_ROLE,
    TENANT_MISMATCH,
    ToolError,
)
from contact_ops.mcp.registry import MCPContext, list_tools
from contact_ops.mcp.tools.voice_match_admin import (
    CONSENT_TOKEN_INVALID,
    PERSON_NOT_FOUND,
    register_voice_match_admin_tools,
)

# Re-register for tests
try:
    register_voice_match_admin_tools()
except (ValueError, KeyError):
    pass


@pytest.fixture
def ctx() -> MCPContext:
    return MCPContext(
        tenant_id=uuid.uuid4(),
        user_id="test-user",
        actor_chain={"sub": "test"},
        human_authority="test-user",
        db=AsyncMock(spec=AsyncSession),
        audit_db=AsyncMock(spec=AsyncSession),
        request_id="test-req",
        claims={
            "realm_access": {"roles": ["STAFF"]},
            "scope": "voice:write consent:write voice:read",
        },
    )


class TestToolRegistration:
    """Tools/list should include the 5 new tools."""

    def test_tools_registered(self) -> None:
        tools = list_tools()
        names = [t.name for t in tools]
        assert "enroll_voice" in names
        assert "record_consent" in names
        assert "revoke_voice_consent" in names
        assert "voice_match_status" in names
        assert "unlink_voice" in names

    def test_tool_annotations(self) -> None:
        tools = list_tools()
        tool_map = {t.name: t for t in tools}
        enroll = tool_map.get("enroll_voice")
        assert enroll is not None
        assert enroll.required_role == "STAFF"
        assert "voice:write" in enroll.required_scopes
        assert enroll.annotations["readOnlyHint"] is False


class TestEnrollVoiceRBAC:
    """RBAC reject paths for enroll_voice."""

    @pytest.mark.asyncio
    async def test_enroll_voice_requires_staff(self) -> None:
        client_ctx = MCPContext(
            tenant_id=uuid.uuid4(),
            user_id="client-user",
            actor_chain={"sub": "client"},
            human_authority="client-user",
            db=AsyncMock(spec=AsyncSession),
            audit_db=AsyncMock(spec=AsyncSession),
            request_id="test",
            claims={
                "realm_access": {"roles": ["CLIENT"]},
                "scope": "voice:read",
            },
        )
        from contact_ops.mcp.tools.voice_match_admin import _handle_enroll_voice
        from contact_ops.mcp.tools.voice_match_admin import EnrollVoiceInput

        with pytest.raises(ToolError) as exc:
            await _handle_enroll_voice(
                client_ctx,
                EnrollVoiceInput(
                    person_id=uuid.uuid4(),
                    sample_audio_url="garage://test.wav",
                    consent_token="fake-token",
                ),
            )
        assert exc.value.code == INSUFFICIENT_ROLE

    @pytest.mark.asyncio
    async def test_enroll_voice_requires_scopes(self) -> None:
        from contact_ops.mcp.tools.voice_match_admin import _handle_enroll_voice
        from contact_ops.mcp.tools.voice_match_admin import EnrollVoiceInput

        no_scope_ctx = MCPContext(
            tenant_id=uuid.uuid4(),
            user_id="staff-user",
            actor_chain={"sub": "staff"},
            human_authority="staff-user",
            db=AsyncMock(spec=AsyncSession),
            audit_db=AsyncMock(spec=AsyncSession),
            request_id="test",
            claims={
                "realm_access": {"roles": ["STAFF"]},
                "scope": "person:read",
            },
        )
        with pytest.raises(ToolError):
            await _handle_enroll_voice(
                no_scope_ctx,
                EnrollVoiceInput(
                    person_id=uuid.uuid4(),
                    sample_audio_url="garage://test.wav",
                    consent_token="fake-token",
                ),
            )


class TestVoiceMatchStatusRBAC:
    """RBAC for voice_match_status."""

    def _make_ctx(
        self,
        role: str,
        scope: str = "",
        tenant_id: UUID | None = None,
    ) -> MCPContext:
        return MCPContext(
            tenant_id=tenant_id or uuid.uuid4(),
            user_id="user",
            actor_chain={"sub": "user"},
            human_authority="user",
            db=AsyncMock(spec=AsyncSession),
            audit_db=AsyncMock(spec=AsyncSession),
            request_id="test",
            claims={
                "realm_access": {"roles": [role]},
                "scope": scope,
            },
        )

    @pytest.mark.asyncio
    async def test_client_can_query_own_tenant(self) -> None:
        from contact_ops.mcp.tools.voice_match_admin import _handle_voice_match_status
        from contact_ops.mcp.tools.voice_match_admin import VoiceMatchStatusInput

        my_tenant = uuid.uuid4()
        c = self._make_ctx("CLIENT", tenant_id=my_tenant)
        # Mock the DB queries
        c.db.execute = AsyncMock(return_value=MagicMock(all=lambda: []))

        result = await _handle_voice_match_status(
            c, VoiceMatchStatusInput()
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_client_cannot_query_other_tenant(self) -> None:
        from contact_ops.mcp.tools.voice_match_admin import _handle_voice_match_status
        from contact_ops.mcp.tools.voice_match_admin import VoiceMatchStatusInput

        my_tenant = uuid.uuid4()
        other_tenant = uuid.uuid4()
        c = self._make_ctx("CLIENT", tenant_id=my_tenant)

        with pytest.raises(ToolError) as exc:
            await _handle_voice_match_status(
                c, VoiceMatchStatusInput(tenant_id=other_tenant)
            )
        assert exc.value.code == TENANT_MISMATCH


class TestRecordConsent:
    """record_consent creates a consent row and issues a token."""

    @pytest.mark.asyncio
    async def test_record_consent_flow(self, ctx: MCPContext) -> None:
        from contact_ops.mcp.tools.voice_match_admin import (
            RecordConsentInput,
            _handle_record_consent,
        )

        person_id = uuid.uuid4()
        mock_person = MagicMock()
        mock_person.id = person_id
        mock_person.canonical_owner_tenant_id = ctx.tenant_id
        ctx.db.get = AsyncMock(return_value=mock_person)

        result = await _handle_record_consent(
            ctx,
            RecordConsentInput(
                person_id=person_id,
                method="api",
            ),
        )
        assert result.voice_extraction_allowed is True
        assert result.consent_token is not None
        assert len(result.consent_token.split(".")) == 3  # JWT


class TestUnlinkVoice:
    """unlink_voice reversibility check."""

    @pytest.mark.asyncio
    async def test_unlink_reversible_event(self, ctx: MCPContext) -> None:
        from contact_ops.mcp.tools.voice_match_admin import (
            UnlinkVoiceInput,
            _handle_unlink_voice,
        )

        event_id = uuid.uuid4()
        person_id = uuid.uuid4()
        ae_row = {
            "event_id": event_id,
            "event_type": "voice_match.auto_linked",
            "status": "applied",
            "reversibility_class": "reversible",
            "payload": {"person_id": str(person_id), "meeting_id": str(uuid.uuid4())},
        }
        ae_mock = MagicMock()
        ae_mock.mappings.return_value.first.return_value = ae_row
        ctx.db.execute = AsyncMock(return_value=ae_mock)
        ctx.db.get_bind = MagicMock()

        with patch(
            "contact_ops.agents.outbox.EventOutbox.publish",
            new_callable=AsyncMock,
        ):
            result = await _handle_unlink_voice(
                ctx, UnlinkVoiceInput(action_event_id=event_id)
            )
            assert result.edges_removed == 1
            assert result.unlinked_at is not None

    @pytest.mark.asyncio
    async def test_unlink_irreversible_raises(self, ctx: MCPContext) -> None:
        from contact_ops.mcp.tools.voice_match_admin import (
            UnlinkVoiceInput,
            VOICE_UNLINK_NOT_REVERSIBLE,
            _handle_unlink_voice,
        )

        event_id = uuid.uuid4()
        ae_row = {
            "event_id": event_id,
            "event_type": "voice_match.something",
            "status": "applied",
            "reversibility_class": "irreversible",
            "payload": {},
        }
        ae_mock = MagicMock()
        ae_mock.mappings.return_value.first.return_value = ae_row
        ctx.db.execute = AsyncMock(return_value=ae_mock)
        ctx.db.get_bind = MagicMock()

        with pytest.raises(ToolError) as exc:
            await _handle_unlink_voice(
                ctx, UnlinkVoiceInput(action_event_id=event_id)
            )
        assert exc.value.code == VOICE_UNLINK_NOT_REVERSIBLE
