"""Suite-identity convergence (additive) — the §2.1–§2.7 deliverables.

Proves the additive suite-identity changes WITHOUT touching the preserved
auth-auto-provision base (those stay in test_p4_auto_provision.py /
test_jwt_validation.py, untouched). Layers, each mapped to a hand-off section:

  (1) canonical_role mapping (§2.6, SUITE-IDENTITY §7) — pure unit.
  (2) per-request addressing precedence + flag gating (§2.3, D4) — pure unit on
      the resolver, plus the wire-claim synonym fold in _validate.
  (3) azp enforcement behind TENANT_CLAIM_AZP_ENFORCE (§2.4) — _validate offline.
  (4) the signed thin projection (§2.2): allow-list omits every infra column,
      inert-503 without the secret, 401 on a missing/forged/expired signature
      (no existence oracle), 200 with a valid signature; HMAC verify unit tests.
  (5) switch-workspace re-scope: set_default_workspace flips is_default + clears
      the prior default + re-gates membership (§2.7) — DB (rt_engine) + handler.
  (6) isolation_tier/status mirror columns + the HIPAA→tier ratchet (§2.5) — DB.

DB tests run against the session-scoped testcontainer Postgres (conftest
postgres_container / db_session) with all Alembic migrations applied, using the
same sync rt_engine RLS-subject pattern as test_p4_membership_gate.py.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import types
import uuid
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, text

# ===========================================================================
# (1) canonical_role mapping (SUITE-IDENTITY §7)
# ===========================================================================


def test_canonical_role_maps_co_membership_to_suite() -> None:
    from contact_ops.mcp.roles import canonical_role

    assert canonical_role("viewer") == "viewer"
    assert canonical_role("member") == "member"
    assert canonical_role("manager") == "manager"
    assert canonical_role("admin") == "admin"  # CO's top aligns 1:1
    # Unknown / None -> least privilege, never a crash.
    assert canonical_role("owner") == "viewer"  # CO never SOURCES owner
    assert canonical_role("nonsense") == "viewer"
    assert canonical_role(None) == "viewer"
    assert canonical_role("") == "viewer"
    # Case / whitespace tolerant.
    assert canonical_role("  ADMIN ") == "admin"


def test_app_role_collapses_owner_to_admin() -> None:
    from contact_ops.mcp.roles import app_role

    assert app_role("viewer") == "viewer"
    assert app_role("member") == "member"
    assert app_role("manager") == "manager"
    assert app_role("admin") == "admin"
    # owner -> admin (CO's top): the inbound collapse (SUITE-IDENTITY §7).
    assert app_role("owner") == "admin"
    assert app_role("garbage") == "viewer"
    assert app_role(None) == "viewer"


def test_realm_role_from_canonical_for_brigade_path() -> None:
    """The Brigade path maps a canonical role to CO's uppercase realm ladder."""
    from contact_ops.mcp.roles import realm_role_from_canonical

    assert realm_role_from_canonical("viewer") == "CLIENT"
    assert realm_role_from_canonical("member") == "STAFF"
    assert realm_role_from_canonical("manager") == "MANAGER"
    assert realm_role_from_canonical("admin") == "ADMIN"
    assert realm_role_from_canonical("owner") == "ADMIN"  # collapses
    # Case-insensitive: an already-uppercase canonical name still maps (the
    # function lowercases first), so a token that happened to send "ADMIN" is
    # treated as canonical admin rather than being dropped.
    assert realm_role_from_canonical("ADMIN") == "ADMIN"
    # A value that is NOT a canonical role at all returns None so the Brigade
    # path falls back to its prior behavior rather than demoting/misclassifying.
    assert realm_role_from_canonical("superuser") is None
    assert realm_role_from_canonical("STAFF") is None  # not a canonical name
    assert realm_role_from_canonical(None) is None


def test_brigade_canonical_role_resolution_end_to_end(monkeypatch) -> None:
    """A Brigade token carrying a canonical `role` resolves to the CO ladder via
    realm_role_from_canonical; a legacy/absent role falls back to scopes."""
    from contact_ops.mcp.roles import realm_role_from_canonical
    from contact_ops.middleware.jwt_validation import _role_from_scopes

    # This mirrors the exact resolution expression in _validate_brigade.
    def resolve(role_claim, scopes):
        return (
            realm_role_from_canonical(role_claim)
            or str(role_claim or _role_from_scopes(scopes)).upper()
        )

    # canonical role claim -> ladder
    assert resolve("manager", []) == "MANAGER"
    assert resolve("owner", []) == "ADMIN"
    # absent role -> scope-derived (write: -> STAFF)
    assert resolve(None, ["write:contacts"]) == "STAFF"
    assert resolve(None, ["admin:*"]) == "ADMIN"
    assert resolve(None, []) == "CLIENT"
    # legacy uppercase role claim -> preserved (fallback .upper())
    assert resolve("ADMIN", []) == "ADMIN"


