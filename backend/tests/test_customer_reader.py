import uuid
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from contact_ops.core.config import get_settings
from contact_ops.mcp.errors import ToolError
from contact_ops.mcp.tools import customer_reader as reader

SUB = "d0b3c214-5350-4b99-a722-8803444c2750"
TENANT = "00000000-0000-0000-0000-000000000020"


def proof():
    return {
        "sub": SUB,
        "act": {"sub": SUB, "client_id": "customer-ops"},
        "workspace_id": TENANT,
        "scopes": ["contacts:read"],
    }


@pytest.mark.parametrize(
    "change",
    [
        None,
        {"act": {}},
        {"act": {"sub": SUB, "client_id": "other-app"}},
        {"act": {"sub": "human", "client_id": "customer-ops"}},
        {"workspace_id": str(uuid.uuid4())},
        {"scopes": ["contacts:read", "contacts:write"]},
        {"role": "ADMIN"},
        {"sub": "not-a-uuid"},
    ],
)
def test_rejects_arbitrary_human_foreign_or_write_proof(change):
    claims = proof() if change is not None else None
    if change is not None:
        claims.update(change)
    with pytest.raises(ToolError):
        reader.customer_reader_subject(claims, TENANT)


def test_accepts_exact_reader_and_hides_proof():
    assert reader.customer_reader_subject(proof(), TENANT) == SUB
    assert "sensitive-proof-token-123" not in repr(
        reader.ConnectCustomerReaderInput(reader_proof=SecretStr("sensitive-proof-token-123"))
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing", [None, ("viewer", "active"), ("viewer", "revoked"), ("admin", "active")]
)
async def test_owner_grant_real_postgres(db_session, seeded_tenants, monkeypatch, existing):
    tid = seeded_tenants["non_hipaa"] if "non_hipaa" in seeded_tenants else uuid.UUID(TENANT)
    assert str(tid) == TENANT
    await db_session.execute(
        text(
            "INSERT INTO user_tenant_membership "
            "(user_uc_uid,tenant_id,role,status,is_default) VALUES "
            "(:u,:t,'admin','active',false)"
        ),
        {"u": TENANT, "t": TENANT},
    )
    if existing:
        await db_session.execute(
            text(
                "INSERT INTO user_tenant_membership "
                "(user_uc_uid,tenant_id,role,status,is_default) VALUES (:u,:t,:r,:s,false)"
            ),
            {"u": SUB, "t": TENANT, "r": existing[0], "s": existing[1]},
        )
    await db_session.execute(
        text("SELECT set_config('app.tenant_id', :t, true),set_config('app.uc_uid', :u,true)"),
        {"t": TENANT, "u": TENANT},
    )
    await db_session.execute(text("SET LOCAL ROLE contact_ops_app"))
    ctx = SimpleNamespace(
        tenant_id=tid,
        user_id=TENANT,
        db=db_session,
        claims={"iss": get_settings().KEYCLOAK_ISSUER, "scope": "tenant:write membership:write"},
    )
    monkeypatch.setattr(reader, "verify_brigade_jwt", lambda token: proof())
    audit = AsyncMock()
    monkeypatch.setattr(reader, "emit_action_event", audit)
    req = reader.ConnectCustomerReaderInput(reader_proof=SecretStr("sensitive-proof-token-123"))
    if existing and existing != ("viewer", "active"):
        with pytest.raises(ToolError):
            await reader.connect_customer_ops_reader(ctx, req)
        audit.assert_not_awaited()
    else:
        result = await reader.connect_customer_ops_reader(ctx, req)
        assert result.role == "viewer" and result.created == (existing is None)
        row = (
            await db_session.execute(
                text(
                    "SELECT role,status FROM user_tenant_membership WHERE tenant_id=:t AND "
                    "user_uc_uid=:u"
                ),
                {"t": TENANT, "u": SUB},
            )
        ).one()
        assert tuple(row) == ("viewer", "active")
        if existing is None:
            assert "sensitive-proof" not in repr(audit.await_args)
            assert audit.await_count == 1


