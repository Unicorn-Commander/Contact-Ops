# ruff: noqa: E402,S106
"""Tests for the shared `contactops_push` connector (P-00059).

The connector lives at repo-root `clients/contactops_push/` and is self-contained
(httpx + stdlib only, NO contact_ops import) so other apps can copy/submodule it.
These tests mock httpx with respx and assert:
  * it POSTs the correct ingest envelope to /api/v1/contacts/ingest with a Bearer;
  * it returns {contact_id, action} from the server response;
  * bulk POSTs to /ingest/bulk and returns the batch summary;
  * a callable / async token source is invoked per push;
  * it degrades CLEAN on a transient 5xx (retries, then a typed
    ContactOpsPushTransient — never a bare httpx error);
  * a 4xx raises a typed ContactOpsPushClientError immediately (no retry);
  * an obviously-malformed payload fails fast (ValueError) before any network I/O.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
import respx

# The connector is NOT a package on the import path; add its dir explicitly.
_CONNECTOR_DIR = (
    Path(__file__).resolve().parents[2] / "clients" / "contactops_push"
)
if str(_CONNECTOR_DIR) not in sys.path:
    sys.path.insert(0, str(_CONNECTOR_DIR))

import contactops_push as cp  # noqa: E402

BASE = "https://contacts.test.invalid"
INGEST_URL = f"{BASE}/api/v1/contacts/ingest"
BULK_URL = f"{BASE}/api/v1/contacts/ingest/bulk"

_IDENTITY = {
    "match_by": [{"kind": "email", "value": "REMOVED"}],
    "attributes": {
        "display_name": "Dana Lead",
        "emails": [{"address": "REMOVED"}],
    },
}
_SOURCE = {
    "source_label": "NorthwindLeads",
    "source_kind": "leadgen_app",
    "external_ref": "LV-42",
    "consent_basis": "opted_in",
}
_ROLLUPS = {"lead_score": 87.5, "stage": "qualified"}


@pytest.mark.asyncio
@respx.mock
async def test_push_contact_posts_envelope_and_returns_result():
    route = respx.post(INGEST_URL).mock(
        return_value=httpx.Response(
            200, json={"contact_id": "11111111-1111-1111-1111-111111111111",
                       "action": "created", "etag": "e1"}
        )
    )
    async with cp.ContactOpsPush(base_url=BASE, token="svc-token") as push:
        res = await push.push_contact(_IDENTITY, _SOURCE, _ROLLUPS)

    assert res == {
        "contact_id": "11111111-1111-1111-1111-111111111111",
        "action": "created",
    }
    assert route.called
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer svc-token"
    import json

    body = json.loads(sent.content)
    assert body["match_by"] == _IDENTITY["match_by"]
    assert body["attributes"] == _IDENTITY["attributes"]
    assert body["source"] == _SOURCE
    assert body["rollups"] == _ROLLUPS


@pytest.mark.asyncio
@respx.mock
async def test_push_contact_terse_identity_form():
    """Identity passed as attributes-with-top-level-match_by is folded correctly."""
    route = respx.post(INGEST_URL).mock(
        return_value=httpx.Response(200, json={"contact_id": "x", "action": "updated"})
    )
    terse_identity = {
        "match_by": [{"kind": "external_ref", "value": "LV-9"}],
        "display_name": "Terse",
    }
    async with cp.ContactOpsPush(base_url=BASE, token="t") as push:
        res = await push.push_contact(terse_identity, _SOURCE)
    assert res["action"] == "updated"
    import json

    body = json.loads(route.calls.last.request.content)
    assert body["attributes"] == {"display_name": "Terse"}
    assert "rollups" not in body  # omitted when not supplied


@pytest.mark.asyncio
@respx.mock
async def test_push_contacts_bulk_returns_summary():
    summary = {
        "created": 1, "updated": 1, "noop": 0, "errors": 0,
        "results": [
            {"index": 0, "contact_id": "a", "action": "created"},
            {"index": 1, "contact_id": "b", "action": "updated"},
        ],
    }
    route = respx.post(BULK_URL).mock(return_value=httpx.Response(200, json=summary))
    async with cp.ContactOpsPush(base_url=BASE, token="t") as push:
        res = await push.push_contacts_bulk(
            [
                {"identity": _IDENTITY, "source": _SOURCE, "rollups": _ROLLUPS},
                {"identity": _IDENTITY, "source": _SOURCE},
            ]
        )
    assert res == summary
    import json

    body = json.loads(route.calls.last.request.content)
    assert len(body["items"]) == 2


@pytest.mark.asyncio
@respx.mock
async def test_callable_token_source_invoked_per_push():
    calls: list[int] = []

    def token_source() -> str:
        calls.append(1)
        return f"tok-{len(calls)}"

    respx.post(INGEST_URL).mock(
        return_value=httpx.Response(200, json={"contact_id": "x", "action": "noop"})
    )
    async with cp.ContactOpsPush(base_url=BASE, token=token_source) as push:
        await push.push_contact(_IDENTITY, _SOURCE)
        await push.push_contact(_IDENTITY, _SOURCE)
    assert len(calls) == 2


@pytest.mark.asyncio
@respx.mock
async def test_async_callable_token_source():
    async def token_source() -> str:
        return "async-tok"

    route = respx.post(INGEST_URL).mock(
        return_value=httpx.Response(200, json={"contact_id": "x", "action": "created"})
    )
    async with cp.ContactOpsPush(base_url=BASE, token=token_source) as push:
        await push.push_contact(_IDENTITY, _SOURCE)
    assert route.calls.last.request.headers["authorization"] == "Bearer async-tok"


@pytest.mark.asyncio
@respx.mock
async def test_transient_5xx_retries_then_raises_typed_transient():
    route = respx.post(INGEST_URL).mock(return_value=httpx.Response(503))
    async with cp.ContactOpsPush(
        base_url=BASE, token="t", max_retries=2, backoff_base=0.0
    ) as push:
        with pytest.raises(cp.ContactOpsPushTransientError) as exc:
            await push.push_contact(_IDENTITY, _SOURCE)
    # up to 3 attempts (initial + 2 retries) — proves it retried, not raised once.
    assert route.call_count == 3
    assert exc.value.status_code == 503
    # Back-compat: the pre-rename alias resolves to the same class.
    assert cp.ContactOpsPushTransient is cp.ContactOpsPushTransientError


@pytest.mark.asyncio
@respx.mock
async def test_transient_then_success_recovers():
    """A 503 followed by a 200 succeeds within the retry budget."""
    route = respx.post(INGEST_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"contact_id": "ok", "action": "created"}),
        ]
    )
    async with cp.ContactOpsPush(
        base_url=BASE, token="t", max_retries=2, backoff_base=0.0
    ) as push:
        res = await push.push_contact(_IDENTITY, _SOURCE)
    assert res == {"contact_id": "ok", "action": "created"}
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_timeout_raises_typed_transient_not_bare_httpx():
    route = respx.post(INGEST_URL).mock(
        side_effect=httpx.ConnectTimeout("boom")
    )
    async with cp.ContactOpsPush(
        base_url=BASE, token="t", max_retries=1, backoff_base=0.0
    ) as push:
        with pytest.raises(cp.ContactOpsPushTransientError):
            await push.push_contact(_IDENTITY, _SOURCE)
    assert route.call_count == 2  # initial + 1 retry


@pytest.mark.asyncio
@respx.mock
async def test_4xx_raises_client_error_without_retry():
    route = respx.post(INGEST_URL).mock(
        return_value=httpx.Response(
            403, json={"detail": "INSUFFICIENT_SCOPE: requires contacts:write"}
        )
    )
    async with cp.ContactOpsPush(
        base_url=BASE, token="t", max_retries=3, backoff_base=0.0
    ) as push:
        with pytest.raises(cp.ContactOpsPushClientError) as exc:
            await push.push_contact(_IDENTITY, _SOURCE)
    assert route.call_count == 1  # NOT retried
    assert exc.value.status_code == 403
    assert "INSUFFICIENT_SCOPE" in exc.value.detail


@pytest.mark.asyncio
async def test_malformed_payload_fails_fast_before_network():
    """Missing consent / match_by raises ValueError BEFORE any HTTP call (so the
    producer catches a coding mistake locally, not as a 422)."""
    async with cp.ContactOpsPush(base_url=BASE, token="t") as push:
        with pytest.raises(ValueError):
            await push.push_contact(_IDENTITY, {"source_label": "L"})  # no consent
        with pytest.raises(ValueError):
            await push.push_contact(
                {"attributes": {"display_name": "X"}},  # no match_by
                {"source_label": "L", "consent_basis": "opted_in"},
            )
