"""Contact-Ops domain services.

Phase 3.2 adds voice-biometric services (Voice Match Agent's
extractors, score fusion, Qdrant access, consent lifecycle).
"""

from contact_ops.services.score_fusion import (
    ERES2NETV2_WEIGHT,
    WESPEAKER_WEIGHT,
    fuse_short_utt_scores,
)
from contact_ops.services.short_utt_extractor import (
    ERes2NetV2Extractor,
    get_short_utt_extractor,
    reset_short_utt_extractor,
)
from contact_ops.services.voice_consent import (
    VoiceConsentService,
    get_voice_consent_service,
    reset_voice_consent_service,
)
from contact_ops.services.voice_extractor import (
    WeSpeakerExtractor,
    get_voice_extractor,
    reset_voice_extractor,
)

__all__ = [
    "ERES2NETV2_WEIGHT",
    "ERes2NetV2Extractor",
    "VoiceConsentService",
    "WESPEAKER_WEIGHT",
    "WeSpeakerExtractor",
    "fuse_short_utt_scores",
    "get_short_utt_extractor",
    "get_voice_consent_service",
    "get_voice_extractor",
    "reset_short_utt_extractor",
    "reset_voice_consent_service",
    "reset_voice_extractor",
]
