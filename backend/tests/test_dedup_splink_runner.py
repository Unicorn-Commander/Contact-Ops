from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import jellyfish
import pandas as pd
import pytest

jellyfish.double_metaphone = jellyfish.metaphone

from contact_ops.agents.dedup.splink_runner import (
    build_splink_settings,
    effective_weight,
)


class TestBuildSplinkSettings:
    def test_build_splink_settings_structure(self) -> None:
        settings = build_splink_settings()
        assert isinstance(settings, dict)
        assert settings["link_type"] == "dedupe_only"
        assert settings["unique_id_column_name"] == "person_id"
        assert "comparisons" in settings
        assert len(settings["comparisons"]) == 11
        assert "blocking_rules_to_generate_predictions" in settings
        assert len(settings["blocking_rules_to_generate_predictions"]) == 5
        assert "term_frequency_adjustments" in settings
        assert len(settings["term_frequency_adjustments"]) == 2

        comparison_names = {c["output_column_name"] for c in settings["comparisons"]}
        expected = {
            "first_name", "last_name", "email", "phone", "dob",
            "address", "name_embedding", "face_embedding",
            "voice_fingerprint", "company", "government_id",
        }
        assert comparison_names == expected


@pytest.mark.asyncio
class TestEffectiveWeight:
    async def test_effective_weight(self) -> None:
        tenant_id = uuid4()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = 0.90
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "contact_ops.agents.dedup.splink_runner._WEIGHT_CACHE",
            {},
        ):
            weight = await effective_weight(
                raw_bits=10.0,
                source_kind="carddav",
                tenant_id=tenant_id,
                db_session=mock_session,
            )
        assert weight == 9.0  # 10.0 * 0.90
        mock_session.execute.assert_awaited_once()


@pytest.mark.asyncio
class TestScoreViaFallback:
    async def test_score_via_fallback(self) -> None:
        tenant_id = uuid4()
        a_id = uuid4()
        b_id = uuid4()

        df = pd.DataFrame({
            "person_id_l": [str(a_id)],
            "person_id_r": [str(b_id)],
            "first_name_l": ["Aaron"],
            "first_name_r": ["Aaron"],
            "last_name_l": ["Smith"],
            "last_name_r": ["Smith"],
            "email_l": ["REMOVED"],
            "email_r": ["REMOVED"],
            "phone_l": ["+18439019078"],
            "phone_r": ["+18439019078"],
            "dob_l": ['{"year": 1990, "month": 1, "day": 15}'],
            "dob_r": ['{"year": 1990, "month": 1, "day": 15}'],
            "company_l": ["Acme"],
            "company_r": ["Acme"],
            "government_id_l": ["XXX-XX-1234"],
            "government_id_r": ["XXX-XX-1234"],
            "address_l": ["123 Main St"],
            "address_r": ["123 Main St"],
            "name_embedding_l": ["[1.0, 0.0]"],
            "name_embedding_r": ["[1.0, 0.0]"],
            "face_embedding_l": ["[1.0, 0.0]"],
            "face_embedding_r": ["[1.0, 0.0]"],
            "voice_fingerprint_l": ["[1.0, 0.0]"],
            "voice_fingerprint_r": ["[1.0, 0.0]"],
            "source_kind": ["carddav"],
            "blocking_key": ["email_exact"],
        })

        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none.return_value = 1.0
        mock_session.execute.return_value = mock_result

        with patch(
            "contact_ops.agents.dedup.splink_runner._WEIGHT_CACHE",
            {},
        ), patch(
            "contact_ops.agents.dedup.splink_runner.SPLINK_AVAILABLE",
            False,
        ):
            from contact_ops.agents.dedup.splink_runner import _score_via_fallback

            result = await _score_via_fallback(
                df,
                tenant_id=tenant_id,
                db_session=mock_session,
            )
        assert len(result.scored_pairs) == 1
        pair = result.scored_pairs[0]
        assert pair.person_a_id == a_id
        assert pair.person_b_id == b_id
        assert pair.match_weight_bits > 0.0
        assert pair.source_kind == "carddav"
        assert pair.blocking_key == "email_exact"
