"""contactops_push — the shared producer connector for Contact-Ops ingest.

Write-once, drop-into-each-leadgen-app client for the REST ingest seam
(CONTACT-OPS-INGEST-CONTRACT §1B / §3). A producing app (NorthwindLeads, Unicorn
Outreach, multistate-retirement-leads, LoopNet-Leads, …) maps its *graduated* lead
to identity attributes + a source/consent block and calls :meth:`push_contact`;
Contact-Ops does the idempotent upsert and returns the shared ``contact_id`` to
store for back-reference.

Design properties (the contract's "degrade-clean + idempotent"):
  * SELF-CONTAINED — depends only on ``httpx`` + the stdlib. It does NOT import the
    contact_ops backend package, so it can be copied or git-submodule'd into any
    app without dragging the server in.
  * IDEMPOTENT — the *caller* supplies the dedup keys (email / phone /
    external_ref) in ``identity["match_by"]`` (or via emails/phones + source's
    external_ref); the server dedups on them, so a retry or a re-graduation of the
    same lead updates rather than duplicating.
  * DEGRADE-CLEAN — transient failures (timeouts, connection errors, HTTP 5xx, 429)
    are retried with backoff up to ``max_retries``; if they persist the call raises
    a typed :class:`ContactOpsPushTransientError` (never a bare httpx error), so a
    producer can catch ONE exception type and choose to queue-and-retry later
    without crashing its graduation flow. A 4xx (e.g. 403 missing scope, 422 bad
    payload) raises :class:`ContactOpsPushClientError` immediately — retrying will
    not help.

Token handling is modeled on the federation consumer SDK: pass a STATIC service
token, OR a callable / async-callable token source (e.g. one backed by a
Brigade-STS client) that the connector invokes per-push so short-lived tokens are
always fresh.

Usage (see README.md for the 10-line example)::

    push = ContactOpsPush(base_url="https://contacts.example.dev", token="svc-...")
    res = await push.push_contact(
        identity={
            "match_by": [{"kind": "email", "value": "REMOVED"}],
            "attributes": {"display_name": "Dana Lead",
                           "emails": [{"address": "REMOVED"}]},
        },
        source={"source_label": "NorthwindLeads", "source_kind": "leadgen_app",
                "external_ref": "LV-42", "consent_basis": "opted_in"},
        rollups={"lead_score": 87.5, "stage": "qualified"},
    )
    # res == {"contact_id": "…uuid…", "action": "created"}
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

__all__ = [
    "ContactOpsPush",
    "ContactOpsPushError",
    "ContactOpsPushClientError",
    "ContactOpsPushTransientError",
    # Back-compat alias for the original name (kept so an early adopter import
    # does not break); prefer the Error-suffixed name going forward.
    "ContactOpsPushTransient",
]

# A token source is either a static string, or a (possibly async) zero-arg callable
# returning the current bearer token. Modeled on the consumer SDK's token handling.
TokenSource = str | Callable[[], str | Awaitable[str]]

_INGEST_PATH = "/api/v1/contacts/ingest"
_BULK_PATH = "/api/v1/contacts/ingest/bulk"

# Statuses worth retrying: 429 (rate limit) + any 5xx (transient server-side).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class ContactOpsPushError(Exception):
    """Base error for the connector. Producers can catch this one type."""


class ContactOpsPushClientError(ContactOpsPushError):
    """A non-retryable failure (HTTP 4xx): bad payload, missing scope, etc.

    Carries the HTTP ``status_code`` and the server ``detail`` (when present) so the
    producer can log/branch. Retrying will not help — fix the request or the token.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"contact-ops ingest rejected (HTTP {status_code}): {detail}")
        self.status_code = status_code
        self.detail = detail


