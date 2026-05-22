"""Tests for voice extractor services.

Covers:
- WeSpeakerExtractor: lazy init, 256-dim L2-normalized output, GPU/CPU fallback
- ERes2NetV2Extractor: short-utterance fallback, dimension projection
- Score fusion: weight constants, fuse_short_utt_scores math
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from contact_ops.services.score_fusion import (
    ERES2NETV2_WEIGHT,
    WESPEAKER_WEIGHT,
    fuse_short_utt_scores,
)
from contact_ops.services.voice_extractor import (
    EMBEDDING_DIM,
    WeSpeakerExtractor,
    get_voice_extractor,
    reset_voice_extractor,
)


class TestWeSpeakerExtractor:
    """WeSpeaker ResNet34-LM extractor contract tests."""

    @pytest.fixture(autouse=True)
    def _reset(self) -> None:
        reset_voice_extractor()

    @pytest.fixture
    def mock_pyannote_model(self) -> MagicMock:
        model = MagicMock()
        # Return a 256-dim tensor
        model.return_value = MagicMock(
            cpu=lambda: MagicMock(
                numpy=lambda: np.random.randn(1, EMBEDDING_DIM).astype(np.float32)
            )
        )
        return model

    @pytest.mark.asyncio
    async def test_lazy_init_does_not_load_at_construction(self) -> None:
        """Model is not loaded when the extractor is constructed."""
        extractor = WeSpeakerExtractor(device="cpu")
        assert extractor._model is None

    @pytest.mark.asyncio
    async def test_returns_256_dim_float32(self) -> None:
        """Extracted embedding is 256-dim float32."""
        extractor = WeSpeakerExtractor(device="cpu")
        with patch.object(extractor, "_load_model", new_callable=AsyncMock):
            with patch.object(
                extractor,
                "_model",
                MagicMock(
                    return_value=MagicMock(
                        cpu=lambda: MagicMock(
                            numpy=lambda: np.random.randn(EMBEDDING_DIM).astype(
                                np.float32
                            )
                        )
                    )
                ),
            ):
                audio = np.zeros((1, 16000), dtype=np.float32)
                emb = await extractor.extract_embedding(audio, sr=16000)

        assert emb.shape == (EMBEDDING_DIM,)
        assert emb.dtype == np.float32

    @pytest.mark.asyncio
    async def test_output_is_l2_normalized(self) -> None:
        """Extracted embedding has unit L2 norm."""
        extractor = WeSpeakerExtractor(device="cpu")
        with patch.object(extractor, "_load_model", new_callable=AsyncMock):
            with patch.object(extractor, "_model", MagicMock()):
                audio = np.zeros((1, 16000), dtype=np.float32)
                emb = await extractor.extract_embedding(audio, sr=16000)

        norm = np.linalg.norm(emb)
        assert abs(norm - 1.0) < 1e-5 or norm == 0.0

    @pytest.mark.asyncio
    async def test_handles_mono_audio(self) -> None:
        """1D mono audio does not crash."""
        extractor = WeSpeakerExtractor(device="cpu")
        with patch.object(extractor, "_load_model", new_callable=AsyncMock):
            with patch.object(extractor, "_model", MagicMock()):
                audio = np.zeros(16000, dtype=np.float32)
                emb = await extractor.extract_embedding(audio, sr=16000)

        assert emb.shape == (EMBEDDING_DIM,)

    @pytest.mark.asyncio
    async def test_handles_stereo_audio(self) -> None:
        """2-channel audio does not crash (pyannote auto-downmixes)."""
        extractor = WeSpeakerExtractor(device="cpu")
        with patch.object(extractor, "_load_model", new_callable=AsyncMock):
            with patch.object(extractor, "_model", MagicMock()):
                audio = np.zeros((2, 16000), dtype=np.float32)
                emb = await extractor.extract_embedding(audio, sr=16000)

        assert emb.shape == (EMBEDDING_DIM,)

    def test_singleton(self) -> None:
        """get_voice_extractor returns the same instance."""
        e1 = get_voice_extractor()
        e2 = get_voice_extractor()
        assert e1 is e2

        reset_voice_extractor()
        e3 = get_voice_extractor()
        assert e3 is not e1

    def test_embedding_model_version(self) -> None:
        """Model version string is correct."""
        extractor = WeSpeakerExtractor()
        assert extractor.embedding_model_version == "wespeaker-resnet34-LM-2024.03"


class TestScoreFusion:
    """Score fusion contract tests."""

    def test_weights_have_correct_values(self) -> None:
        """Weights match Phase 3 Design §10.3."""
        assert ERES2NETV2_WEIGHT == 0.6
        assert WESPEAKER_WEIGHT == 0.4
        assert abs(ERES2NETV2_WEIGHT + WESPEAKER_WEIGHT - 1.0) < 1e-9

    def test_fusion_math_correct(self) -> None:
        """fuse_short_utt_scores(0.8, 0.9) == 0.84."""
        result = fuse_short_utt_scores(0.8, 0.9)
        expected = 0.6 * 0.8 + 0.4 * 0.9
        assert abs(result - expected) < 1e-9

    def test_fusion_extremes(self) -> None:
        """Boundary values work correctly."""
        assert abs(fuse_short_utt_scores(1.0, 1.0) - 1.0) < 1e-9
        assert abs(fuse_short_utt_scores(0.0, 0.0) - 0.0) < 1e-9
        assert abs(fuse_short_utt_scores(0.5, 0.5) - 0.5) < 1e-9


class TestShortUttExtractor:
    """ERes2NetV2 short-utterance extractor contract tests."""

    @pytest.mark.asyncio
    async def test_fallback_returns_256_dim(self) -> None:
        """ERes2NetV2 returns 256-dim even when model unavailable."""
        from contact_ops.services.short_utt_extractor import (
            ERes2NetV2Extractor,
            reset_short_utt_extractor,
        )

        reset_short_utt_extractor()
        extractor = ERes2NetV2Extractor(device="cpu")
        audio = np.zeros((1, 16000), dtype=np.float32)
        emb = await extractor.extract_embedding(audio, sr=16000)

        assert emb.shape == (EMBEDDING_DIM,)
        assert emb.dtype == np.float32

    @pytest.mark.asyncio
    async def test_lazy_init(self) -> None:
        """Model not loaded at construction."""
        from contact_ops.services.short_utt_extractor import ERes2NetV2Extractor

        extractor = ERes2NetV2Extractor(device="cpu")
        assert extractor._model is None

    @pytest.mark.asyncio
    async def test_dimension_projection(self) -> None:
        """Non-256-dim output is projected to 256 (§10.3)."""
        from contact_ops.services.short_utt_extractor import ERes2NetV2Extractor

        extractor = ERes2NetV2Extractor(device="cpu")
        # Mock the pipeline to return 128-dim
        with patch.object(extractor, "_load_model", new_callable=AsyncMock):
            extractor._model = MagicMock(
                return_value={"spk_embedding": np.random.randn(128).astype(np.float32)}
            )
            audio = np.zeros((1, 16000), dtype=np.float32)
            emb = await extractor.extract_embedding(audio, sr=16000)

        assert emb.shape == (EMBEDDING_DIM,)
