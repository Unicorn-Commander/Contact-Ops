"""Backend tests for the Ops-Center billing proxy + the paid-tier gate.

Covers the ``feat/billing-ops-center`` build:

  * :mod:`contact_ops.api.billing` — the thin proxy to the central Ops-Center
    billing brain (``GET /status``, ``GET /plans``, ``POST /checkout`` under the
    ``/api/v1/billing`` prefix) and its shared core
    :func:`~contact_ops.api.billing.fetch_subscription_status`.
  * :mod:`contact_ops.services.tier` — the cached tier read used by the gate.
  * :mod:`contact_ops.api.deps` — the ``require_pro`` gate, flag-gated behind
    ``BILLING_TIER_ENFORCED`` and mounted on ``POST /chat``.

Patterns reused from the existing suite (see ``test_chat.py`` /
``test_jwt_validation.py``):

  * ``TestClient(app)`` **without** a ``with`` block, so Starlette does NOT run
    the lifespan (no DB/boot-self-test/Brigade-registration). The app is
    importable and the proxy endpoints touch no DB.
  * Auth is provided by flipping ``STANDALONE_MODE`` on the cached ``Settings``
    singleton, exactly like ``test_chat.py``. The JWT middleware then injects a
    fixed claim set (``uc_uid="standalone-user"``,
    ``email="REMOVED"``, a valid ``tenant_id``), so the
    proxy's ``_identity()`` resolves without minting tokens.
  * ``respx`` mocks the outbound ``httpx`` calls to Ops-Center. respx's default
    mode raises on any un-mocked request, which we exploit to *prove* the
    unconfigured path makes no network call.

The four behaviors the build must guarantee (and that these tests pin):

  1. ``GET /status`` is INERT when Ops-Center is unconfigured —
     ``{"tier":"free","configured":false}`` with **no** network call — and
     proxies + normalizes correctly when configured.
  2. ``POST /checkout`` returns 503 when unconfigured; returns the URL when
     configured.
  3. The tier gate is a strict NO-OP when ``BILLING_TIER_ENFORCED`` is False
     (``/chat`` works for everyone); when True a free user gets 402 and a Pro
     user is allowed.
  4. The Ops-Center service key never appears in any response or error body,
     even when an upstream maliciously echoes it back.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Generator, Sequence
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from contact_ops.api import billing as billing_api
from contact_ops.api import chat as chat_api
from contact_ops.api import deps as deps_api
from contact_ops.core.config import get_settings
from contact_ops.main import app
from contact_ops.services import tier as tier_svc

# Ops-Center config used across the configured-path tests. The proxy builds an
# ``httpx.AsyncClient(base_url=OPS_CENTER_URL)``; respx matches on the fully
# resolved URL, so routes are registered against this base + the upstream path.
OPS_CENTER_URL = "http://ops-center-direct:8084"
# A distinctive sentinel so the "key never leaks" tests can assert its absence
# unambiguously anywhere in a serialized response/error.
SERVICE_KEY = "sk-opscenter-SECRET-DO-NOT-LEAK-deadbeef"

SUBSCRIPTIONS_CURRENT = f"{OPS_CENTER_URL}/api/v1/subscriptions/current"
SUBSCRIPTIONS_PLANS = f"{OPS_CENTER_URL}/api/v1/subscriptions/plans"
BILLING_CHECKOUT = f"{OPS_CENTER_URL}/api/v1/billing/checkout"


@pytest.fixture(autouse=True)
def _billing_settings() -> Generator[Any]:
    """Put the cached Settings singleton in a known billing test state.

    Defaults for every test: STANDALONE_MODE on (so the JWT middleware injects
    a valid identity), Ops-Center UNconfigured, and tier enforcement OFF. Each
    test opts into a configured/enforced state by mutating the yielded settings.
    The tier cache is cleared before and after so cross-test bleed can't mask a
    real read.
    """
    settings = get_settings()
    saved = {
        "STANDALONE_MODE": settings.STANDALONE_MODE,
        "OPS_CENTER_URL": settings.OPS_CENTER_URL,
        "OPS_CENTER_SERVICE_KEY": settings.OPS_CENTER_SERVICE_KEY,
        "OPS_CENTER_TIMEOUT": settings.OPS_CENTER_TIMEOUT,
        "BILLING_TIER_ENFORCED": settings.BILLING_TIER_ENFORCED,
    }
    settings.STANDALONE_MODE = True
    settings.OPS_CENTER_URL = ""
    settings.OPS_CENTER_SERVICE_KEY = ""
    settings.OPS_CENTER_TIMEOUT = 5.0
    settings.BILLING_TIER_ENFORCED = False
    tier_svc.reset_tier_cache()
    try:
        yield settings
    finally:
        for key, value in saved.items():
            setattr(settings, key, value)
        tier_svc.reset_tier_cache()


def _configure_ops_center(settings: Any) -> None:
    """Flip the proxy from inert -> configured (both URL and key set)."""
    settings.OPS_CENTER_URL = OPS_CENTER_URL
    settings.OPS_CENTER_SERVICE_KEY = SERVICE_KEY


# ── (1) GET /status: inert when unconfigured, proxies when configured ────────


def test_status_inert_when_unconfigured() -> None:
    """Unconfigured Ops-Center -> {tier:'free', configured:false}, HTTP 200."""
    resp = TestClient(app).get("/api/v1/billing/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "free"
    assert body["configured"] is False


def test_status_makes_no_network_call_when_unconfigured(_billing_settings: Any) -> None:
    """The inert /status path must not touch the network at all.

    Two independent proofs:
      * the whole request runs inside ``respx.mock()`` with **zero** routes
        registered — respx raises on any outbound httpx request, so a network
        attempt would fail the test; and
      * ``httpx.AsyncClient`` is monkeypatched to a tripwire that raises if it
        is ever constructed.
    """
    assert _billing_settings.OPS_CENTER_URL == ""

    constructed = {"count": 0}

    class _TripwireAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            constructed["count"] += 1
            raise AssertionError("httpx.AsyncClient must not be constructed when unconfigured")

    with respx.mock(assert_all_called=False):  # zero routes => any call raises
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(billing_api.httpx, "AsyncClient", _TripwireAsyncClient)
            resp = TestClient(app).get("/api/v1/billing/status")

    assert resp.status_code == 200
    assert resp.json()["configured"] is False
    assert constructed["count"] == 0


def test_status_proxies_and_normalizes_when_configured(_billing_settings: Any) -> None:
    """Configured -> proxies GET /subscriptions/current and normalizes the body.

    Also asserts the outbound request carried the service key as a Bearer token
    plus the X-User-ID / X-User-Email identity headers the contract requires.
    """
    _configure_ops_center(_billing_settings)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(SUBSCRIPTIONS_CURRENT).respond(
            json={
                "tier": "pro",
                "status": "active",
                "plan_name": "Contact-Ops Pro",
                "customer_portal_url": "https://billing.example/portal/abc",
            }
        )
        resp = TestClient(app).get("/api/v1/billing/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "tier": "pro",
        "status": "active",
        "plan": "Contact-Ops Pro",
        "configured": True,
        "portal_url": "https://billing.example/portal/abc",
    }

    sent = route.calls.last.request
    assert sent.headers["authorization"] == f"Bearer {SERVICE_KEY}"
    assert sent.headers["x-user-id"] == "standalone-user"
    assert sent.headers["x-user-email"] == "REMOVED"


def test_status_normalizes_alias_fields_when_configured(_billing_settings: Any) -> None:
    """The Lago-shaped aliases (tier_code / tier_name / portal_url) normalize too."""
    _configure_ops_center(_billing_settings)

    with respx.mock(assert_all_called=True) as mock:
        mock.get(SUBSCRIPTIONS_CURRENT).respond(
            json={
                "tier_code": "suite",
                "status": "trialing",
                "tier_name": "Suite",
                "portal_url": "https://billing.example/p/xyz",
            }
        )
        resp = TestClient(app).get("/api/v1/billing/status")

    body = resp.json()
    assert body["tier"] == "suite"
    assert body["status"] == "trialing"
    assert body["plan"] == "Suite"
    assert body["portal_url"] == "https://billing.example/p/xyz"
    assert body["configured"] is True


def test_status_degrades_to_safe_shape_on_upstream_5xx(_billing_settings: Any) -> None:
    """A read must never 5xx the billing UI; a non-200 degrades to configured:false."""
    _configure_ops_center(_billing_settings)

    with respx.mock(assert_all_called=True) as mock:
        mock.get(SUBSCRIPTIONS_CURRENT).respond(status_code=500, json={"error": "boom"})
        resp = TestClient(app).get("/api/v1/billing/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "free"
    assert body["configured"] is False


def test_plans_inert_when_unconfigured() -> None:
    """Unconfigured -> {plans:[], configured:false} with no network call."""
    with respx.mock(assert_all_called=False):  # zero routes => any call raises
        resp = TestClient(app).get("/api/v1/billing/plans")
    assert resp.status_code == 200
    assert resp.json() == {"plans": [], "configured": False}


def test_plans_proxies_body_verbatim_when_configured(_billing_settings: Any) -> None:
    """Configured -> returns Ops-Center's plan list verbatim."""
    _configure_ops_center(_billing_settings)
    upstream = {"plans": [{"code": "pro", "price": 2000}, {"code": "suite", "price": 3500}]}

    with respx.mock(assert_all_called=True) as mock:
        mock.get(SUBSCRIPTIONS_PLANS).respond(json=upstream)
        resp = TestClient(app).get("/api/v1/billing/plans")

    assert resp.status_code == 200
    assert resp.json() == upstream


