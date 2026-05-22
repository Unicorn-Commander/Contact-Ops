"""Unit tests for the CardDAV Basic-auth header decoder + rate limiter.

DB-backed integration tests (full resolve_principal flow) live in
``test_carddav_router.py`` once the testcontainer harness ships.
"""

from __future__ import annotations

import base64

import bcrypt

from contact_ops.carddav.auth import (
    WWW_AUTHENTICATE_HEADER,
    hash_app_password,
    is_rate_limited,
    parse_basic_auth_header,
    record_failure,
    record_success,
    reset_rate_limits,
    verify_app_password,
)


# ---------- header parsing ----------


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_parse_basic_auth_header_returns_user_password() -> None:
    header = f"Basic {_b64('aaron:secret-token')}"
    result = parse_basic_auth_header(header)
    assert result == ("aaron", "secret-token")


def test_parse_basic_auth_header_accepts_case_insensitive_scheme() -> None:
    header = f"basic {_b64('user:pw')}"
    assert parse_basic_auth_header(header) == ("user", "pw")


def test_parse_basic_auth_header_handles_password_with_colon() -> None:
    header = f"Basic {_b64('user:pw:with:colons')}"
    assert parse_basic_auth_header(header) == ("user", "pw:with:colons")


def test_parse_basic_auth_header_returns_none_when_missing() -> None:
    assert parse_basic_auth_header(None) is None
    assert parse_basic_auth_header("") is None


def test_parse_basic_auth_header_returns_none_on_wrong_scheme() -> None:
    assert parse_basic_auth_header("Bearer abc") is None
    assert parse_basic_auth_header("Digest foo=bar") is None


def test_parse_basic_auth_header_returns_none_on_garbage_base64() -> None:
    assert parse_basic_auth_header("Basic !!!not-valid-base64@@@") is None


def test_parse_basic_auth_header_returns_none_when_no_colon_in_decoded() -> None:
    assert parse_basic_auth_header(f"Basic {_b64('no-colon-here')}") is None


def test_www_authenticate_header_has_realm_and_charset() -> None:
    assert 'realm=' in WWW_AUTHENTICATE_HEADER
    assert 'charset' in WWW_AUTHENTICATE_HEADER


# ---------- bcrypt helpers ----------


def test_hash_and_verify_app_password() -> None:
    plaintext = "test-secret-password-xyz"
    hashed = hash_app_password(plaintext)
    assert hashed.startswith("$2")  # bcrypt prefix
    assert verify_app_password(plaintext, hashed) is True
    assert verify_app_password("wrong-password", hashed) is False


def test_verify_app_password_returns_false_on_malformed_hash() -> None:
    assert verify_app_password("anything", "not-a-bcrypt-hash") is False


def test_hash_app_password_uses_high_cost_factor() -> None:
    h = hash_app_password("x")
    # bcrypt format: $2b$<cost>$<22-char-salt><31-char-digest>
    cost = int(h.split("$")[2])
    assert cost >= 10


# ---------- rate limiter ----------


def test_rate_limit_threshold_triggers_after_5_failures() -> None:
    reset_rate_limits()
    ip, user = "192.0.2.1", "victim"
    for _ in range(5):
        record_failure(ip, user)
    assert is_rate_limited(ip, user) is True


def test_rate_limit_does_not_apply_to_other_ips() -> None:
    reset_rate_limits()
    for _ in range(5):
        record_failure("attacker", "victim")
    assert is_rate_limited("victim-own-ip", "victim") is False


def test_rate_limit_resets_on_success() -> None:
    reset_rate_limits()
    ip, user = "192.0.2.2", "user"
    for _ in range(4):
        record_failure(ip, user)
    record_success(ip, user)
    assert is_rate_limited(ip, user) is False


def test_rate_limit_isolated_per_user() -> None:
    reset_rate_limits()
    for _ in range(5):
        record_failure("ip", "user-a")
    assert is_rate_limited("ip", "user-a") is True
    assert is_rate_limited("ip", "user-b") is False
