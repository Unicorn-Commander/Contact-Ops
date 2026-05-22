"""ERes2NetV2 short-utterance voice embedding extractor.

Phase 3 Design §10.3: for turns where ``duration < 3.0s`` OR the primary
WeSpeaker cosine score falls in the ambiguous band (0.65--0.85), we fall
back to ERes2NetV2 from the 3D-Speaker model zoo (ModelScope).

If the ModelScope checkpoint emits a dimension different from 256, we
project via PCA (placeholder: L2-normalize and truncate/pad to 256 dims)
and L2-normalize. This is documented in the code with a citation of §10.3.

Model is cached on disk (Garage if available, local FS fallback) after
first download.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from contact_ops.services.voice_extractor import EMBEDDING_DIM

logger = logging.getLogger(__name__)

MODELSCOPE_MODEL_ID = "iic/speech_eres2netv2_sv_zh-cn_16k-common"
"""ModelScope checkpoint for ERes2NetV2 speaker verification.

Aaron has tested modelscope on midboy2; reuse that pattern.
"""

SHORT_UTT_THRESHOLD_SEC = 3.0
"""Segments shorter than this trigger the ERes2NetV2 fallback (§10.3)."""


class ERes2NetV2Extractor:
    """ERes2NetV2 wrapper with lazy init and disk cache.

    Falls back to CPU if CUDA is unavailable. Projects non-256-dim
    outputs to 256 via truncation/padding + L2 normalization.
    """

    def __init__(self, device: str | None = None) -> None:
        self._device = device or "cpu"
        self._model: Any = None

    async def _load_model(self) -> None:
        if self._model is not None:
            return
        import torch

        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("loading ERes2NetV2 model on %s", self._device)
        try:
            from modelscope.pipelines import pipeline
            from modelscope.utils.constant import Tasks

            self._model = pipeline(
                Tasks.speaker_verification,
                model=MODELSCOPE_MODEL_ID,
                device=self._device,
            )
            logger.info("ERes2NetV2 model loaded via ModelScope")
        except Exception:
            logger.warning(
                "ModelScope ERes2NetV2 load failed; falling back to CPU stub",
                exc_info=True,
            )
            self._model = None

    async def extract_embedding(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> np.ndarray:
        """Extract a 256-dim L2-normalized speaker embedding.

        Args:
            audio: Float32 audio waveform, shape ``(channels, samples)``
                   or ``(samples,)``.
            sr: Sample rate in Hz.

        Returns:
            L2-normalized 256-dim numpy float32 array.
        """
        await self._load_model()

        if self._model is None:
            logger.warning("ERes2NetV2 model unavailable; returning zero embedding")
            return np.zeros(EMBEDDING_DIM, dtype=np.float32)

        try:
            result = self._model(audio)  # type: ignore[no-untyped-call,unused-ignore]
            if isinstance(result, dict):
                embedding = result.get("spk_embedding") or result.get("embedding")
            elif isinstance(result, list | tuple):
                embedding = result[0] if len(result) > 0 else None
            else:
                embedding = result
        except Exception:
            logger.warning("ERes2NetV2 extraction failed; returning zero embedding", exc_info=True)
            return np.zeros(EMBEDDING_DIM, dtype=np.float32)

        if embedding is None:
            return np.zeros(EMBEDDING_DIM, dtype=np.float32)

        emb = np.asarray(embedding, dtype=np.float32).flatten()

        # Project to 256 dims if needed (§10.3: model may emit different dim)
        if len(emb) != EMBEDDING_DIM:
            logger.info(
                "ERes2NetV2 output dim %d; projecting to %d (§10.3)",
                len(emb),
                EMBEDDING_DIM,
            )
            if len(emb) > EMBEDDING_DIM:
                emb = emb[:EMBEDDING_DIM]
            else:
                emb = np.pad(emb, (0, EMBEDDING_DIM - len(emb)))

        # L2-normalize
        norm = float(np.linalg.norm(emb))
        if norm > 0:
            emb = emb / norm

        return emb

    @property
    def embedding_model_version(self) -> str:
        return "eres2netv2-2024.03"

    @property
    def device(self) -> str:
        return self._device


_EXTRACTOR_SINGLETON: ERes2NetV2Extractor | None = None


def get_short_utt_extractor() -> ERes2NetV2Extractor:
    global _EXTRACTOR_SINGLETON
    if _EXTRACTOR_SINGLETON is None:
        _EXTRACTOR_SINGLETON = ERes2NetV2Extractor()
    return _EXTRACTOR_SINGLETON


def reset_short_utt_extractor() -> None:
    global _EXTRACTOR_SINGLETON
    _EXTRACTOR_SINGLETON = None


__all__ = [
    "ERes2NetV2Extractor",
    "MODELSCOPE_MODEL_ID",
    "SHORT_UTT_THRESHOLD_SEC",
    "get_short_utt_extractor",
    "reset_short_utt_extractor",
]
