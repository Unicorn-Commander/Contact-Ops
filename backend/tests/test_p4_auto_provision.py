"""Phase 4.4 auto-provision onboarding suite (migration 0038 + POST /api/auth/onboard
+ the JWT-middleware tenant-less allowance).

Three layers, mirroring the three things the build added:

  (1) DB function ``provision_personal_workspace(uc_uid, username, email)``
      (migration 0038, SECURITY DEFINER). Exercised AS the ``contact_ops_runtime``
      role (a ``contact_ops_app`` member, NOSUPERUSER NOBYPASSRLS) with NO tenant
      bound — the exact first-login posture the function exists for. Proves it
      creates a tenant + an admin/active/default membership with valid NOT-NULL
      columns even though both target tables are FORCE RLS with
      ``= current_tenant_id()`` WITH CHECKs (DEFINER bypass), and that a SECOND
      call for the same uc_uid is IDEMPOTENT (same tenant, no duplicate
      membership / no second tenant). Follows the sync ``rt_engine`` DB-test
      pattern from test_p4_membership_gate.py / test_p4_isolation_rls.py.

  (2) Middleware allowance NARROWNESS, end-to-end through the real ASGI stack:
      a valid RS256 Keycloak token that LACKS tenant_id is accepted on
      ``POST /api/auth/onboard`` (it reaches the handler — NOT a 401) but is
      REJECTED (401) on another path (``/mcp``) with the SAME token. Uses the
      offline JWKS-cache-preseed trick from test_jwt_validation.py (respx may be
      absent, so we never rely on it) by seeding ``_jwks`` on the live
      middleware instance mounted in ``app``.

  (3) Endpoint SEMANTICS (handler invoked directly with a synthetic Request so
      claims are fully controlled, the existing-repo monkeypatch-the-session
      idiom from test_notes.py / test_import_vcard.py):
        * a non-Keycloak issuer is rejected (400) — onboarding is KC-only;
        * ``uc_uid`` comes from the TOKEN, not the request body: a caller cannot
          provision for a different uc_uid (the handler ignores the body
          entirely; the provisioned workspace is owned by the token ``sub``);
        * an already-member call returns ``provisioned: false`` with the EXISTING
          home tenant (idempotent replay; no second workspace, no KC write).

DB tests run against the session-scoped testcontainer Postgres (conftest
``postgres_container`` / ``db_session``) with all Alembic migrations applied.
"""
from __future__ import annotations

import base64
import json as _json
import time
import types
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from contact_ops.core.config import get_settings
from contact_ops.main import app

# ── shared identity constants ──────────────────────────────────────────────
# owner_user_id on tenants is NOT NULL + uuid, and provision_personal_workspace
# safe-casts uc_uid->uuid for it, so the principal MUST be a uuid string to own a
# workspace (the function's documented contract). Use stable, valid uuids.
UC_A = "11111111-1111-1111-1111-111111111111"
UC_B = "22222222-2222-2222-2222-222222222222"
# A DISTINCT principal for the one end-to-end test that COMMITS to the shared
# container (the others run in rolled-back transactions). Keeping its identity
# separate means its committed workspace can never pollute the UC_A/UC_B-keyed
# rolled-back tests regardless of test ordering.
UC_E2E = "33333333-3333-3333-3333-333333333333"


# ===========================================================================
# (1) provision_personal_workspace — DB function (sync rt_engine, RLS-subject)
# ===========================================================================


