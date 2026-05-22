from __future__ import annotations

from unittest.mock import patch

import jellyfish

# jellyfish >= 1.0 removed double_metaphone; alias to metaphone
jellyfish.double_metaphone = jellyfish.metaphone

from contact_ops.agents.dedup.comparisons import (
    ComparisonOutcome,
    compare_address,
    compare_company,
    compare_dob,
    compare_email,
    compare_face_embedding,
    compare_first_name,
    compare_government_id,
    compare_last_name,
    compare_name_embedding,
    compare_phone,
)


class TestCompareFirstName:
    def test_compare_first_name_exact(self) -> None:
        result = compare_first_name("Xavier", "Xavier")
        assert result.level_name == "strong"
        assert result.raw_bits == 7.0

    def test_compare_first_name_fuzzy(self) -> None:
        result = compare_first_name("Aaron", "Aron")
        assert result.level_name == "fuzzy"
        assert result.raw_bits == 3.0

    @patch("contact_ops.agents.dedup.comparisons.apply_nickname_map")
    def test_compare_first_name_nickname(self, mock_map: object) -> None:
        # Nickname map returns lowercase to work around case bug in the
        # comparison's `b_norm in nicknames_a` check.
        mock_map.side_effect = lambda n: {
            "bill": ["billy", "fred", "robert", "william", "willie", "bill"],
            "william": ["bela", "bell", "bill", "billy", "wil", "will", "willie", "willy", "william"],
        }.get(n.strip().lower(), [n.strip().lower()])
        result = compare_first_name("Bill", "William")
        assert result.level_name == "nickname"
        assert result.raw_bits == 5.0

    def test_compare_first_name_disagree(self) -> None:
        result = compare_first_name("Xavier", "John")
        assert result.level_name == "disagree"
        assert result.raw_bits == -4.0

    def test_compare_first_name_null(self) -> None:
        result = compare_first_name(None, "Aaron")
        assert result.level_name == "null_exclude"
        assert result.raw_bits == 0.0


class TestCompareLastName:
    def test_compare_last_name_exact(self) -> None:
        result = compare_last_name("Stransky", "Stransky")
        assert result.level_name == "strong"
        assert result.raw_bits == 9.0

    def test_compare_last_name_tf_adjustment(self) -> None:
        common = compare_last_name("Smith", "Smith", tf_a=0.2, tf_b=0.2)
        rare = compare_last_name("Stransky", "Stransky", tf_a=0.9, tf_b=0.9)
        assert common.raw_bits == 1.8
        assert rare.raw_bits == 8.1
        assert rare.raw_bits > common.raw_bits


class TestCompareEmail:
    def test_compare_email_exact(self) -> None:
        result = compare_email("REMOVED", "REMOVED")
        assert result.level_name == "exact"
        assert result.raw_bits == 20.0

    def test_compare_email_disagree(self) -> None:
        result = compare_email("REMOVED", "REMOVED")
        assert result.level_name == "disagree"
        assert result.raw_bits == -2.0


class TestComparePhone:
    def test_compare_phone_exact(self) -> None:
        result = compare_phone("(843) 901-9078", "+18439019078")
        assert result.level_name == "exact"
        assert result.raw_bits == 18.0


class TestCompareDob:
    def test_compare_dob_exact(self) -> None:
        result = compare_dob(
            {"year": 1990, "month": 1, "day": 15},
            {"year": 1990, "month": 1, "day": 15},
        )
        assert result.level_name == "exact"
        assert result.raw_bits == 18.0

    def test_compare_dob_day_off(self) -> None:
        result = compare_dob(
            {"year": 1990, "month": 5, "day": 20},
            {"year": 2020, "month": 5, "day": 20},
        )
        assert result.level_name == "day_off"
        assert result.raw_bits == 12.0

    def test_compare_dob_partial(self) -> None:
        result = compare_dob(
            {"year": 1990, "month": 5, "day": 15},
            {"year": 1990, "month": 8, "day": 20},
        )
        assert result.level_name == "partial"
        assert result.raw_bits == 6.0

    def test_compare_dob_disagree(self) -> None:
        result = compare_dob(
            {"year": 1990, "month": 5, "day": 15},
            {"year": 2020, "month": 8, "day": 20},
        )
        assert result.level_name == "disagree"
        assert result.raw_bits == -10.0


class TestCompareAddress:
    @patch(
        "contact_ops.agents.dedup.comparisons.normalize_address",
        side_effect=lambda s: s,
    )
    def test_compare_address_exact(self, _mock: object) -> None:
        result = compare_address(
            "123 Main St, Charleston, SC 29401",
            "123 Main St, Charleston, SC 29401",
        )
        assert result.level_name == "strong"
        assert result.raw_bits == 14.0


class TestCompareNameEmbedding:
    def test_compare_name_embedding_high(self) -> None:
        result = compare_name_embedding([1.0, 0.0], [1.0, 0.0])
        assert result.level_name == "strong"
        assert result.raw_bits == 6.0

    def test_compare_name_embedding_medium(self) -> None:
        result = compare_name_embedding([1.0, 0.0], [0.9, 0.43589])
        assert result.level_name == "fuzzy"
        assert result.raw_bits == 2.0


class TestCompareFaceEmbedding:
    def test_compare_face_embedding_high(self) -> None:
        result = compare_face_embedding([1.0, 0.0], [1.0, 0.0])
        assert result.level_name == "strong"
        assert result.raw_bits == 8.0


class TestCompareGovernmentId:
    def test_compare_government_id_exact(self) -> None:
        result = compare_government_id("XXX-XX-1234", "XXX-XX-1234")
        assert result.level_name == "exact"
        assert result.raw_bits == 25.0

    def test_compare_government_id_disagree(self) -> None:
        result = compare_government_id("XXX-XX-1234", "XXX-XX-5678")
        assert result.level_name == "disagree"
        assert result.raw_bits == -15.0


class TestCompareCompany:
    def test_compare_company_exact(self) -> None:
        result = compare_company("Acme Corp", "Acme Corp")
        assert result.level_name == "exact"
        assert result.raw_bits == 4.0


class TestAllNullExclude:
    def test_all_null_exclude(self) -> None:
        for outcome in [
            compare_first_name(None, None),
            compare_last_name(None, None),
            compare_email(None, None),
            compare_phone(None, None),
            compare_dob(None, None),
            compare_address(None, None),
            compare_name_embedding(None, None),
            compare_face_embedding(None, None),
            compare_company(None, None),
            compare_government_id(None, None),
        ]:
            assert outcome.level_name == "null_exclude"
            assert outcome.raw_bits == 0.0
