"""Tests for the ETag precondition layer."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from contact_ops.carddav.etag import (
    Precondition,
    collection_etag,
    derived_etag_from_text,
    enforce_preconditions,
    evaluate_if_match,
    evaluate_if_none_match,
    parse_precondition,
    quote_etag,
    strip_etag_quotes,
)


def test_quote_etag_wraps_in_dquote() -> None:
    assert quote_etag("abc") == '"abc"'
    assert quote_etag('"already-quoted"') == '"already-quoted"'


def test_quote_etag_falls_back_to_weak_when_empty() -> None:
    assert quote_etag("") == 'W/"0"'
    assert quote_etag(None) == 'W/"0"'


def test_strip_etag_quotes_handles_weak_and_strong() -> None:
    assert strip_etag_quotes('"hello"') == "hello"
    assert strip_etag_quotes('W/"weak"') == "weak"
    assert strip_etag_quotes(None) is None


def test_parse_precondition_recognizes_star_any() -> None:
    pre = parse_precondition(header_name="if-match", raw="*")
    assert pre.mode == "any"


def test_parse_precondition_recognizes_specific_list() -> None:
    pre = parse_precondition(header_name="if-match", raw='"abc", "def"')
    assert pre.mode == "specific"
    assert pre.etags == ("abc", "def")


def test_parse_precondition_returns_none_when_missing() -> None:
    pre = parse_precondition(header_name="if-match", raw=None)
    assert pre.mode == "none"


def test_evaluate_if_match_any_requires_existence() -> None:
    pre = Precondition(header="if-match", mode="any", etags=())
    assert evaluate_if_match(pre, current="abc") is True
    assert evaluate_if_match(pre, current=None) is False


def test_evaluate_if_match_specific_matches_member() -> None:
    pre = Precondition(header="if-match", mode="specific", etags=("a", "b"))
    assert evaluate_if_match(pre, current="a") is True
    assert evaluate_if_match(pre, current="c") is False
    assert evaluate_if_match(pre, current=None) is False


def test_evaluate_if_none_match_any_requires_absence() -> None:
    pre = Precondition(header="if-none-match", mode="any", etags=())
    assert evaluate_if_none_match(pre, current=None) is True
    assert evaluate_if_none_match(pre, current="a") is False


def test_evaluate_if_none_match_specific_blocks_match() -> None:
    pre = Precondition(header="if-none-match", mode="specific", etags=("a",))
    assert evaluate_if_none_match(pre, current=None) is True
    assert evaluate_if_none_match(pre, current="a") is False
    assert evaluate_if_none_match(pre, current="b") is True


def test_enforce_preconditions_raises_412_on_stale_if_match() -> None:
    with pytest.raises(HTTPException) as exc:
        enforce_preconditions(if_match='"stale"', if_none_match=None, current="fresh")
    assert exc.value.status_code == 412


def test_enforce_preconditions_raises_412_on_if_none_match_when_exists() -> None:
    with pytest.raises(HTTPException) as exc:
        enforce_preconditions(if_match=None, if_none_match="*", current="exists")
    assert exc.value.status_code == 412


def test_enforce_preconditions_ok_when_no_headers() -> None:
    # Should not raise
    enforce_preconditions(if_match=None, if_none_match=None, current="anything")


def test_enforce_preconditions_ok_when_if_match_star_and_exists() -> None:
    enforce_preconditions(if_match="*", if_none_match=None, current="exists")


def test_enforce_preconditions_ok_when_if_none_match_star_and_absent() -> None:
    enforce_preconditions(if_match=None, if_none_match="*", current=None)


def test_collection_etag_deterministic_in_member_order() -> None:
    a = collection_etag(["x", "y", "z"])
    b = collection_etag(["z", "y", "x"])
    assert a == b


def test_collection_etag_changes_with_membership() -> None:
    a = collection_etag(["x"])
    b = collection_etag(["x", "y"])
    assert a != b


def test_derived_etag_from_text_returns_sha256_hex() -> None:
    etag = derived_etag_from_text("hello")
    assert len(etag) == 64
    assert all(c in "0123456789abcdef" for c in etag)
