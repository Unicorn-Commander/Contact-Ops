"""WeSpeaker ResNet34-LM voice embedding extractor.

Primary extractor for the Voice Match Agent (Phase 3 Design §10.2).
Uses ``pyannote/wespeaker-voxceleb-resnet34-LM`` to extract 256-dim
L2-normalized speaker embeddings.

Lazy-init: the model is loaded on the first ``extract_embedding`` call,
not at import time. This avoids paying cold-start on worker startup.

GPU/CPU fallback is transparent — if CUDA is available we use it,
otherwise CPU. Stereo audio is handled by pyannote 3.1's auto-downmix.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import numpy as np

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 256
"""Dimension of WeSpeaker ResNet34-LM embeddings — matches Phase 2 schema."""

HF_MODEL_ID = "pyannote/wespeaker-voxceleb-resnet34-LM"
"""HuggingFace model identifier for the primary voice extractor."""


class WeSpeakerExtractor:
    """pyannote/wespeaker-voxceleb-resnet34-LM wrapper with deferred init.

    Usage::

        extractor = WeSpeakerExtractor()
        embedding = await extractor.extract_embedding(audio, sr)
        # embedding is a 256-dim L2-normalized numpy array
    """

    def __init__(self, device: str | None = None) -> None:
        self._device = device or "cpu"
        self._model: Any = None

    async def _load_model(self) -> None:
        """Lazy-load the pyannote model on first call."""
        if self._model is not None:
            return
        import torch
        from pyannote.audio import Model

        self._device = "cuda" if torch.cuda.is_available() else "cpu"

        logger.info(
            "loading WeSpeaker ResNet34-LM model on %s",
            self._device,
        )
        model = Model.from_pretrained(
            HF_MODEL_ID,
            use_auth_token=None,
        )
        model.eval()
        self._model = model.to(self._device)
        logger.info("WeSpeaker ResNet34-LM loaded")

    async def extract_embedding(
        self,
        audio: np.ndarray,
        sr: int,
    ) -> np.ndarray:
        """Extract a 256-dim L2-normalized speaker embedding.

        Args:
            audio: Float32 audio waveform, shape ``(channels, samples)``
                   or ``(samples,)``. pyannote 3.1 auto-downmixes stereo.
            sr: Sample rate in Hz. Should be 16kHz. Resampling is caller's
                responsibility.

        Returns:
            L2-normalized 256-dim numpy float32 array.
        """
        await self._load_model()

        # Ensure 2D: (channels, samples)
        if audio.ndim == 1:
            audio_2d = audio[np.newaxis, :]
        else:
            audio_2d = audio

        import torch

        # pyannote expects torch tensor on the model's device
        waveform = torch.from_numpy(audio_2d).float().to(self._device)

        with torch.no_grad():
            embedding_tensor = self._model(waveform)  # type: ignore[no-untyped-call,unused-ignore]

        embedding = embedding_tensor.cpu().numpy().flatten()

        # L2-normalize
        norm = float(np.linalg.norm(embedding))
        if norm > 0:
            embedding = embedding / norm

        return cast(np.ndarray, embedding.astype(np.float32))

    @property
    def embedding_model_version(self) -> str:
        return "wespeaker-resnet34-LM-2024.03"

    @property
    def device(self) -> str:
        return self._device


_EXTRACTOR_SINGLETON: WeSpeakerExtractor | None = None


def get_voice_extractor() -> WeSpeakerExtractor:
    global _EXTRACTOR_SINGLETON
    if _EXTRACTOR_SINGLETON is None:
        _EXTRACTOR_SINGLETON = WeSpeakerExtractor()
    return _EXTRACTOR_SINGLETON


def reset_voice_extractor() -> None:
    global _EXTRACTOR_SINGLETON
    _EXTRACTOR_SINGLETON = None


__all__ = [
    "EMBEDDING_DIM",
    "HF_MODEL_ID",
    "WeSpeakerExtractor",
    "get_voice_extractor",
    "reset_voice_extractor",
]