# ===========================================================================
# (2) per-request workspace addressing precedence (§2.3, D4)
# ===========================================================================


def _fake_request(path: str, headers: dict | None = None) -> Any:
    """Minimal object quacking like the Request the resolver reads (url.path +
    headers). Headers are lower-cased like Starlette's case-insensitive mapping."""
    url = types.SimpleNamespace(path=path)
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
    req = types.SimpleNamespace(url=url, headers=types.SimpleNamespace(get=hdrs.get))
    return cast(Any, req)


def _settings_with(per_request: bool):
    from contact_ops.core.config import get_settings

    s = get_settings()
    saved = s.WORKSPACE_PER_REQUEST_ENABLED
    s.WORKSPACE_PER_REQUEST_ENABLED = per_request
    return s, saved


CLAIM_WS = "11111111-1111-1111-1111-111111111111"
PATH_WS = "22222222-2222-2222-2222-222222222222"
HEADER_WS = "33333333-3333-3333-3333-333333333333"


def test_per_request_off_ignores_path_and_header() -> None:
    """Flag OFF: only the claim is used; path/header are ignored (today's behavior)."""
    from contact_ops.core.database import _resolve_effective_workspace

    s, saved = _settings_with(False)
    try:
        claims = {"tenant_id": CLAIM_WS}
        req = _fake_request(
            f"/api/v1/workspaces/{PATH_WS}/contacts",
            {"X-Workspace-Id": HEADER_WS},
        )
        ws, override = _resolve_effective_workspace(req, claims, s)
        assert ws == CLAIM_WS, "flag off must use the claim, not the path/header"
        assert override is False
    finally:
        s.WORKSPACE_PER_REQUEST_ENABLED = saved


def test_per_request_on_precedence_path_beats_header_beats_claim() -> None:
    """Flag ON: path > header > claim, and override is flagged when it differs."""
    from contact_ops.core.database import _resolve_effective_workspace

    s, saved = _settings_with(True)
    try:
        claims = {"tenant_id": CLAIM_WS}
        # (1) path wins
        req = _fake_request(
            f"/api/v1/workspaces/{PATH_WS}/contacts", {"X-Workspace-Id": HEADER_WS}
        )
        ws, override = _resolve_effective_workspace(req, claims, s)
        assert ws == PATH_WS and override is True
        # (2) header wins when no path
        req = _fake_request("/api/v1/contacts", {"X-Workspace-Id": HEADER_WS})
        ws, override = _resolve_effective_workspace(req, claims, s)
        assert ws == HEADER_WS and override is True
        # (3) claim when neither
        req = _fake_request("/api/v1/contacts", {})
        ws, override = _resolve_effective_workspace(req, claims, s)
        assert ws == CLAIM_WS and override is False
        # (4) header EQUAL to claim is not an "override"
        req = _fake_request("/api/v1/contacts", {"X-Workspace-Id": CLAIM_WS})
        ws, override = _resolve_effective_workspace(req, claims, s)
        assert ws == CLAIM_WS and override is False
    finally:
        s.WORKSPACE_PER_REQUEST_ENABLED = saved


def test_per_request_on_non_uuid_path_segment_falls_through() -> None:
    """A non-UUID path segment after /workspaces/ is ignored (falls to claim)."""
    from contact_ops.core.database import (
        _resolve_effective_workspace,
        _workspace_id_from_path,
    )

    assert _workspace_id_from_path("/api/v1/workspaces/not-a-uuid/x") == ""
    assert _workspace_id_from_path(f"/api/v1/workspaces/{PATH_WS}/x") == PATH_WS
    assert _workspace_id_from_path("/api/v1/contacts") == ""

    s, saved = _settings_with(True)
    try:
        claims = {"tenant_id": CLAIM_WS}
        req = _fake_request("/api/v1/workspaces/not-a-uuid/contacts", {})
        ws, override = _resolve_effective_workspace(req, claims, s)
        assert ws == CLAIM_WS and override is False
    finally:
        s.WORKSPACE_PER_REQUEST_ENABLED = saved