# ── (2) POST /checkout: 503 when unconfigured, URL when configured ───────────


def test_checkout_503_when_unconfigured(_billing_settings: Any) -> None:
    """A write fails loudly when Ops-Center is unconfigured: 503, no network."""
    assert _billing_settings.OPS_CENTER_URL == ""
    with respx.mock(assert_all_called=False):  # zero routes => any call raises
        resp = TestClient(app).post(
            "/api/v1/billing/checkout", json={"plan_code": "pro"}
        )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "billing not configured"


def test_checkout_returns_url_when_configured(_billing_settings: Any) -> None:
    """Configured -> proxies POST /billing/checkout and returns the checkout URL.

    Also asserts the proxied request body carried the acting user_id + plan_code
    (+ email) per the Ops-Center checkout contract.
    """
    _configure_ops_center(_billing_settings)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(BILLING_CHECKOUT).respond(
            status_code=201,
            json={
                "checkout_url": "https://checkout.stripe.com/c/pay/cs_test_123",
                "session_id": "cs_test_123",
            },
        )
        resp = TestClient(app).post(
            "/api/v1/billing/checkout",
            json={
                "plan_code": "pro",
                "success_url": "https://contacts.example/ok",
                "cancel_url": "https://contacts.example/no",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["checkout_url"] == "https://checkout.stripe.com/c/pay/cs_test_123"
    assert body["session_id"] == "cs_test_123"

    sent_body = json.loads(route.calls.last.request.content)
    assert sent_body["user_id"] == "standalone-user"
    assert sent_body["plan_code"] == "pro"
    assert sent_body["email"] == "REMOVED"
    assert sent_body["success_url"] == "https://contacts.example/ok"
    assert sent_body["cancel_url"] == "https://contacts.example/no"


def test_checkout_accepts_url_alias_when_configured(_billing_settings: Any) -> None:
    """Ops-Center may return the URL under ``url`` instead of ``checkout_url``."""
    _configure_ops_center(_billing_settings)

    with respx.mock(assert_all_called=True) as mock:
        mock.post(BILLING_CHECKOUT).respond(
            status_code=200, json={"url": "https://checkout.stripe.com/c/pay/cs_test_999"}
        )
        resp = TestClient(app).post(
            "/api/v1/billing/checkout", json={"plan_code": "suite"}
        )

    assert resp.status_code == 200
    assert resp.json()["checkout_url"] == "https://checkout.stripe.com/c/pay/cs_test_999"


# ── (3) Tier gate on /chat: NO-OP when off, 402/allow when on ────────────────


class _StubLLMClient:
    """One-shot streaming LLM: emits a single delta then stops (no tool loop)."""

    async def stream_chat_completion(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        yield {"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}


class _StubToolExecutor:
    def definitions(self) -> list[dict[str, Any]]:
        return []

    async def execute(self, name: str, args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        raise AssertionError("no tool call expected in the gate tests")


def _install_stub_orchestrator(mp: pytest.MonkeyPatch) -> None:
    """Replace the chat orchestrator so /chat streams without real LLM/tools."""
    from contact_ops.services.chat_orchestrator import ChatOrchestrator

    orchestrator = ChatOrchestrator(
        llm_client=_StubLLMClient(),
        tool_executor=_StubToolExecutor(),
    )
    mp.setattr(chat_api, "ChatOrchestrator", lambda: orchestrator)


def _post_chat() -> httpx.Response:
    return TestClient(app).post(
        "/chat", json={"messages": [{"role": "user", "content": "hello"}]}
    )


def test_chat_not_gated_when_enforcement_off(_billing_settings: Any) -> None:
    """Flag off (default) -> /chat works for everyone and the tier is NEVER read.

    The gate must be a strict NO-OP when ``BILLING_TIER_ENFORCED`` is False: no
    Ops-Center call, no tier read, byte-for-byte the pre-gate behavior. We prove
    "no read" by tripwiring ``get_user_tier`` and asserting it never fires.
    """
    assert _billing_settings.BILLING_TIER_ENFORCED is False

    def _tripwire(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("tier must not be read when enforcement is off")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(deps_api, "get_user_tier", _tripwire)
        _install_stub_orchestrator(mp)
        with respx.mock(assert_all_called=False):  # zero routes => any call raises
            resp = _post_chat()

    assert resp.status_code == 200
    assert "hi" in resp.text


def test_chat_free_user_402_when_enforced(_billing_settings: Any) -> None:
    """Flag on + sub-Pro tier -> /chat is 402 with the upgrade hint."""
    _billing_settings.BILLING_TIER_ENFORCED = True

    async def _free_tier(*args: Any, **kwargs: Any) -> str:
        return "free"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(deps_api, "get_user_tier", _free_tier)
        _install_stub_orchestrator(mp)
        resp = _post_chat()

    assert resp.status_code == 402
    detail = resp.json()["detail"]
    assert detail["error"] == "tier_required"
    assert detail["required_tier"] == "pro"
    assert detail["current_tier"] == "free"
    assert "upgrade" in detail


def test_chat_pro_user_allowed_when_enforced(_billing_settings: Any) -> None:
    """Flag on + Pro tier -> /chat passes the gate and streams normally."""
    _billing_settings.BILLING_TIER_ENFORCED = True

    async def _pro_tier(*args: Any, **kwargs: Any) -> str:
        return "pro"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(deps_api, "get_user_tier", _pro_tier)
        _install_stub_orchestrator(mp)
        resp = _post_chat()

    assert resp.status_code == 200
    assert "hi" in resp.text


def test_chat_higher_tier_allowed_when_enforced(_billing_settings: Any) -> None:
    """A tier above Pro (e.g. enterprise) also passes the Pro gate (ladder)."""
    _billing_settings.BILLING_TIER_ENFORCED = True

    async def _enterprise_tier(*args: Any, **kwargs: Any) -> str:
        return "enterprise"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(deps_api, "get_user_tier", _enterprise_tier)
        _install_stub_orchestrator(mp)
        resp = _post_chat()

    assert resp.status_code == 200


def test_gate_reads_tier_through_ops_center_when_enforced(_billing_settings: Any) -> None:
    """End-to-end gate: real ``get_user_tier`` reading a respx-mocked Ops-Center.

    Rather than patching the tier read, this drives the *actual* integration —
    the gate -> ``tier.get_user_tier`` -> ``billing.fetch_subscription_status``
    -> Ops-Center ``/subscriptions/current`` — to prove a Pro subscription from
    Ops-Center unlocks the gated endpoint when enforcement is on.
    """
    _billing_settings.BILLING_TIER_ENFORCED = True
    _configure_ops_center(_billing_settings)

    with pytest.MonkeyPatch.context() as mp:
        _install_stub_orchestrator(mp)
        with respx.mock(assert_all_called=True) as mock:
            mock.get(SUBSCRIPTIONS_CURRENT).respond(json={"tier": "pro", "status": "active"})
            resp = _post_chat()

    assert resp.status_code == 200
    assert "hi" in resp.text


def test_gate_402_when_ops_center_reports_free(_billing_settings: Any) -> None:
    """End-to-end gate: Ops-Center reporting a free tier -> 402 (fail-closed)."""
    _billing_settings.BILLING_TIER_ENFORCED = True
    _configure_ops_center(_billing_settings)

    with pytest.MonkeyPatch.context() as mp:
        _install_stub_orchestrator(mp)
        with respx.mock(assert_all_called=True) as mock:
            mock.get(SUBSCRIPTIONS_CURRENT).respond(json={"tier": "free"})
            resp = _post_chat()

    assert resp.status_code == 402
    assert resp.json()["detail"]["required_tier"] == "pro"


# ── (4) The service key must never appear in any response/error body ─────────


def _assert_key_absent(resp: httpx.Response) -> None:
    """The raw response body must not contain the Ops-Center service key."""
    assert SERVICE_KEY not in resp.text


def test_service_key_absent_from_status_when_upstream_echoes_it(_billing_settings: Any) -> None:
    """A malicious/buggy upstream that echoes the key must not leak via /status.

    Ops-Center returns a non-200 whose body literally contains the service key;
    the proxy must degrade to the safe shape and never surface that body.
    """
    _configure_ops_center(_billing_settings)

    with respx.mock(assert_all_called=True) as mock:
        mock.get(SUBSCRIPTIONS_CURRENT).respond(
            status_code=401,
            json={"detail": f"bad service key: {SERVICE_KEY}", "echo": SERVICE_KEY},
        )
        resp = TestClient(app).get("/api/v1/billing/status")

    assert resp.status_code == 200
    assert resp.json()["configured"] is False
    _assert_key_absent(resp)


def test_service_key_absent_from_plans_error(_billing_settings: Any) -> None:
    """A /plans upstream error collapses to a generic 502 that omits the key."""
    _configure_ops_center(_billing_settings)

    with respx.mock(assert_all_called=True) as mock:
        mock.get(SUBSCRIPTIONS_PLANS).respond(
            status_code=500, json={"trace": f"key={SERVICE_KEY}"}
        )
        resp = TestClient(app).get("/api/v1/billing/plans")

    assert resp.status_code == 502
    assert resp.json()["detail"] == "billing upstream unavailable"
    _assert_key_absent(resp)


def test_service_key_absent_from_checkout_error(_billing_settings: Any) -> None:
    """A /checkout upstream error collapses to a generic 502 that omits the key."""
    _configure_ops_center(_billing_settings)

    with respx.mock(assert_all_called=True) as mock:
        mock.post(BILLING_CHECKOUT).respond(
            status_code=500, json={"detail": f"stripe failure with {SERVICE_KEY}"}
        )
        resp = TestClient(app).post(
            "/api/v1/billing/checkout", json={"plan_code": "pro"}
        )

    assert resp.status_code == 502
    assert resp.json()["detail"] == "billing upstream unavailable"
    _assert_key_absent(resp)


def test_service_key_absent_on_transport_error(_billing_settings: Any) -> None:
    """A transport failure (no usable upstream body) still never surfaces the key."""
    _configure_ops_center(_billing_settings)

    with respx.mock(assert_all_called=True) as mock:
        mock.post(BILLING_CHECKOUT).mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        resp = TestClient(app).post(
            "/api/v1/billing/checkout", json={"plan_code": "pro"}
        )

    assert resp.status_code == 502
    assert resp.json()["detail"] == "billing upstream unavailable"
    _assert_key_absent(resp)