@pytest.fixture(scope="module")
def rt_engine(postgres_container: str):
    """Sync engine; ensures the contact_ops_runtime login role exists once.

    Identical bootstrap to test_p4_membership_gate.py / test_p4_isolation_rls.py:
    a NOSUPERUSER NOBYPASSRLS member of contact_ops_app, so the DEFINER bypass is
    proven from a role that RLS actually constrains.
    """
    sync_url = postgres_container.replace("postgresql+asyncpg://", "postgresql://")
    eng = create_engine(sync_url, future=True)
    with eng.begin() as c:
        c.execute(text(
            "DO $$ BEGIN CREATE ROLE contact_ops_runtime LOGIN PASSWORD 'rtpw' "
            "NOSUPERUSER NOBYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        ))
        c.execute(text("GRANT contact_ops_app TO contact_ops_runtime"))
    yield eng
    eng.dispose()


def _provision(c, uc_uid: str, username: str = "", email: str = "") -> str:
    return c.execute(
        text("SELECT provision_personal_workspace(:u, :n, :e)"),
        {"u": uc_uid, "n": username, "e": email},
    ).scalar()


def test_provision_creates_tenant_and_admin_membership(rt_engine):
    """A first-login (tenant-less) runtime session provisions exactly one
    personal workspace + a NOT-NULL-valid admin/active/default self-membership."""
    with rt_engine.connect() as c:
        trans = c.begin()
        try:
            # First-login posture: RLS-subject role, NO tenant bound (the GUC is
            # unset, so current_tenant_id() is NULL — a plain INSERT would be
            # WITH-CHECK-rejected; only the DEFINER function can write here).
            c.execute(text("SET LOCAL ROLE contact_ops_runtime"))

            tenant_id = _provision(c, UC_A, username="Aaron Stransky", email="REMOVED")
            assert tenant_id, "function must return the new tenant id"

            # --- the tenant row (read back as the function owner via a fresh
            # superuser cursor would need RESET; instead inspect within the same
            # tx but the runtime role cannot SELECT it (no tenant bound). Reset to
            # the connection's privileged role to verify the committed-shape row.
            c.execute(text("RESET ROLE"))
            trow = c.execute(
                text(
                    "SELECT slug, kind::text, display_name, owner_user_id, "
                    "isolation_mode, hipaa_mode, qdrant_namespace, "
                    "garage_bucket_prefix, graph_name, deprecated_at "
                    "FROM tenants WHERE id = CAST(:t AS uuid)"
                ),
                {"t": tenant_id},
            ).mappings().one()

            # NARROW shape the migration promises: personal / standard / non-HIPAA,
            # owned by the calling principal, with all NOT-NULL infra columns set.
            assert trow["kind"] == "personal"
            assert trow["isolation_mode"] == "standard"
            assert trow["hipaa_mode"] is False
            assert str(trow["owner_user_id"]) == UC_A
            assert trow["display_name"] == "Aaron Stransky"
            assert trow["deprecated_at"] is None
            # NOT-NULL infra columns are populated (DB would have rejected NULLs,
            # but assert the derived shapes are coherent + slug-constraint-legal).
            assert trow["qdrant_namespace"], "qdrant_namespace NOT NULL must be set"
            assert trow["garage_bucket_prefix"], "garage_bucket_prefix NOT NULL must be set"
            assert trow["graph_name"], "graph_name must be derived"
            slug = trow["slug"]
            # slug ~ '^[a-z0-9][a-z0-9-]{1,62}$' (tenants_slug_check) + the doc's
            # '<base>-<6 hex>' shape; <= 63 chars; no trailing '-'.
            assert 2 <= len(slug) <= 63
            assert slug[0].isalnum() and not slug.endswith("-")
            assert all(ch.islower() or ch.isdigit() or ch == "-" for ch in slug)
            assert slug.split("-")[-1] and len(slug.split("-")[-1]) == 6
            # graph_name folds '-'→'_' off the same slug (legible coupling).
            assert trow["graph_name"] == "contact_ops__" + slug.replace("-", "_")

            # --- the admin self-membership the §4.4 gate enforces ---
            mrow = c.execute(
                text(
                    "SELECT user_uc_uid, role, status, is_default, "
                    "consent_basis::text, added_by, added_at "
                    "FROM user_tenant_membership WHERE tenant_id = CAST(:t AS uuid)"
                ),
                {"t": tenant_id},
            ).mappings().all()
            assert len(mrow) == 1, "exactly one membership row created"
            m = mrow[0]
            assert m["user_uc_uid"] == UC_A
            assert m["role"] == "admin"
            assert m["status"] == "active"
            assert m["is_default"] is True
            assert m["consent_basis"] == "self_provided"
            assert m["added_by"] == "onboarding:auto-provision"
            assert m["added_at"] is not None  # NOT NULL DEFAULT now()
        finally:
            trans.rollback()


def test_provision_is_idempotent_same_uc_uid(rt_engine):
    """A SECOND provision for the same uc_uid returns the SAME tenant and creates
    no duplicate membership and no second tenant (idempotent fast path)."""
    with rt_engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("SET LOCAL ROLE contact_ops_runtime"))
            first = _provision(c, UC_A, username="aaron", email="REMOVED")
            # Different username/email on replay must NOT spawn a second workspace.
            second = _provision(c, UC_A, username="aaron-renamed", email="REMOVED")
            assert second == first, "idempotent: same uc_uid -> same tenant"

            c.execute(text("RESET ROLE"))
            tenant_count = c.execute(
                text("SELECT count(*) FROM tenants WHERE owner_user_id = CAST(:u AS uuid)"),
                {"u": UC_A},
            ).scalar()
            assert tenant_count == 1, "no second tenant for a repeat first-login"

            mem_count = c.execute(
                text("SELECT count(*) FROM user_tenant_membership WHERE user_uc_uid = :u"),
                {"u": UC_A},
            ).scalar()
            assert mem_count == 1, "no duplicate membership on idempotent replay"

            # The single tenant still carries the ORIGINAL display name (the
            # replay took the fast path and wrote nothing).
            disp = c.execute(
                text("SELECT display_name FROM tenants WHERE id = CAST(:t AS uuid)"),
                {"t": first},
            ).scalar()
            assert disp == "aaron"
        finally:
            trans.rollback()


def test_provision_distinct_uc_uids_get_distinct_workspaces(rt_engine):
    """Two different principals each get their own personal workspace (the
    idempotency is keyed on uc_uid, not global)."""
    with rt_engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("SET LOCAL ROLE contact_ops_runtime"))
            t_a = _provision(c, UC_A, username="aaron")
            t_b = _provision(c, UC_B, username="aaron")  # same username, diff uid
            assert t_a != t_b, "distinct uc_uids must not collapse to one tenant"

            c.execute(text("RESET ROLE"))
            slugs = c.execute(
                text(
                    "SELECT slug FROM tenants WHERE id = ANY(ARRAY[:a, :b]::uuid[])"
                ),
                {"a": t_a, "b": t_b},
            ).scalars().all()
            # Even with an identical username base, the random 6-hex suffix keeps
            # the UNIQUE slug constraint satisfied.
            assert len(set(slugs)) == 2, "slugs must be unique across workspaces"
        finally:
            trans.rollback()