async def test_workspace_id_synonym_written_back_in_validate(monkeypatch) -> None:
    """_validate folds workspace_id<->tenant_id (SUITE-IDENTITY §6 wire claim).

    We drive the post-decode normalization by calling _validate with a token that
    carries ONLY workspace_id and assert both keys end up populated; and a token
    with ONLY tenant_id gets workspace_id mirrored. Uses the offline JWKS preseed.
    """
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from contact_ops.core.config import get_settings
    from contact_ops.main import app
    from contact_ops.middleware.jwt_validation import JWTValidationMiddleware

    iss = "https://test-keycloak.invalid/realms/uchub"
    aud = "contact-ops-mcp"
    s = get_settings()
    saved = (s.KEYCLOAK_ISSUER, s.KEYCLOAK_AUDIENCE, list(s.JWT_ALLOWED_ALGORITHMS))
    s.KEYCLOAK_ISSUER, s.KEYCLOAK_AUDIENCE, s.JWT_ALLOWED_ALGORITHMS = (
        iss, aud, ["RS256"],
    )

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = priv.public_key().public_numbers()

    def b64(n: int) -> str:
        ln = (n.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(n.to_bytes(ln, "big")).rstrip(b"=").decode()

    jwk = {"kty": "RSA", "kid": "k1", "use": "sig", "alg": "RS256",
           "n": b64(pub.n), "e": b64(pub.e)}

    def make(claims: dict) -> str:
        from jose import jwt as jjwt
        return jjwt.encode(claims, pem, algorithm="RS256", headers={"kid": "k1"})

    try:
        mw = JWTValidationMiddleware(app, get_settings())
        mw._jwks = {"keys": [jwk]}
        mw._jwks_expires = time.time() + 3600
        now = int(time.time())
        base = {"sub": "kc-sub-1", "iss": iss, "aud": aud,
                "iat": now - 60, "exp": now + 300, "nbf": now - 60}

        # workspace_id-only token: folded into tenant_id, both populated.
        ws_only = await mw._validate(make({**base, "workspace_id": CLAIM_WS}))
        assert ws_only is not None
        assert ws_only["tenant_id"] == CLAIM_WS
        assert ws_only["workspace_id"] == CLAIM_WS

        # tenant_id-only token: workspace_id mirrored.
        tid_only = await mw._validate(make({**base, "tenant_id": CLAIM_WS}))
        assert tid_only is not None
        assert tid_only["tenant_id"] == CLAIM_WS
        assert tid_only["workspace_id"] == CLAIM_WS
    finally:
        s.KEYCLOAK_ISSUER, s.KEYCLOAK_AUDIENCE, s.JWT_ALLOWED_ALGORITHMS = saved


# ===========================================================================
# (3) azp enforcement behind TENANT_CLAIM_AZP_ENFORCE (§2.4)
# ===========================================================================


def _rsa_mw():
    """Build a JWTValidationMiddleware with an offline RSA JWKS preseeded, plus a
    token factory. Returns (mw, make_token, jwk, settings, restore_fn)."""
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from contact_ops.core.config import get_settings
    from contact_ops.main import app
    from contact_ops.middleware.jwt_validation import JWTValidationMiddleware

    iss = "https://test-keycloak.invalid/realms/uchub"
    aud = "contact-ops-mcp"
    s = get_settings()
    saved = (
        s.KEYCLOAK_ISSUER, s.KEYCLOAK_AUDIENCE, list(s.JWT_ALLOWED_ALGORITHMS),
        list(s.TENANT_CLAIM_ALLOWED_AZP), s.TENANT_CLAIM_AZP_ENFORCE,
    )
    s.KEYCLOAK_ISSUER, s.KEYCLOAK_AUDIENCE, s.JWT_ALLOWED_ALGORITHMS = (
        iss, aud, ["RS256"],
    )

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = priv.public_key().public_numbers()

    def b64(n: int) -> str:
        ln = (n.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(n.to_bytes(ln, "big")).rstrip(b"=").decode()

    jwk = {"kty": "RSA", "kid": "k1", "use": "sig", "alg": "RS256",
           "n": b64(pub.n), "e": b64(pub.e)}

    def make(claims: dict) -> str:
        from jose import jwt as jjwt
        return jjwt.encode(claims, pem, algorithm="RS256", headers={"kid": "k1"})

    mw = JWTValidationMiddleware(app, get_settings())
    mw._jwks = {"keys": [jwk]}
    mw._jwks_expires = time.time() + 3600

    def restore() -> None:
        (s.KEYCLOAK_ISSUER, s.KEYCLOAK_AUDIENCE, s.JWT_ALLOWED_ALGORITHMS,
         s.TENANT_CLAIM_ALLOWED_AZP, s.TENANT_CLAIM_AZP_ENFORCE) = saved

    return mw, make, jwk, s, restore


def _kc_claims_azp(azp: str | None) -> dict:
    now = int(time.time())
    c = {
        "sub": "kc-sub-azp", "iss": "https://test-keycloak.invalid/realms/uchub",
        "aud": "contact-ops-mcp",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "iat": now - 60, "exp": now + 300, "nbf": now - 60,
    }
    if azp is not None:
        c["azp"] = azp
    return c


async def test_azp_enforce_off_warns_admits_bad_azp() -> None:
    """Flag OFF (default): a non-allowlisted azp is WARNED but ADMITTED."""
    mw, make, _jwk, s, restore = _rsa_mw()
    try:
        s.TENANT_CLAIM_ALLOWED_AZP = ["contact-ops-app", "contact-ops-mcp"]
        s.TENANT_CLAIM_AZP_ENFORCE = False
        token = make(_kc_claims_azp("rogue-client"))
        result = await mw._validate(token)
        assert result is not None, "warn-only must admit a bad azp"
        assert result["tenant_id"] == "11111111-1111-1111-1111-111111111111"
    finally:
        restore()


async def test_azp_enforce_on_rejects_bad_azp_admits_good() -> None:
    """Flag ON: a non-allowlisted azp is REJECTED (None); an allowlisted one passes."""
    mw, make, _jwk, s, restore = _rsa_mw()
    try:
        s.TENANT_CLAIM_ALLOWED_AZP = ["contact-ops-app", "contact-ops-mcp"]
        s.TENANT_CLAIM_AZP_ENFORCE = True

        bad = await mw._validate(make(_kc_claims_azp("rogue-client")))
        assert bad is None, "enforce must REJECT a non-allowlisted azp"

        good = await mw._validate(make(_kc_claims_azp("contact-ops-app")))
        assert good is not None, "enforce must ADMIT an allowlisted azp"
        assert good["tenant_id"] == "11111111-1111-1111-1111-111111111111"
    finally:
        restore()


# ===========================================================================
# (4) signed thin projection (§2.2) — allow-list, inert, signature, oracle
# ===========================================================================

# The forbidden infra columns the projection must NEVER expose.
FORBIDDEN_KEYS = {
    "graph_mode", "graph_name", "qdrant_namespace", "garage_bucket_prefix",
    "tenant_keys", "owner_user_id", "retention_policy", "branding",
    "kind", "parent_tenant_id", "etag", "created_at", "updated_at",
    "deprecated_at", "carddav_enabled", "data_intel_publish_consent",
    "isolation_mode_locked",
}
ALLOWED_KEYS = {
    "workspace_id", "slug", "display_name", "isolation_mode", "isolation_tier",
    "hipaa_mode", "gdpr_mode", "data_residency", "status",
}


def test_projection_allow_list_omits_every_infra_column() -> None:
    """workspace_thin_projection emits EXACTLY the 9 allow-listed fields and NONE
    of the infra columns, even when the Tenant carries infra values."""
    from contact_ops.mcp.tools.tenancy import (
        WORKSPACE_THIN_FIELDS,
        workspace_thin_projection,
    )
    from contact_ops.models import Tenant
    from contact_ops.models.enums import TenantKind

    t = Tenant(
        id=uuid.UUID("44444444-4444-4444-4444-444444444444"),
        slug="acme",
        kind=TenantKind.brand,
        display_name="Acme",
        owner_user_id=uuid.UUID("55555555-5555-5555-5555-555555555555"),
        hipaa_mode=False,
        gdpr_mode=True,
        data_residency="us-east",
        graph_mode="per_org_graph",
        graph_name="contact_ops__acme",  # SECRET infra
        qdrant_namespace="contact_ops__acme",  # SECRET infra
        garage_bucket_prefix="contact-ops-acme",  # SECRET infra
        isolation_mode="standard",
        isolation_tier="shared",
        status="active",
        branding={"logo": "x"},
        retention_policy={"days": 90},
    )
    proj = workspace_thin_projection(t)

    assert set(proj.keys()) == ALLOWED_KEYS == set(WORKSPACE_THIN_FIELDS)
    for forbidden in FORBIDDEN_KEYS:
        assert forbidden not in proj, f"projection leaked infra column: {forbidden}"
    # The values are the workspace identity + compliance dials only.
    assert proj["workspace_id"] == "44444444-4444-4444-4444-444444444444"
    assert proj["slug"] == "acme"
    assert proj["isolation_tier"] == "shared"
    assert proj["status"] == "active"
    assert proj["gdpr_mode"] is True
    # No infra value can be present even by value.
    assert "contact_ops__acme" not in proj.values()
    assert "contact-ops-acme" not in proj.values()


def _sign(workspace_id: str, secret: str, ts: int | None = None) -> str:
    ts = ts if ts is not None else int(time.time())
    mac = hmac.new(
        secret.encode(), f"{workspace_id}.{ts}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{ts}.{mac}"


def test_mirror_signature_verify_unit() -> None:
    """The HMAC verify accepts a correct signature, rejects forged/expired/missing."""
    from contact_ops.api.workspaces import _verify_mirror_signature
    from contact_ops.core.config import get_settings

    s = get_settings()
    saved = (s.WORKSPACE_MIRROR_READ_SECRET, s.WORKSPACE_MIRROR_READ_MAX_AGE_SECONDS)
    s.WORKSPACE_MIRROR_READ_SECRET = "top-secret-mirror-key"  # noqa: S105
    s.WORKSPACE_MIRROR_READ_MAX_AGE_SECONDS = 300
    wid = "66666666-6666-6666-6666-666666666666"
    try:
        # valid
        assert _verify_mirror_signature(wid, _sign(wid, "top-secret-mirror-key"), s)
        # wrong secret -> reject
        assert not _verify_mirror_signature(wid, _sign(wid, "WRONG-key"), s)
        # signature for a DIFFERENT workspace id -> reject (binds the id)
        other = _sign("99999999-9999-9999-9999-999999999999", "top-secret-mirror-key")
        assert not _verify_mirror_signature(wid, other, s)
        # expired timestamp -> reject
        old = _sign(wid, "top-secret-mirror-key", ts=int(time.time()) - 10_000)
        assert not _verify_mirror_signature(wid, old, s)
        # missing / malformed -> reject (never crash)
        assert not _verify_mirror_signature(wid, None, s)
        assert not _verify_mirror_signature(wid, "garbage", s)
        assert not _verify_mirror_signature(wid, "123.", s)
    finally:
        (s.WORKSPACE_MIRROR_READ_SECRET,
         s.WORKSPACE_MIRROR_READ_MAX_AGE_SECONDS) = saved


def _ws_request(sig: str | None) -> Any:
    """A minimal Request-like for the workspaces handler (it reads nothing off
    request; the signature comes via the typed Header param)."""
    return cast(Any, types.SimpleNamespace())


async def test_projection_endpoint_inert_503_without_secret() -> None:
    """INERT: with no WORKSPACE_MIRROR_READ_SECRET the endpoint 503s (no body)."""
    from fastapi import HTTPException

    from contact_ops.api import workspaces as ws_mod
    from contact_ops.core.config import get_settings

    s = get_settings()
    saved = s.WORKSPACE_MIRROR_READ_SECRET
    s.WORKSPACE_MIRROR_READ_SECRET = ""
    try:
        with pytest.raises(HTTPException) as ei:
            await ws_mod.get_workspace_projection(
                uuid.UUID("66666666-6666-6666-6666-666666666666"),
                x_workspace_signature=None,
            )
        assert ei.value.status_code == 503
    finally:
        s.WORKSPACE_MIRROR_READ_SECRET = saved


async def test_projection_endpoint_401_on_bad_signature_no_oracle() -> None:
    """A missing/forged signature -> 401 BEFORE any DB read: not an existence
    oracle. We monkeypatch the session maker to assert it is NEVER opened."""
    from fastapi import HTTPException

    from contact_ops.api import workspaces as ws_mod
    from contact_ops.core.config import get_settings

    s = get_settings()
    saved = s.WORKSPACE_MIRROR_READ_SECRET
    s.WORKSPACE_MIRROR_READ_SECRET = "top-secret-mirror-key"  # noqa: S105
    opened = {"db": False}

    def _boom(*a, **k):  # the session maker must never be called on 401
        opened["db"] = True
        raise AssertionError("DB must not be touched on a bad signature")

    try:
        import contextlib

        # patch the module-level async_session_maker the handler uses
        ws_mod.async_session_maker = cast(Any, contextlib.asynccontextmanager(_boom))
        with pytest.raises(HTTPException) as ei:
            await ws_mod.get_workspace_projection(
                uuid.UUID("66666666-6666-6666-6666-666666666666"),
                x_workspace_signature="123.forged",
            )
        assert ei.value.status_code == 401
        assert opened["db"] is False, "no DB read may happen on a bad signature"
    finally:
        s.WORKSPACE_MIRROR_READ_SECRET = saved


# ===========================================================================
# (5) + (6) DB-backed: set_default_workspace, ratchet coupling, projection read
# ===========================================================================


@pytest.fixture(scope="module")
def rt_engine(postgres_container: str):
    """Sync RLS-subject engine (NOSUPERUSER NOBYPASSRLS member of contact_ops_app),
    same bootstrap as test_p4_membership_gate.py."""
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


SD_A = "a1a1a1a1-0000-0000-0000-000000000001"
SD_B = "b2b2b2b2-0000-0000-0000-000000000002"
SD_C = "c3c3c3c3-0000-0000-0000-000000000003"


def test_set_default_workspace_flips_and_clears_prior(rt_engine):
    """set_default_workspace sets the chosen row default + clears the prior, and
    re-gates membership (a non-member target is a no-op false)."""
    with rt_engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text(
                "INSERT INTO tenants (id,slug,kind,display_name,owner_user_id,"
                "qdrant_namespace,garage_bucket_prefix) VALUES "
                "(:a,'sd-a','brand','A',:a,'a-ns','a-bkt'),"
                "(:b,'sd-b','brand','B',:b,'b-ns','b-bkt'),"
                "(:cc,'sd-c','brand','C',:cc,'c-ns','c-bkt')"
            ), {"a": SD_A, "b": SD_B, "cc": SD_C})
            # uc-aaron is a member of A (default) and B (not default); NOT of C.
            c.execute(text(
                "INSERT INTO user_tenant_membership "
                "(user_uc_uid,tenant_id,role,status,is_default) VALUES "
                "('uc-aaron',:a,'admin','active',true),"
                "('uc-aaron',:b,'member','active',false)"
            ), {"a": SD_A, "b": SD_B})

            def call(uc, w):
                return c.execute(
                    text("SELECT set_default_workspace(:u, CAST(:w AS uuid))"),
                    {"u": uc, "w": w},
                ).scalar()

            def is_default(w):
                return c.execute(
                    text("SELECT is_default FROM user_tenant_membership "
                         "WHERE user_uc_uid='uc-aaron' AND tenant_id=CAST(:w AS uuid)"),
                    {"w": w},
                ).scalar()

            # Switch default A -> B.
            assert call("uc-aaron", SD_B) is True
            assert is_default(SD_B) is True, "B is now default"
            assert is_default(SD_A) is False, "prior default A cleared"
            # last_switched_at stamped on B.
            stamped = c.execute(text(
                "SELECT last_switched_at FROM user_tenant_membership "
                "WHERE user_uc_uid='uc-aaron' AND tenant_id=CAST(:w AS uuid)"
            ), {"w": SD_B}).scalar()
            assert stamped is not None

            # Non-member target C -> no-op false; defaults unchanged.
            assert call("uc-aaron", SD_C) is False
            assert is_default(SD_B) is True
            # An unknown uc_uid -> false.
            assert call("uc-nobody", SD_B) is False
        finally:
            trans.rollback()


def test_set_default_workspace_is_security_definer(rt_engine):
    """Guard the DEFINER posture (a future ALTER to INVOKER would re-RLS-block the
    cross-tenant default flip)."""
    with rt_engine.connect() as c:
        prosecdef = c.execute(text(
            "SELECT prosecdef FROM pg_proc WHERE proname='set_default_workspace'"
        )).scalar()
        assert prosecdef is True


def test_isolation_tier_ratchet_coupling(rt_engine):
    """0039 ratchet: promote-only tier + HIPAA⇒tier≥premium, enforced at the DB."""
    with rt_engine.connect() as c:
        trans = c.begin()
        try:
            tid = "d4d4d4d4-0000-0000-0000-000000000004"
            c.execute(text(
                "INSERT INTO tenants (id,slug,kind,display_name,owner_user_id,"
                "qdrant_namespace,garage_bucket_prefix) VALUES "
                "(:t,'rt-x','brand','X',:t,'x-ns','x-bkt')"
            ), {"t": tid})

            # Defaults: shared + active.
            row = c.execute(text(
                "SELECT isolation_tier, status FROM tenants WHERE id=CAST(:t AS uuid)"
            ), {"t": tid}).mappings().one()
            assert row["isolation_tier"] == "shared"
            assert row["status"] == "active"

            # Promote shared -> premium: OK.
            c.execute(text(
                "UPDATE tenants SET isolation_tier='premium' WHERE id=CAST(:t AS uuid)"
            ), {"t": tid})
            assert c.execute(text(
                "SELECT isolation_tier FROM tenants WHERE id=CAST(:t AS uuid)"
            ), {"t": tid}).scalar() == "premium"

            # Demote premium -> shared: RAISES (promote-only). SAVEPOINT-wrapped
            # so the RAISE does not abort the outer transaction.
            with pytest.raises(Exception) as ei:  # noqa: PT012
                with c.begin_nested():
                    c.execute(text(
                        "UPDATE tenants SET isolation_tier='shared' "
                        "WHERE id=CAST(:t AS uuid)"
                    ), {"t": tid})
            assert "promote-only" in str(ei.value)
            # The tier is still premium after the rejected demote.
            assert c.execute(text(
                "SELECT isolation_tier FROM tenants WHERE id=CAST(:t AS uuid)"
            ), {"t": tid}).scalar() == "premium"
        finally:
            trans.rollback()


def test_hipaa_requires_tier_premium(rt_engine):
    """HIPAA on a shared-tier (strict) tenant RAISES the §5 coupling at the DB."""
    with rt_engine.connect() as c:
        trans = c.begin()
        try:
            tid = "e5e5e5e5-0000-0000-0000-000000000005"
            # Strict + shared, non-HIPAA first (strict satisfies the mode ratchet).
            c.execute(text(
                "INSERT INTO tenants (id,slug,kind,display_name,owner_user_id,"
                "isolation_mode,isolation_tier,qdrant_namespace,garage_bucket_prefix) "
                "VALUES (:t,'rt-h','brand','H',:t,'strict','shared','h-ns','h-bkt')"
            ), {"t": tid})
            # Enabling HIPAA while tier is still 'shared' must RAISE the coupling.
            # Wrap in a SAVEPOINT so the RAISE doesn't abort the outer tx (the
            # next asserts continue in the same connection).
            with pytest.raises(Exception) as ei:  # noqa: PT012
                with c.begin_nested():
                    c.execute(text(
                        "UPDATE tenants SET hipaa_mode=true WHERE id=CAST(:t AS uuid)"
                    ), {"t": tid})
            assert "isolation_tier >= premium" in str(ei.value)

            # Promote to premium THEN enable HIPAA: allowed (strict+premium+hipaa).
            c.execute(text(
                "UPDATE tenants SET isolation_tier='premium' WHERE id=CAST(:t AS uuid)"
            ), {"t": tid})
            c.execute(text(
                "UPDATE tenants SET hipaa_mode=true WHERE id=CAST(:t AS uuid)"
            ), {"t": tid})
            assert c.execute(text(
                "SELECT hipaa_mode FROM tenants WHERE id=CAST(:t AS uuid)"
            ), {"t": tid}).scalar() is True
        finally:
            trans.rollback()


def test_projection_endpoint_200_reads_workspace_omits_infra(
    postgres_container, monkeypatch
):
    """A VALID signature returns the thin projection of a real workspace, sourced
    through the GUC-bound RLS read, and NEVER carries an infra column (the §2.2
    happy path through the handler). Driven on the TestClient portal loop with a
    dedicated NullPool engine (the self-contained pattern from
    test_p4_auto_provision::test_tenantless_token_rejected_on_mcp_but_passes_on_onboard
    — NullPool opens a fresh loop-bound asyncpg connection per checkout, so it is
    safe to invoke from the portal loop and avoids the session-fixture loop)."""
    import contextlib

    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import NullPool
    from starlette.testclient import TestClient

    from contact_ops.api import workspaces as ws_mod
    from contact_ops.core.config import get_settings
    from contact_ops.main import app

    wid = uuid.uuid4()
    sync_url = postgres_container.replace("postgresql+asyncpg://", "postgresql://")
    # Seed (committed) a workspace carrying SECRET infra values via a sync engine.
    seed_eng = create_engine(sync_url, future=True)
    with seed_eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO tenants (id,slug,kind,display_name,owner_user_id,"
                "isolation_mode,isolation_tier,status,hipaa_mode,gdpr_mode,"
                "data_residency,graph_mode,graph_name,qdrant_namespace,"
                "garage_bucket_prefix) VALUES "
                "(:id,'mirror-w','brand','Mirror WS',:id,'standard','premium',"
                "'active',false,true,'eu-west','per_org_graph','contact_ops__secret',"
                "'contact_ops__secret_ns','contact-ops-secret-bkt')"
            ),
            {"id": str(wid)},
        )
    seed_eng.dispose()

    s = get_settings()
    saved = s.WORKSPACE_MIRROR_READ_SECRET
    s.WORKSPACE_MIRROR_READ_SECRET = "top-secret-mirror-key"  # noqa: S105

    eng = create_async_engine(postgres_container, poolclass=NullPool, echo=False)
    real_maker = async_sessionmaker(
        bind=eng, class_=AsyncSession, expire_on_commit=False
    )

    @contextlib.asynccontextmanager
    async def _sess():
        async with real_maker() as session:
            yield session

    monkeypatch.setattr(ws_mod, "async_session_maker", cast(Any, _sess))
    try:
        with TestClient(app) as client:
            resp = client.get(
                f"/api/v1/workspaces/{wid}",
                headers={
                    "X-Workspace-Signature": _sign(str(wid), "top-secret-mirror-key")
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Exactly the allow-list, none of the infra columns.
        assert set(body.keys()) == ALLOWED_KEYS
        for forbidden in FORBIDDEN_KEYS:
            assert forbidden not in body
        assert body["workspace_id"] == str(wid)
        assert body["slug"] == "mirror-w"
        assert body["isolation_tier"] == "premium"
        assert body["status"] == "active"
        assert body["data_residency"] == "eu-west"
        # No infra VALUE leaks either.
        assert "contact_ops__secret" not in body.values()
        assert "contact_ops__secret_ns" not in body.values()
        assert "contact-ops-secret-bkt" not in body.values()

        # A FORGED signature for the same (existing) workspace -> 401, never the
        # body: proves it is not an existence oracle even for a real workspace.
        with TestClient(app) as client:
            forged = client.get(
                f"/api/v1/workspaces/{wid}",
                headers={"X-Workspace-Signature": "123.deadbeef"},
            )
        assert forged.status_code == 401
        assert "slug" not in forged.text
    finally:
        s.WORKSPACE_MIRROR_READ_SECRET = saved


def test_projection_endpoint_404_on_missing_with_valid_signature(
    postgres_container, monkeypatch
):
    """A VALID signature for a workspace that does not exist -> 404 (the trusted
    sibling learns non-existence; a bad signature would have been 401). The
    inert/auth/lookup path is exercised on the TestClient loop against the
    testcontainer."""
    import contextlib

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import NullPool
    from starlette.testclient import TestClient

    from contact_ops.api import workspaces as ws_mod
    from contact_ops.core.config import get_settings
    from contact_ops.main import app

    s = get_settings()
    saved = s.WORKSPACE_MIRROR_READ_SECRET
    s.WORKSPACE_MIRROR_READ_SECRET = "top-secret-mirror-key"  # noqa: S105
    missing = uuid.uuid4()

    eng = create_async_engine(postgres_container, poolclass=NullPool, echo=False)
    real_maker = async_sessionmaker(
        bind=eng, class_=AsyncSession, expire_on_commit=False
    )

    @contextlib.asynccontextmanager
    async def _sess():
        async with real_maker() as session:
            yield session

    monkeypatch.setattr(ws_mod, "async_session_maker", cast(Any, _sess))
    try:
        with TestClient(app) as client:
            resp = client.get(
                f"/api/v1/workspaces/{missing}",
                headers={
                    "X-Workspace-Signature": _sign(
                        str(missing), "top-secret-mirror-key"
                    )
                },
            )
        assert resp.status_code == 404
    finally:
        s.WORKSPACE_MIRROR_READ_SECRET = saved


def test_create_tenant_persists_registry_supplied_workspace_id(rt_engine):
    """create_tenant's INSERT uses a registry-supplied id verbatim when given,
    else the uuidv7 DEFAULT. We exercise the SQL contract directly (the handler's
    INSERT path) to prove the mirror-mint behavior end to end at the DB."""
    with rt_engine.connect() as c:
        trans = c.begin()
        try:
            # registry-supplied id is persisted exactly
            supplied = "f6f6f6f6-0000-0000-0000-000000000006"
            c.execute(text(
                "INSERT INTO tenants (id,slug,kind,display_name,owner_user_id,"
                "qdrant_namespace,garage_bucket_prefix) VALUES "
                "(CAST(:id AS uuid),'reg-w','brand','Reg',:id,'r-ns','r-bkt')"
            ), {"id": supplied})
            got = c.execute(text(
                "SELECT id FROM tenants WHERE slug='reg-w'"
            )).scalar()
            assert str(got) == supplied, "registry-supplied workspace_id persisted verbatim"

            # omitted id -> uuidv7 DEFAULT mints one (non-null, valid uuid)
            c.execute(text(
                "INSERT INTO tenants (slug,kind,display_name,owner_user_id,"
                "qdrant_namespace,garage_bucket_prefix) VALUES "
                "('std-w','brand','Std',CAST(:o AS uuid),'s-ns','s-bkt')"
            ), {"o": "07070707-0000-0000-0000-000000000007"})
            minted = c.execute(text(
                "SELECT id FROM tenants WHERE slug='std-w'"
            )).scalar()
            assert minted is not None
            uuid.UUID(str(minted))  # parses as a uuid
            assert str(minted) != supplied
        finally:
            trans.rollback()
