from __future__ import annotations

from contact_ops.agents.dedup.normalizers import (
    apply_nickname_map,
    extract_vcard_uid,
    normalize_address,
    normalize_email,
    normalize_phone_e164,
)


class TestNormalizeEmail:
    def test_normalize_email_gmail_dot_stripping(self) -> None:
        assert normalize_email("REMOVED") == "REMOVED"

    def test_normalize_email_plus_tag(self) -> None:
        assert normalize_email("REMOVED") == "REMOVED"

    def test_normalize_email_googlemail(self) -> None:
        assert normalize_email("REMOVED") == "REMOVED"

    def test_normalize_email_non_gmail(self) -> None:
        assert normalize_email("REMOVED") == "REMOVED"

    def test_normalize_email_none(self) -> None:
        assert normalize_email(None) is None


class TestNormalizePhone:
    def test_normalize_phone_e164(self) -> None:
        assert normalize_phone_e164("(843) 901-9078", region="US") == "+18439019078"

    def test_normalize_phone_e164_invalid(self) -> None:
        assert normalize_phone_e164("not-a-phone") is None

    def test_normalize_phone_none(self) -> None:
        assert normalize_phone_e164(None) is None


class TestNormalizeAddress:
    def test_normalize_address(self) -> None:
        result = normalize_address("123 Main St Ste 4, Charleston, SC 29401")
        assert isinstance(result, dict)
        assert result["street_number"] == "123"
        assert result["street_name"] == "Main"
        assert result["street_name_suffix"] == "St"
        assert result["locality"] == "Charleston"
        assert result["region_name"] == "SC"
        assert result["postal_code"] == "29401"

    def test_normalize_address_none(self) -> None:
        assert normalize_address(None) is None

    def test_normalize_address_weird_input(self) -> None:
        result = normalize_address("!@#$%^&*()")
        assert result is None or isinstance(result, dict)


class TestExtractVcardUid:
    def test_extract_vcard_uid(self) -> None:
        source_pid_map = {
            "apple": {"uid": "AB123456-7890-1234-5678-123456789ABC"},
        }
        assert extract_vcard_uid(source_pid_map) == "AB123456-7890-1234-5678-123456789ABC"

    def test_extract_vcard_uid_none(self) -> None:
        assert extract_vcard_uid(None) is None


class TestApplyNicknameMap:
    def test_apply_nickname_map(self) -> None:
        result = apply_nickname_map("Bill")
        assert "William" in result

    def test_apply_nickname_map_none(self) -> None:
        assert apply_nickname_map(None) == []

    def test_apply_nickname_map_unknown(self) -> None:
        result = apply_nickname_map("Xyzzy")
        assert result == ["Xyzzy"]