@pytest.mark.asyncio
async def test_non_owner_or_wrong_issuer_cannot_grant(monkeypatch):
    db = SimpleNamespace(execute=AsyncMock())
    ctx = SimpleNamespace(
        tenant_id=uuid.UUID(TENANT),
        user_id=TENANT,
        db=db,
        claims={"iss": "https://untrusted.example", "scope": "tenant:write membership:write"},
    )
    with pytest.raises(ToolError):
        await reader.connect_customer_ops_reader(
            ctx,
            reader.ConnectCustomerReaderInput(reader_proof=SecretStr("sensitive-proof-token-123")),
        )
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,gate", [("viewer", True), (None, True), ("viewer", False), (None, False)]
)
async def test_actual_brigade_middleware_caps_viewer_and_rejects_revoked(monkeypatch, role, gate):
    from contact_ops.mcp.rbac import require_scopes
    from contact_ops.middleware import jwt_validation as auth

    class Result:
        def __init__(self, row=None, scalar=None):
            self.row = row
            self.scalar = scalar

        def mappings(self):
            return self

        def first(self):
            return self.row

        def scalar_one_or_none(self):
            return self.scalar

    class DB:
        def __init__(self):
            self.n = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, *args):
            self.n += 1
            return (
                Result(row={"id": TENANT, "slug": "synthetic", "hipaa_mode": False})
                if self.n == 2
                else Result(scalar=role)
            )

    monkeypatch.setattr(auth, "async_session_maker", lambda: DB())
    claims = proof()
    claims["scopes"] = ["contacts:read", "contacts:write", "tenant:write"]
    claims["role"] = "ADMIN"
    monkeypatch.setattr(auth, "verify_brigade_jwt", lambda _: claims)
    middleware = auth.JWTValidationMiddleware(lambda *args: None, get_settings())
    middleware.settings = SimpleNamespace(
        MEMBERSHIP_GATE_ENFORCED=gate,
        BRIGADE_TRUSTED_ISSUER="https://broker.invalid",
        BRIGADE_EXPECTED_AUDIENCE="contact-ops",
    )
    result = await middleware._validate_brigade("short-lived-token")
    if role is None and gate:
        assert result is None
    elif role is None:
        assert "person:write" in result["scope"].split()
    else:
        assert result["realm_access"]["roles"] == ["CLIENT"]
        assert set(result["scope"].split()) == {"contacts:read", "person:read"}
        ctx = SimpleNamespace(claims=result)
        require_scopes(ctx, ["person:read"])
        with pytest.raises(ToolError):
            require_scopes(ctx, ["person:write"])


@pytest.mark.asyncio
async def test_concurrent_owner_grants_create_only_one_viewer(db_engine, monkeypatch):
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession

    tid = uuid.uuid4()
    owner = uuid.uuid4()
    subject = uuid.uuid4()
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO "
                "tenants(id,slug,kind,display_name,owner_user_id,"
                "qdrant_namespace,garage_bucket_prefix) "
                "VALUES(:t,:slug,'brand','Synthetic reader concurrency',:o,:slug,:slug)"
            ),
            {"t": tid, "o": owner, "slug": str(tid)},
        )
        await conn.execute(
            text(
                "INSERT INTO user_tenant_membership(user_uc_uid,tenant_id,role,status) "
                "VALUES(:u,:t,'admin','active')"
            ),
            {"u": str(owner), "t": tid},
        )
    claims = {
        "sub": str(subject),
        "act": {"sub": str(subject), "client_id": "customer-ops"},
        "workspace_id": str(tid),
        "scopes": ["contacts:read"],
    }
    monkeypatch.setattr(reader, "verify_brigade_jwt", lambda _: claims)
    monkeypatch.setattr(reader, "emit_action_event", AsyncMock())

    async def grant():
        async with AsyncSession(db_engine) as db, db.begin():
            await db.execute(
                text("SELECT set_config('app.tenant_id',:t,true),set_config('app.uc_uid',:u,true)"),
                {"t": str(tid), "u": str(owner)},
            )
            await db.execute(text("SET LOCAL ROLE contact_ops_app"))
            ctx = SimpleNamespace(
                tenant_id=tid,
                user_id=str(owner),
                db=db,
                claims={
                    "iss": get_settings().KEYCLOAK_ISSUER,
                    "scope": "tenant:write membership:write",
                },
            )
            return await reader.connect_customer_ops_reader(
                ctx,
                reader.ConnectCustomerReaderInput(
                    reader_proof=SecretStr("sensitive-proof-token-123")
                ),
            )

    try:
        results = await asyncio.gather(grant(), grant())
        assert sorted(x.created for x in results) == [False, True]
        async with db_engine.connect() as conn:
            n = await conn.scalar(
                text(
                    "SELECT count(*) FROM user_tenant_membership WHERE tenant_id=:t AND "
                    "user_uc_uid=:u AND role='viewer'"
                ),
                {"t": tid, "u": str(subject)},
            )
            assert n == 1
    finally:
        async with db_engine.begin() as conn:
            await conn.execute(text("DELETE FROM tenants WHERE id=:t"), {"t": tid})


