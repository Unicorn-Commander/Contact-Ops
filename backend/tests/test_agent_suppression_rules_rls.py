"""Agent suppression rules RLS — runtime-role adversarial test.

Verifies that the contact_ops_runtime role (NOBYPASSRLS, the prod app role)
can INSERT and SELECT suppression rules within its own tenant, and that
cross-tenant access is blocked by RLS.

This test must pass after migration 0046 has been applied.

ACCEPT: reject_proposal(mode='mute') works under the runtime role;
relforcerowsecurity=true; single linear alembic head.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

T1 = "10000000-0000-0000-0000-000000000001"
T2 = "20000000-0000-0000-0000-000000000002"
SUPPRESSED_AGENT = "test-agent"


@pytest.fixture(scope="module")
def rt_engine(postgres_container: str):
    """Sync engine; ensures the contact_ops_runtime login role exists once."""
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


def _seed(c) -> None:
    c.execute(text(
        """
        INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
            qdrant_namespace, garage_bucket_prefix) VALUES
         (:t1,'supp-t1','brand','Tenant 1',:t1,'t1-ns','t1-bkt'),
         (:t2,'supp-t2','brand','Tenant 2',:t2,'t2-ns','t2-bkt')
        """
    ), {"t1": T1, "t2": T2})


def _bind_tenant(c, tenant_id: str) -> None:
    c.execute(text("SET LOCAL ROLE contact_ops_runtime"))
    c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})


def _insert_rule(c, tenant_id: str) -> uuid.UUID:
    """Insert one suppression rule and return its id."""
    row = c.execute(
        text(
            """
            INSERT INTO agent_suppression_rules
                (tenant_id, agent_slug, aggregate_type, created_by)
            VALUES (CAST(:t AS uuid), :agent, 'person', CAST(:t AS uuid))
            RETURNING id
            """
        ),
        {"t": tenant_id, "agent": SUPPRESSED_AGENT},
    ).scalar_one()
    return uuid.UUID(str(row))


def test_runtime_role_insert_and_select_own_tenant(rt_engine):
    """Runtime role can INSERT a rule in its own tenant and SELECT it back."""
    with rt_engine.connect() as c:
        trans = c.begin()
        try:
            _seed(c)
            _bind_tenant(c, T1)
            rule_id = _insert_rule(c, T1)

            rows = c.execute(
                text(
                    "SELECT id, agent_slug FROM agent_suppression_rules WHERE id = CAST(:id AS uuid)"
                ),
                {"id": str(rule_id)},
            ).fetchall()
            assert len(rows) == 1, "must see own rule"
            assert rows[0][1] == SUPPRESSED_AGENT
        finally:
            trans.rollback()


def test_runtime_role_cross_tenant_invisible(rt_engine):
    """A rule in T2 must be invisible when bound to T1."""
    with rt_engine.connect() as c:
        trans = c.begin()
        try:
            _seed(c)
            _insert_rule(c, T1)
            t2_rule_id = _insert_rule(c, T2)

            _bind_tenant(c, T1)

            rows = c.execute(
                text(
                    "SELECT id FROM agent_suppression_rules WHERE id = CAST(:id AS uuid)"
                ),
                {"id": str(t2_rule_id)},
            ).fetchall()
            assert len(rows) == 0, "cross-tenant rule must be invisible"
        finally:
            trans.rollback()


def test_runtime_role_cross_tenant_insert_blocked(rt_engine):
    """RLS WITH CHECK must block an INSERT with mismatched tenant_id."""
    with rt_engine.connect() as c:
        trans = c.begin()
        try:
            _seed(c)
            _bind_tenant(c, T1)

            blocked = False
            sp = c.begin_nested()
            try:
                _insert_rule(c, T2)
                sp.commit()
            except Exception as e:  # noqa: BLE001
                sp.rollback()
                blocked = "row-level security" in str(e).lower()
            assert blocked, "cross-tenant INSERT must be RLS-blocked"
        finally:
            trans.rollback()


def test_table_has_force_rls(rt_engine):
    """agent_suppression_rules must have FORCE ROW LEVEL SECURITY enabled."""
    with rt_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT relforcerowsecurity FROM pg_class "
                "WHERE relname = 'agent_suppression_rules'"
            )
        ).scalar()
        assert row is True, "relforcerowsecurity must be true"


def test_coverage_included_in_rls_check(rt_engine):
    """The table must not appear in the gap list (no tenant-FK table without RLS)."""
    with rt_engine.connect() as c:
        gaps = c.execute(text(
            """
            SELECT array_agg(DISTINCT cl.relname) FROM pg_constraint con
            JOIN pg_class cl ON cl.oid = con.conrelid
            JOIN pg_class ref ON ref.oid = con.confrelid
            JOIN pg_namespace n ON n.oid = cl.relnamespace
            WHERE con.contype='f' AND ref.relname='tenants' AND n.nspname='public'
              AND NOT cl.relrowsecurity
            """
        )).scalar()
        assert gaps is None, f"tenant-FK tables without RLS: {gaps}"