def test_provision_function_is_security_definer(rt_engine):
    """Guard the DEFINER posture the whole design relies on (a future ALTER to
    INVOKER would silently re-RLS-block first-login)."""
    with rt_engine.connect() as c:
        prosecdef = c.execute(text(
            "SELECT prosecdef FROM pg_proc WHERE proname = 'provision_personal_workspace'"
        )).scalar()
        assert prosecdef is True, "provision_personal_workspace must be SECURITY DEFINER"


# ===========================================================================
# (2) + (3) shared HTTP/RS256 helpers (offline JWKS-cache-preseed; no respx)
# ===========================================================================

VALID_ISSUER = "https://test-keycloak.invalid/realms/uchub"
VALID_JWKS_URL = "https://test-keycloak.invalid/protocol/openid-connect/certs"
VALID_AUD = "contact-ops-mcp"


def _int_to_b64url(num: int) -> str:
    length = (num.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(num.to_bytes(length, "big")).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def rsa_keypair():
    """RS256 keypair + its JWK (kid=test-key-1), generated once for this module."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA", "kid": "test-key-1", "use": "sig", "alg": "RS256",
        "n": _int_to_b64url(pub.n), "e": _int_to_b64url(pub.e),
    }
    return {"private_pem": private_pem, "jwk": jwk}


def _jwks(jwk: dict) -> dict:
    return {"keys": [jwk]}


def _make_token(claims: dict, key_pem: str, headers: dict | None = None) -> str:
    from jose import jwt as jose_jwt
    return jose_jwt.encode(
        claims, key_pem, algorithm="RS256", headers=headers or {"kid": "test-key-1"}
    )


def _kc_claims(*, sub: str, iss: str = VALID_ISSUER, aud: str = VALID_AUD,
               tenant_id: str | None = None, username: str = "newuser",
               email: str = "REMOVED") -> dict:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": sub, "iss": iss, "aud": aud,
        "preferred_username": username, "email": email,
        "iat": now - 60, "exp": now + 300, "nbf": now - 60,
    }
    if tenant_id is not None:
        claims["tenant_id"] = tenant_id
    return claims


@pytest.fixture
def configure_kc():
    """Put the app's JWT settings into the known RS256 test state for the
    duration of a test, then restore them (so settings never leak across tests).
    The middleware's offline JWKS resolution is wired separately via
    ``_offline_jwks`` (a class-level monkeypatch of ``_refresh_jwks``)."""
    s = get_settings()
    saved = (s.STANDALONE_MODE, s.KEYCLOAK_ISSUER, s.KEYCLOAK_JWKS_URL,
             s.KEYCLOAK_AUDIENCE, list(s.JWT_ALLOWED_ALGORITHMS))
    s.STANDALONE_MODE = False
    s.KEYCLOAK_ISSUER = VALID_ISSUER
    s.KEYCLOAK_JWKS_URL = VALID_JWKS_URL
    s.KEYCLOAK_AUDIENCE = VALID_AUD
    s.JWT_ALLOWED_ALGORITHMS = ["RS256"]
    yield s
    (s.STANDALONE_MODE, s.KEYCLOAK_ISSUER, s.KEYCLOAK_JWKS_URL,
     s.KEYCLOAK_AUDIENCE, s.JWT_ALLOWED_ALGORITHMS) = saved


def _offline_jwks(jwk: dict, monkeypatch) -> None:
    """Make the LIVE middleware resolve the test key OFFLINE (no network, no
    respx) regardless of which built instance Starlette uses for the request.

    Starlette 1.x wraps BaseHTTPMiddleware so reaching the built instance to
    seed ``_jwks`` directly is fragile (the instance is not exposed on
    ``app.user_middleware``, which holds Middleware *wrappers*). Instead we
    class-level-patch ``_refresh_jwks`` so that whichever instance handles the
    request loads the test JWK into its own cache on first ``_resolve_key`` —
    the same offline-verification posture as test_jwt_validation.py's
    ``_mw_offline`` cache-preseed, but instance-agnostic. Signature/issuer/
    audience/exp verification is otherwise completely unchanged."""
    from contact_ops.middleware.jwt_validation import JWTValidationMiddleware

    async def _fake_refresh(self):  # type: ignore[no-untyped-def]
        self._jwks = _jwks(jwk)
        self._jwks_expires = time.time() + 3600

    monkeypatch.setattr(JWTValidationMiddleware, "_refresh_jwks", _fake_refresh)


def _mcp_body() -> str:
    return _json.dumps({
        "jsonrpc": "2.0", "method": "initialize",
        "params": {"protocolVersion": "2025-11-25"}, "id": "t1",
    })


# ===========================================================================
# (2) Middleware allowance: tenant-less KC token reaches POST /onboard only
# ===========================================================================


def test_tenantless_token_rejected_on_mcp_but_passes_on_onboard(
    postgres_container, rsa_keypair, configure_kc, monkeypatch
):
    """SAME valid-but-tenant-less KC token: 401 on /mcp, NOT 401 on POST
    /api/auth/onboard (it gets past the middleware to the handler).

    This is the end-to-end proof of the narrow allowance: the middleware lets a
    verified tenant-less KC token through ONLY for POST /api/auth/onboard.
    """
    from contact_ops.api import auth as auth_mod

    # Use a DEDICATED principal: this test runs the real handler against the
    # container with a real COMMIT (NullPool, not a rolled-back db_session), so a
    # shared uc_uid would leak a committed membership into the other tests.
    token = _make_token(_kc_claims(sub=UC_E2E, tenant_id=None), rsa_keypair["private_pem"])

    # Keep the REAL onboard handler + REAL middleware + REAL routing (so this is a
    # true end-to-end allowance proof, not a stubbed route). We only make the
    # handler's DB calls land on the test container and we leave the KC switch
    # secret UNSET, so on the allowed path the real handler provisions and then
    # returns a clean 503 ("onboarding not configured"). The decisive bit is the
    # STATUS CONTRAST: 401 means the middleware blocked the token; ANY non-401
    # means the token was let through to the handler. A tracking flag confirms the
    # handler's session was actually opened.
    reached = {"db_opened": False}

    # A dedicated NullPool engine on the test container: NullPool opens a fresh
    # connection per checkout on whatever loop is active, so it is safe to use
    # from the TestClient's portal loop (asyncpg connections are loop-bound).
    eng = create_async_engine(postgres_container, poolclass=NullPool, echo=False)
    real_maker = async_sessionmaker(bind=eng, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def _tracking_session():  # type: ignore[no-untyped-def]
        reached["db_opened"] = True
        async with real_maker() as s:
            yield s

    monkeypatch.setattr(auth_mod, "async_session_maker", cast(Any, _tracking_session))
    # Ensure the inert-503 posture (no KC write attempted) regardless of env, so
    # the allowed path resolves to a deterministic non-401 (503) after provision.
    monkeypatch.setattr(get_settings(), "KEYCLOAK_SWITCH_CLIENT_SECRET", "", raising=False)
    _offline_jwks(rsa_keypair["jwk"], monkeypatch)

    client = TestClient(app, raise_server_exceptions=False)
    auth_header = {"Authorization": f"Bearer {token}"}

    # /mcp with a tenant-less token -> middleware fails closed -> 401.
    mcp_resp = client.post(
        "/mcp", headers={**auth_header, "content-type": "application/json"},
        content=_mcp_body(),
    )
    assert mcp_resp.status_code == 401, "tenant-less token must be 401 on /mcp"

    # POST /api/auth/onboard with the SAME token -> allowance -> reaches handler.
    onboard_resp = client.post("/api/auth/onboard", headers=auth_header)
    assert onboard_resp.status_code != 401, (
        "tenant-less token must NOT be 401 on POST /api/auth/onboard "
        f"(got {onboard_resp.status_code})"
    )
    assert reached["db_opened"] is True, (
        "request must have reached the onboard handler (its DB session opened)"
    )


def test_get_onboard_does_not_get_allowance(rsa_keypair, configure_kc, monkeypatch):
    """The allowance is POST-only: a GET to /api/auth/onboard with a tenant-less
    token is still rejected (the dispatcher requires method == POST)."""
    token = _make_token(_kc_claims(sub=UC_A, tenant_id=None), rsa_keypair["private_pem"])
    _offline_jwks(rsa_keypair["jwk"], monkeypatch)

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/auth/onboard", headers={"Authorization": f"Bearer {token}"})
    # GET never rides the allowance -> the tenant-less token is rejected at the
    # middleware (401). (A tenant-bearing token would instead 405 at routing, but
    # the point here is the missing-tenant fail-closed on the wrong method.)
    assert resp.status_code == 401


# ===========================================================================
# (3) Endpoint semantics — handler invoked directly with a synthetic Request
# ===========================================================================


def _session_factory(session: AsyncSession) -> Any:
    """Yield the test's transaction-rolled-back session wherever the handler does
    ``async with async_session_maker() as s`` (the test_notes.py idiom). The
    handler's commit() lands on the savepoint; the outer rollback still wins."""
    @asynccontextmanager
    async def _ctx() -> AsyncGenerator[AsyncSession, None]:
        yield session

    return cast(Any, _ctx)


def _fake_request(claims: dict | None, headers: dict | None = None) -> Any:
    """Minimal object quacking like the Request the handler reads.

    The handler only touches: request.state.jwt_claims, request.state.actor_chain
    (via getattr default), request.state.human_authority (via getattr default),
    and request.headers.get(...). A SimpleNamespace state + a dict-ish headers is
    sufficient and keeps the test free of a live ASGI scope.
    """
    state = types.SimpleNamespace()
    if claims is not None:
        state.jwt_claims = claims
    req = types.SimpleNamespace(state=state)
    req.headers = headers or {}
    return cast(Any, req)


async def _seed_membership(db: AsyncSession, *, uc_uid: str, tenant_slug: str) -> uuid.UUID:
    """Create a tenant + an active default membership for ``uc_uid`` directly
    (bypassing the provisioning function) so we can drive the idempotent-replay
    path. Runs as the test session's privileged role."""
    tenant_id = uuid.uuid4()
    # Own the tenant by uc_uid (a uuid string), exactly as provision_personal_
    # workspace does, so "no second workspace for this user" is a meaningful count.
    await db.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                isolation_mode, hipaa_mode, qdrant_namespace, garage_bucket_prefix,
                graph_name)
            VALUES (:id, :slug, 'personal', 'Seeded', CAST(:owner AS uuid), 'standard', false,
                :qns, :gbp, :gn)
            """
        ),
        {
            "id": tenant_id, "slug": tenant_slug, "owner": uc_uid,
            "qns": f"contact_ops__{tenant_slug}",
            "gbp": f"contact-ops-{tenant_slug}",
            "gn": f"contact_ops__{tenant_slug.replace('-', '_')}",
        },
    )
    await db.execute(
        text(
            """
            INSERT INTO user_tenant_membership (user_uc_uid, tenant_id, role,
                status, is_default, consent_basis, added_by)
            VALUES (:uc, :tid, 'admin', 'active', true, 'self_provided', 'seed')
            """
        ),
        {"uc": uc_uid, "tid": tenant_id},
    )
    await db.commit()
    return tenant_id


async def test_onboard_rejects_non_keycloak_issuer(db_session, monkeypatch):
    """A non-KC issuer (e.g. a PAT / Brigade / standalone identity) is rejected
    with 400 — onboarding provisions a *Keycloak* user only."""
    from fastapi import HTTPException

    from contact_ops.api import auth as auth_mod

    s = get_settings()
    monkeypatch.setattr(s, "KEYCLOAK_ISSUER", VALID_ISSUER, raising=False)
    monkeypatch.setattr(auth_mod, "async_session_maker", _session_factory(db_session))

    claims = _kc_claims(sub=UC_A, iss="contact-ops:personal-access-token", tenant_id=None)
    with pytest.raises(HTTPException) as ei:
        await auth_mod.onboard(_fake_request(claims))
    assert ei.value.status_code == 400


async def test_onboard_has_no_request_body_channel():
    """Structural proof that a caller cannot NAME a uc_uid to provision for: the
    handler's ONLY parameter is ``request``. There is no body model, so unlike
    switch-workspace (which takes ``SwitchWorkspaceRequest``) onboard has no
    caller-supplied field that could redirect ownership. Identity is therefore
    100% the verified claims the middleware placed on request.state."""
    import inspect

    from contact_ops.api import auth as auth_mod

    sig = inspect.signature(auth_mod.onboard)
    assert list(sig.parameters) == ["request"], (
        "onboard must take ONLY request (no body param that could name a uc_uid)"
    )


async def test_onboard_uc_uid_comes_from_token_not_body(db_session, monkeypatch):
    """uc_uid is taken from the verified token (the middleware-pinned ``uc_uid``,
    which IS ``sub``) — a caller cannot provision for a DIFFERENT uc_uid. We feed
    claims EXACTLY as the middleware delivers them (uc_uid already pinned to sub
    == UC_A; a stray non-identity field present to prove it is inert) and assert
    the new workspace is owned by, and its membership keyed to, UC_A — and that a
    different principal (UC_B) gets nothing."""
    from contact_ops.api import auth as auth_mod

    s = get_settings()
    monkeypatch.setattr(s, "KEYCLOAK_ISSUER", VALID_ISSUER, raising=False)
    monkeypatch.setattr(s, "KEYCLOAK_SWITCH_CLIENT_SECRET", "test-secret", raising=False)
    monkeypatch.setattr(auth_mod, "async_session_maker", _session_factory(db_session))

    # Stub the Keycloak admin write + the audit emit so the provisioned=true path
    # stays DB-local and deterministic (no network, no audit-role session).
    kc_calls: list[dict] = []

    async def _fake_kc(settings, *, uc_uid, tenant_id, tenant_slug):  # type: ignore[no-untyped-def]
        kc_calls.append({"uc_uid": uc_uid, "tenant_id": tenant_id, "tenant_slug": tenant_slug})

    async def _fake_audit(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(auth_mod, "_set_keycloak_tenant_attributes", _fake_kc)
    monkeypatch.setattr(auth_mod, "_emit_onboard_audit_event", _fake_audit)

    # Claims AS THE MIDDLEWARE PRODUCES THEM: uc_uid is pinned to sub (the
    # middleware overwrites any divergent uc_uid claim and logs loudly — see
    # test_jwt_validation.py::test_kc_uc_uid_pinned_to_sub_ignores_divergent_claim).
    # The handler relies on that invariant. A stray attacker-controlled field is
    # included to show the handler keys on identity, not on arbitrary claims.
    claims = _kc_claims(sub=UC_A, tenant_id=None, username="aaron", email="REMOVED")
    claims["uc_uid"] = UC_A  # middleware-pinned (== sub)
    claims["impersonate_uc_uid"] = UC_B  # inert: the handler never reads it

    resp = await auth_mod.onboard(_fake_request(claims))
    assert resp.provisioned is True

    # The provisioned workspace is owned by UC_A (the token sub) — NOT UC_B.
    owner = await db_session.execute(
        text("SELECT owner_user_id FROM tenants WHERE id = CAST(:t AS uuid)"),
        {"t": str(resp.tenant_id)},
    )
    assert str(owner.scalar()) == UC_A

    # The membership is keyed to UC_A; UC_B got nothing.
    rows = await db_session.execute(
        text("SELECT user_uc_uid FROM user_tenant_membership WHERE tenant_id = CAST(:t AS uuid)"),
        {"t": str(resp.tenant_id)},
    )
    members = rows.scalars().all()
    assert members == [UC_A]

    uc_b_count = await db_session.execute(
        text("SELECT count(*) FROM user_tenant_membership WHERE user_uc_uid = :u"),
        {"u": UC_B},
    )
    assert uc_b_count.scalar() == 0, "no membership may be created for the body/claim uc_uid"

    # The KC attribute write (when configured) is for the token sub, not UC_B.
    assert kc_calls and kc_calls[0]["uc_uid"] == UC_A


async def test_onboard_existing_member_returns_provisioned_false(db_session, monkeypatch):
    """An already-member call is an idempotent replay: provisioned=false, the
    EXISTING home tenant returned, no second workspace, and NO Keycloak write."""
    from contact_ops.api import auth as auth_mod

    s = get_settings()
    monkeypatch.setattr(s, "KEYCLOAK_ISSUER", VALID_ISSUER, raising=False)
    # Even if a secret IS configured, the idempotent path must NOT call KC.
    monkeypatch.setattr(s, "KEYCLOAK_SWITCH_CLIENT_SECRET", "test-secret", raising=False)
    monkeypatch.setattr(auth_mod, "async_session_maker", _session_factory(db_session))

    kc_called = {"n": 0}

    async def _fake_kc(*args, **kwargs):  # type: ignore[no-untyped-def]
        kc_called["n"] += 1

    monkeypatch.setattr(auth_mod, "_set_keycloak_tenant_attributes", _fake_kc)

    existing = await _seed_membership(db_session, uc_uid=UC_A, tenant_slug="aaron-existing")

    claims = _kc_claims(sub=UC_A, tenant_id=None, username="aaron")
    resp = await auth_mod.onboard(_fake_request(claims))

    assert resp.provisioned is False, "already-member call must report provisioned=false"
    assert resp.tenant_id == existing, "must return the EXISTING home tenant"
    assert resp.tenant_slug == "aaron-existing"
    assert kc_called["n"] == 0, "idempotent replay must not touch Keycloak"

    # No second workspace was materialized for this user.
    tcount = await db_session.execute(
        text("SELECT count(*) FROM tenants WHERE owner_user_id = CAST(:u AS uuid)"),
        {"u": UC_A},
    )
    assert tcount.scalar() == 1


async def test_onboard_missing_identity_fails_closed(db_session, monkeypatch):
    """No jwt_claims on the request (a programming error) fails closed with 401,
    never proceeding to provision without a verified identity."""
    from fastapi import HTTPException

    from contact_ops.api import auth as auth_mod

    monkeypatch.setattr(auth_mod, "async_session_maker", _session_factory(db_session))
    with pytest.raises(HTTPException) as ei:
        await auth_mod.onboard(_fake_request(None))
    assert ei.value.status_code == 401
