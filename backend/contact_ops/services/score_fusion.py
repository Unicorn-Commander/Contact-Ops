"""Score fusion for short-utterance speaker verification.

Phase 3 Design §10.3: when a segment is shorter than 3 seconds, the
WeSpeaker ResNet34-LM primary extractor degrades. ERes2NetV2 has 0.98% EER
at 3s versus ~2.5% for ResNet34-LM. We fuse both scores with empirically
calibrated weights.

    fused = 0.6 * eres2netv2 + 0.4 * wespeaker_resnet34_lm

These weights are test-pinnable: ``fuse_short_utt_scores(0.8, 0.9)`` always
returns ``0.6 * 0.8 + 0.4 * 0.9 = 0.84``.
"""

from __future__ import annotations

ERES2NETV2_WEIGHT = 0.6
"""Fusion weight for ERes2NetV2 (dominant on short utterances <3s)."""

WESPEAKER_WEIGHT = 0.4
"""Fusion weight for WeSpeaker ResNet34-LM."""


def fuse_short_utt_scores(
    eres2netv2_score: float,
    wespeaker_score: float,
) -> float:
    """Fuse two verification scores into one fused score.

    Args:
        eres2netv2_score: Cosine similarity from ERes2NetV2 (0..1).
        wespeaker_score: Cosine similarity from WeSpeaker ResNet34-LM (0..1).

    Returns:
        Fused score in [0, 1]. The weights are global constants so tests
        can pin them.
    """
    return ERES2NETV2_WEIGHT * eres2netv2_score + WESPEAKER_WEIGHT * wespeaker_score


__all__ = [
    "ERES2NETV2_WEIGHT",
    "WESPEAKER_WEIGHT",
    "fuse_short_utt_scores",
]