class ContactOpsPushTransientError(ContactOpsPushError):
    """A transient failure that survived all retries (timeout / conn / 5xx / 429).

    The push did not durably succeed; the producer should queue the lead and retry
    later. Never raised for a 4xx (those are :class:`ContactOpsPushClientError`).
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# Back-compat alias for the original (pre-rename) name.
ContactOpsPushTransient = ContactOpsPushTransientError


class ContactOpsPush:
    """Async client for the Contact-Ops REST ingest seam.

    Parameters
    ----------
    base_url:
        Contact-Ops base URL, e.g. ``https://contacts.magicunicorn.dev``.
    token:
        A static bearer token OR a (sync/async) callable returning the current
        token. Use the callable form for short-lived Brigade-STS / OBO tokens.
    timeout:
        Per-request timeout in seconds (default 10).
    max_retries:
        How many times to retry a transient failure before raising
        :class:`ContactOpsPushTransientError` (default 2 → up to 3 attempts total).
    backoff_base:
        Base seconds for exponential backoff between retries (default 0.5).
    http:
        Optional pre-built ``httpx.AsyncClient`` (e.g. for connection pooling or
        test injection). If omitted, the connector owns one; call :meth:`close`.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: TokenSource,
        timeout: float = 10.0,
        max_retries: int = 2,
        backoff_base: float = 0.5,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._max_retries = max(0, int(max_retries))
        self._backoff_base = max(0.0, float(backoff_base))
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        """Close the underlying httpx client if this connector owns it."""
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> ContactOpsPush:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def push_contact(
        self,
        identity: dict[str, Any],
        source: dict[str, Any],
        rollups: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Push ONE graduated lead. Returns ``{"contact_id": str, "action": str}``.

        ``identity`` is the contract's ``match_by`` + ``attributes`` (identity only:
        name / emails / phones / social / company). For convenience the caller may
        pass either the full ``{"match_by": [...], "attributes": {...}}`` shape, or
        just an ``attributes`` dict plus a top-level ``match_by`` — both are folded
        into the wire envelope. ``source`` MUST include ``source_label`` and
        ``consent_basis`` (the ingest door requires consent). ``rollups`` is
        optional (``lead_score`` / ``stage``).

        Idempotency is the caller's responsibility *by construction*: include a
        stable email / phone / external_ref so a repeat push updates the same
        contact instead of creating a duplicate.
        """
        envelope = _build_envelope(identity, source, rollups)
        result = await self._post(_INGEST_PATH, envelope)
        return {
            "contact_id": result.get("contact_id"),
            "action": result.get("action"),
        }

    async def push_contacts_bulk(
        self, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Push up to 50 leads in one call.

        Each item is ``{"identity": {...}, "source": {...}, "rollups": {...}?}`` and
        is folded into the same per-item envelope as :meth:`push_contact`. Returns
        the server's batch summary ``{created, updated, noop, errors, results}``.
        """
        envelope_items = [
            _build_envelope(
                item["identity"], item["source"], item.get("rollups")
            )
            for item in items
        ]
        return await self._post(_BULK_PATH, {"items": envelope_items})

    # ------------------------------------------------------------------
    # Transport (auth + retry + clean degradation)
    # ------------------------------------------------------------------

    async def _post(self, path: str, json_body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            token = await self._resolve_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            try:
                resp = await self._http.post(url, headers=headers, json=json_body)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # Transport-level failure → transient; retry with backoff.
                last_exc = exc
                if attempt < self._max_retries:
                    await self._sleep_backoff(attempt)
                    continue
                raise ContactOpsPushTransientError(
                    f"transport error after {attempt + 1} attempt(s): "
                    f"{type(exc).__name__}"
                ) from exc

            if resp.status_code in _RETRYABLE_STATUS:
                # Retryable server-side status → back off and retry; if it persists,
                # raise a typed transient error (never a bare httpx error).
                last_exc = ContactOpsPushTransientError(
                    f"server returned {resp.status_code}",
                    status_code=resp.status_code,
                )
                if attempt < self._max_retries:
                    await self._sleep_backoff(attempt)
                    continue
                raise ContactOpsPushTransientError(
                    f"server returned {resp.status_code} after "
                    f"{attempt + 1} attempt(s)",
                    status_code=resp.status_code,
                )

            if resp.status_code >= 400:
                # Non-retryable client error (4xx). Retrying won't help.
                raise ContactOpsPushClientError(
                    resp.status_code, _safe_detail(resp)
                )

            return _safe_json(resp)

        # Unreachable (loop either returns or raises), but keep the type checker
        # happy and fail closed if control ever falls through.
        raise ContactOpsPushTransientError(
            "exhausted retries", status_code=None
        ) from last_exc

    async def _resolve_token(self) -> str:
        src = self._token
        if callable(src):
            value = src()
            if inspect.isawaitable(value):
                value = await value
            return str(value)
        return str(src)

    async def _sleep_backoff(self, attempt: int) -> None:
        if self._backoff_base <= 0:
            return
        await asyncio.sleep(self._backoff_base * (2**attempt))


# ----------------------------------------------------------------------
# Envelope + response helpers (pure functions, easy to unit-test)
# ----------------------------------------------------------------------


def _build_envelope(
    identity: dict[str, Any],
    source: dict[str, Any],
    rollups: dict[str, Any] | None,
) -> dict[str, Any]:
    """Fold the caller's identity/source/rollups into the ingest wire envelope.

    Accepts identity as either the full ``{"match_by", "attributes"}`` shape or an
    ``attributes`` dict with a top-level ``match_by`` (so callers can be terse).
    Raises ``ValueError`` on a clearly malformed payload (missing match_by /
    attributes / source.source_label / source.consent_basis) BEFORE any network
    call, so a producer fails fast on a coding mistake rather than on a 422.
    """
    if "attributes" in identity:
        match_by = identity.get("match_by")
        attributes = identity.get("attributes")
    else:
        # Terse form: identity IS the attributes; match_by passed alongside.
        match_by = identity.get("match_by")
        attributes = {k: v for k, v in identity.items() if k != "match_by"}

    if not match_by:
        raise ValueError("identity must include a non-empty 'match_by'")
    if not attributes:
        raise ValueError("identity must include 'attributes'")
    if not source.get("source_label"):
        raise ValueError("source must include 'source_label'")
    if not source.get("consent_basis"):
        raise ValueError("source must include 'consent_basis' (required on ingest)")

    envelope: dict[str, Any] = {
        "match_by": match_by,
        "attributes": attributes,
        "source": source,
    }
    if rollups:
        envelope["rollups"] = rollups
    return envelope


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        payload = resp.json()
    except (ValueError, httpx.DecodingError) as exc:
        raise ContactOpsPushTransientError("non-JSON success response") from exc
    if not isinstance(payload, dict):
        raise ContactOpsPushTransientError("unexpected response shape (not an object)")
    return payload


def _safe_detail(resp: httpx.Response) -> str:
    """Best-effort extraction of the server's error detail without raising."""
    try:
        body = resp.json()
    except (ValueError, httpx.DecodingError):
        return resp.text[:500]
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)[:500]
