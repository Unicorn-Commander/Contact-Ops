from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from contact_ops.mcp.errors import INSUFFICIENT_ROLE, ToolError
from contact_ops.mcp.registry import MCPContext, get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.mcp.tools.voice import (
    ConfirmVoiceUploadInput,
    ListVoiceSamplesInput,
    MatchSpeakerInput,
    UploadVoiceSampleInput,
    _deterministic_stub_embedding,
    confirm_voice_upload,
    list_voice_samples,
    match_speaker,
    upload_voice_sample,
)
from contact_ops.services.qdrant_voice import EMBEDDING_DIM


def _ctx(role: str = "CLIENT", scopes: str = "") -> MCPContext:
    tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    return MCPContext(
        tenant_id=tenant_id,
        user_id="test-user",
        actor_chain={"sub": "test-user"},
        human_authority=str(tenant_id),
        db=cast(Any, None),
        audit_db=cast(Any, None),
        request_id="test-request",
        claims={"realm_access": {"roles": [role]}, "scope": scopes},
    )


def test_voice_tools_registered() -> None:
    register_all_tools()
    for name in {
        "upload_voice_sample",
        "confirm_voice_upload",
        "list_voice_samples",
        "match_speaker",
    }:
        assert get_tool(name) is not None, f"{name} should be registered"


def test_deterministic_stub_embedding_is_256d_and_unit_norm() -> None:
    e1 = _deterministic_stub_embedding("seed-1")
    e2 = _deterministic_stub_embedding("seed-1")
    e3 = _deterministic_stub_embedding("seed-2")
    assert len(e1) == EMBEDDING_DIM
    assert e1 == e2  # repeatable
    assert e1 != e3
    norm = sum(x * x for x in e1) ** 0.5
    assert 0.99 < norm < 1.01


def test_upload_voice_sample_input_validation() -> None:
    # embedding wrong length is rejected before tool is called
    with pytest.raises(ValueError):
        UploadVoiceSampleInput(
            person_id=uuid.uuid4(),
            byte_size=1000,
            duration_seconds=5.0,
            embedding=[0.0] * 100,
        )


def test_match_speaker_input_locked_to_256_dims() -> None:
    with pytest.raises(ValueError):
        MatchSpeakerInput(
            embedding=[0.0] * 192,
            embedding_model="x",
        )
    ok = MatchSpeakerInput(
        embedding=[0.0] * EMBEDDING_DIM,
        embedding_model="parakeet-tdt-1.1b-speaker-256d",
    )
    assert ok.embedding_model.startswith("parakeet")


def test_match_speaker_score_threshold_bounds() -> None:
    with pytest.raises(ValueError):
        MatchSpeakerInput(
            embedding=[0.0] * EMBEDDING_DIM,
            embedding_model="x",
            min_score=1.5,
        )


@pytest.mark.asyncio
async def test_upload_voice_sample_requires_staff() -> None:
    req = UploadVoiceSampleInput(
        person_id=uuid.uuid4(),
        byte_size=1000,
        duration_seconds=5.0,
    )
    with pytest.raises(ToolError) as exc:
        await upload_voice_sample(_ctx("CLIENT", "media:write voice:write"), req)
    assert exc.value.code == INSUFFICIENT_ROLE


@pytest.mark.asyncio
async def test_confirm_voice_upload_requires_staff() -> None:
    req = ConfirmVoiceUploadInput(
        voice_sample_id=uuid.uuid4(), sha256_hex="a" * 64
    )
    with pytest.raises(ToolError) as exc:
        await confirm_voice_upload(_ctx("CLIENT", "media:write voice:write"), req)
    assert exc.value.code == INSUFFICIENT_ROLE


@pytest.mark.asyncio
async def test_list_voice_samples_requires_scope() -> None:
    with pytest.raises(ToolError):
        await list_voice_samples(
            _ctx("CLIENT", ""), ListVoiceSamplesInput(person_id=uuid.uuid4())
        )


@pytest.mark.asyncio
async def test_match_speaker_requires_staff() -> None:
    req = MatchSpeakerInput(
        embedding=[0.0] * EMBEDDING_DIM,
        embedding_model="parakeet-tdt-1.1b-speaker-256d",
    )
    with pytest.raises(ToolError) as exc:
        await match_speaker(_ctx("CLIENT", "voice:match"), req)
    assert exc.value.code == INSUFFICIENT_ROLE
