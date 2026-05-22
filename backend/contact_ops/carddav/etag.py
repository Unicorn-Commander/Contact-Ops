"""ETag computation + RFC 7232 precondition helpers for CardDAV.

The ``persons.etag`` column is maintained by the ``touch_updated``
trigger in Alembic 0015 — it is the SHA-256 hex digest of the row
payload minus ``etag``/``updated_at``. We surface that value in both the
HTTP ``ETag`` header (DQUOTE-wrapped per RFC 7232 §2.3) and the WebDAV
``{DAV:}getetag`` XML property.

iOS Contacts.app refuses to participate in incremental sync without
ETags that change on every write — the DB trigger guarantees that.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Literal

from fastapi import HTTPException, status


def quote_etag(value: str | None) -> str:
    """Wrap a bare hex digest in DQUOTE for the ``ETag`` header.

    Returns the literal string ``"<digest>"`` (the quotes are part of
    the header value per RFC 7232). Falls back to ``W/"0"`` for empty
    inputs — CardDAV clients drop the resource if no ETag is sent.
    """

    if not value:
        return 'W/"0"'
    bare = value.strip().strip('"')
    return f'"{bare}"'


def strip_etag_quotes(value: str | None) -> str | None:
    """Inverse of :func:`quote_etag` for matching against stored values."""

    if value is None:
        return None
    cleaned = value.strip()
    if cleaned.startswith("W/"):
        cleaned = cleaned[2:]
    return cleaned.strip().strip('"') or None


def derived_etag_from_text(payload: str) -> str:
    """Compute a deterministic etag for arbitrary bytes (tests + helpers)."""

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Precondition:
    """Parsed RFC 7232 ``If-Match`` / ``If-None-Match`` header.

    ``mode``:
      * ``"any"``        — header was ``*``, any current state satisfies
      * ``"specific"``   — etags is non-empty; current must match one
      * ``"none"``       — no header was supplied
    """

    header: Literal["if-match", "if-none-match"]
    mode: Literal["any", "specific", "none"]
    etags: tuple[str, ...]


def parse_precondition(
    *,
    header_name: Literal["if-match", "if-none-match"],
    raw: str | None,
) -> Precondition:
    """Parse a single If-Match / If-None-Match header value."""

    if raw is None:
        return Precondition(header=header_name, mode="none", etags=())
    cleaned = raw.strip()
    if cleaned == "*":
        return Precondition(header=header_name, mode="any", etags=())
    tags: list[str] = []
    for token in _split_etag_list(cleaned):
        stripped = strip_etag_quotes(token)
        if stripped:
            tags.append(stripped)
    if not tags:
        return Precondition(header=header_name, mode="none", etags=())
    return Precondition(header=header_name, mode="specific", etags=tuple(tags))


def evaluate_if_match(precondition: Precondition, current: str | None) -> bool:
    """``If-Match`` semantics — True iff the precondition is satisfied."""

    if precondition.header != "if-match":
        raise ValueError("evaluate_if_match called with wrong header")
    if precondition.mode == "none":
        return True
    if precondition.mode == "any":
        return bool(current)
    return current is not None and current in precondition.etags


def evaluate_if_none_match(precondition: Precondition, current: str | None) -> bool:
    """``If-None-Match`` semantics — True iff the precondition is satisfied."""

    if precondition.header != "if-none-match":
        raise ValueError("evaluate_if_none_match called with wrong header")
    if precondition.mode == "none":
        return True
    if precondition.mode == "any":
        return current is None
    return current is None or current not in precondition.etags


def enforce_preconditions(
    *,
    if_match: str | None,
    if_none_match: str | None,
    current: str | None,
) -> None:
    """Raise HTTP 412 if either RFC 7232 precondition fails.

    Both headers are evaluated; ``If-Match`` failure wins precedence.
    ``current`` is the stored etag (DQUOTE stripped) or ``None`` when
    the target resource does not yet exist.
    """

    if_match_pre = parse_precondition(header_name="if-match", raw=if_match)
    if not evaluate_if_match(if_match_pre, current):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail="If-Match precondition failed",
        )
    inm_pre = parse_precondition(header_name="if-none-match", raw=if_none_match)
    if not evaluate_if_none_match(inm_pre, current):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail="If-None-Match precondition failed",
        )


def collection_etag(member_etags: Iterable[str]) -> str:
    """Compute a deterministic ETag for an addressbook collection.

    The collection's ETag must change whenever any contained vCard's
    ETag changes (so iOS triggers a refresh). We hash the sorted member
    list to satisfy that contract cheaply.
    """

    h = hashlib.sha256()
    for tag in sorted(member_etags):
        if not tag:
            continue
        h.update(tag.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _split_etag_list(raw: str) -> list[str]:
    """Split a comma-separated etag list, honoring quoted commas."""

    out: list[str] = []
    buf: list[str] = []
    in_quote = False
    for ch in raw:
        if ch == '"':
            in_quote = not in_quote
            buf.append(ch)
            continue
        if ch == "," and not in_quote:
            token = "".join(buf).strip()
            if token:
                out.append(token)
            buf = []
            continue
        buf.append(ch)
    token = "".join(buf).strip()
    if token:
        out.append(token)
    return out
