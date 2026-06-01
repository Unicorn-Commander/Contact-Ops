"""Phase 4.3 membership gate — the user_is_active_member predicate (migration 0037)
and the safety property that enforcement is OFF by default.

The predicate is SECURITY DEFINER so it must answer on its arguments regardless of
the session's bound GUCs / RLS state (the gate runs while deciding whether to bind).
The default-off test guards against a deploy silently locking everyone out before
memberships are seeded.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

A = "aaaaaaaa-0000-0000-0000-000000000001"
B = "bbbbbbbb-0000-0000-0000-000000000001"


@pytest.fixture(scope="module")
def rt_engine(postgres_container: str):
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


def test_user_is_active_member_predicate(rt_engine):
    with rt_engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text(
                "INSERT INTO tenants (id,slug,kind,display_name,owner_user_id,"
                "qdrant_namespace,garage_bucket_prefix) VALUES "
                "(:a,'mg-a','brand','A',:a,'a-ns','a-bkt'),"
                "(:b,'mg-b','brand','B',:b,'b-ns','b-bkt')"
            ), {"a": A, "b": B})
            c.execute(text(
                "INSERT INTO user_tenant_membership (user_uc_uid,tenant_id,role,status) "
                "VALUES ('uc-aaron',:a,'admin','active'),('uc-sus',:a,'member','suspended')"
            ), {"a": A})

            # Deliberately mismatched bound session: the DEFINER predicate must
            # ignore it and answer on its arguments.
            c.execute(text("SET LOCAL ROLE contact_ops_runtime"))
            c.execute(text("SELECT set_config('app.uc_uid','uc-other',true)"))
            c.execute(text("SELECT set_config('app.tenant_id',:b,true)"), {"b": B})

            def member(u: str, t: str) -> bool:
                return c.execute(
                    text("SELECT user_is_active_member(:u, CAST(:t AS uuid))"),
                    {"u": u, "t": t},
                ).scalar()

            assert member("uc-aaron", A) is True          # active member
            assert member("uc-aaron", B) is False         # not a member of B
            assert member("uc-other", A) is False         # not a member at all
            assert member("uc-sus", A) is False           # suspended != active
            c.execute(text("RESET ROLE"))
        finally:
            trans.rollback()


def test_membership_and_azp_enforcement_default_off():
    """Deploying the gate code must NOT enforce until explicitly flipped on."""
    import os

    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:y@localhost:5432/z")
    os.environ.setdefault("KEYCLOAK_ISSUER", "https://t.invalid/realms/uchub")
    os.environ.setdefault("ENV", "test")
    os.environ.setdefault("STANDALONE_MODE", "false")

    from contact_ops.core.config import Settings

    s = Settings()
    assert s.MEMBERSHIP_GATE_ENFORCED is False
    assert s.TENANT_CLAIM_AZP_ENFORCE is False
    assert "contact-ops-switch" in s.TENANT_CLAIM_ALLOWED_AZP