@pytest.mark.parametrize(
    "arguments",
    [
        {"reader_proof": "sensitive-short"},
        {"reader_proof": "sensitive-proof" + ("x" * 16001)},
        {"reader_proof": "sensitive-proof-token-123", "unexpected": "sensitive-extra"},
    ],
)
def test_dispatcher_validation_errors_hide_secret_inputs(arguments):
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc:
        reader.ConnectCustomerReaderInput.model_validate(arguments)
    # Generic MCP dispatcher returns str(exc); this model must suppress input values.
    assert "sensitive" not in str(exc.value)


@pytest.mark.asyncio
async def test_owner_grant_with_real_separate_audit_transaction(db_engine, monkeypatch):
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession

    tid, owner, subject = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with db_engine.begin() as conn:
        await conn.execute(
            text("""INSERT INTO tenants
            (id,slug,kind,display_name,owner_user_id,qdrant_namespace,garage_bucket_prefix)
            VALUES(:t,:slug,'brand','Synthetic split audit',:o,:slug,:slug)"""),
            {"t": tid, "o": owner, "slug": str(tid)},
        )
        await conn.execute(
            text("""INSERT INTO user_tenant_membership
            (user_uc_uid,tenant_id,role,status) VALUES(:u,:t,'admin','active')"""),
            {"u": str(owner), "t": tid},
        )
    claims = {
        "sub": str(subject),
        "act": {"sub": str(subject), "client_id": "customer-ops"},
        "workspace_id": str(tid),
        "scopes": ["contacts:read"],
    }
    monkeypatch.setattr(reader, "verify_brigade_jwt", lambda _: claims)
    async with AsyncSession(db_engine) as db, AsyncSession(db_engine) as audit:
        async with db.begin(), audit.begin():
            for session, role in [(db, "contact_ops_app"), (audit, "contact_ops_audit")]:
                await session.execute(
                    text("""SELECT set_config('app.tenant_id',:t,true),
                    set_config('app.uc_uid',:u,true)"""),
                    {"t": str(tid), "u": str(owner)},
                )
                await session.execute(text("SET LOCAL ROLE " + role))
            ctx = SimpleNamespace(
                tenant_id=tid,
                user_id=str(owner),
                db=db,
                audit_db=audit,
                actor_chain={"sub": str(owner)},
                human_authority=str(owner),
                request_id=str(uuid.uuid4()),
                claims={
                    "iss": get_settings().KEYCLOAK_ISSUER,
                    "scope": "tenant:write membership:write",
                },
            )
            result = await asyncio.wait_for(
                reader.connect_customer_ops_reader(
                    ctx,
                    reader.ConnectCustomerReaderInput(
                        reader_proof=SecretStr("sensitive-proof-token-123")
                    ),
                ),
                3,
            )
            assert result.created
    async with db_engine.connect() as conn:
        row = await conn.execute(
            text("SELECT payload FROM action_event WHERE tenant_id=:t"), {"t": tid}
        )
        events = row.all()
        assert len(events) == 1
        assert "sensitive-proof" not in str(events)
