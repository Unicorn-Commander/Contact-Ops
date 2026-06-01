"""Phase 4.2 (migration 0036) isolation RLS adversarial suite.

Runs AS the ``contact_ops_runtime`` role (a ``contact_ops_app`` member, not a
superuser, not bypassrls) — the same role the prod app uses — because RLS is only
meaningfully testable from an RLS-subject role. The design's merge gate for the
isolation_mode dial; also guards against future regressions of the strict wall.

Proves, with synthetic strict(S) / open(O) / standard(D) tenants and cross
membership + acl_grant shares:
  * A  legit share VISIBLE   — an open viewer sees a shared standard person + child.
  * B  strict owner HIDDEN   — a strict-owned person + child is invisible to a
        sharee that holds BOTH a membership AND an acl_grant (owner fence wins).
  * C  strict viewer NARROWED — a strict viewer sees none of a shared standard
        person + child (no commingling into the clean room).
  * D  owner sees OWN        — a strict owner still sees its own person + child.
  * E  writes owner-only     — cross-tenant UPDATE touches 0 rows; cross-tenant
        INSERT is blocked by RLS.
Plus coverage: every tenant-FK table has RLS, and persons/organizations carry
both RESTRICTIVE fences.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

# Synthetic ids (all valid hex; slugs must be lowercase per tenants_slug_check).
D = "d0000000-0000-0000-0000-000000000001"   # standard
S = "50000000-0000-0000-0000-000000000001"   # strict
O = "f0000000-0000-0000-0000-000000000001"   # open
PD = "d1000000-0000-0000-0000-000000000001"  # person owned by D
PS = "51000000-0000-0000-0000-000000000001"  # person owned by S
ED = "d2000000-0000-0000-0000-000000000001"  # email of PD
ES = "52000000-0000-0000-0000-000000000001"  # email of PS


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
        INSERT INTO tenants (id, slug, kind, display_name, owner_user_id, qdrant_namespace, garage_bucket_prefix) VALUES
         (:d,'p4t-d','brand','D',:d,'d-ns','d-bkt'),
         (:s,'p4t-s','brand','S',:s,'s-ns','s-bkt'),
         (:o,'p4t-o','brand','O',:o,'o-ns','o-bkt')
        """
    ), {"d": D, "s": S, "o": O})
    c.execute(text("UPDATE tenants SET isolation_mode='strict' WHERE id=:s"), {"s": S})
    c.execute(text("UPDATE tenants SET isolation_mode='open' WHERE id=:o"), {"o": O})
    c.execute(text(
        "INSERT INTO persons (id, display_name, canonical_owner_tenant_id) VALUES (:pd,'Dee',:d),(:ps,'Esse',:s)"
    ), {"pd": PD, "ps": PS, "d": D, "s": S})
    c.execute(text(
        "INSERT INTO emails (id, person_id, address) VALUES (:ed,:pd,'REMOVED'),(:es,:ps,'REMOVED')"
    ), {"ed": ED, "es": ES, "pd": PD, "ps": PS})
    # pD and pS shared into O (membership + acl); pD shared into S (membership).
    c.execute(text(
        "INSERT INTO person_tenant_membership (person_id, tenant_id) VALUES (:pd,:o),(:ps,:o),(:pd,:s)"
    ), {"pd": PD, "ps": PS, "o": O, "s": S})
    c.execute(text(
        """
        INSERT INTO acl_grant (tenant_id, subject_kind, subject_id, grantee_tenant_id, scope) VALUES
         (:d,'person',:pd,:o,'read'),
         (:s,'person',:ps,:o,'read')
        """
    ), {"d": D, "s": S, "pd": PD, "ps": PS, "o": O})


def _as_runtime(c, tenant_id: str):
    c.execute(text("SET LOCAL ROLE contact_ops_runtime"))
    c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})


def _count(c, sql: str) -> int:
    return c.execute(text(sql)).scalar() or 0


def test_isolation_adversarial(rt_engine):
    with rt_engine.connect() as c:
        trans = c.begin()
        try:
            _seed(c)

            # --- A + B as O (open viewer holding membership AND acl on both) ---
            _as_runtime(c, O)
            assert _count(c, f"SELECT count(*) FROM persons WHERE id='{PD}'") == 1, "A: open viewer must see shared standard person"
            assert _count(c, f"SELECT count(*) FROM emails  WHERE id='{ED}'") == 1, "A: child must follow visible parent"
            assert _count(c, f"SELECT count(*) FROM persons WHERE id='{PS}'") == 0, "B: strict-owned person must be hidden (owner fence)"
            assert _count(c, f"SELECT count(*) FROM emails  WHERE id='{ES}'") == 0, "B: strict-owned child must be hidden"
            c.execute(text("RESET ROLE"))

            # --- C + D as S (strict viewer holding a membership on pD) ---
            _as_runtime(c, S)
            assert _count(c, f"SELECT count(*) FROM persons WHERE id='{PD}'") == 0, "C: strict viewer must not see a shared standard person"
            assert _count(c, f"SELECT count(*) FROM emails  WHERE id='{ED}'") == 0, "C: strict viewer must not see the shared child"
            assert _count(c, f"SELECT count(*) FROM persons WHERE id='{PS}'") == 1, "D: owner must still see its own strict person"
            assert _count(c, f"SELECT count(*) FROM emails  WHERE id='{ES}'") == 1, "D: owner must still see its own child"
            c.execute(text("RESET ROLE"))

            # --- E writes owner-only (as O) ---
            _as_runtime(c, O)
            res = c.execute(text(f"UPDATE persons SET display_name='hacked' WHERE id='{PD}'"))
            assert res.rowcount == 0, "E: cross-tenant UPDATE must touch 0 rows"
            # The expected RLS failure aborts its (sub)transaction, so isolate it
            # in a SAVEPOINT to keep the outer transaction usable afterwards.
            sp = c.begin_nested()
            insert_blocked = False
            try:
                c.execute(text(
                    "INSERT INTO persons (id, display_name, canonical_owner_tenant_id) "
                    "VALUES ('ff000000-0000-0000-0000-0000000000ff','evil',:d)"
                ), {"d": D})
                sp.commit()
            except Exception as e:  # noqa: BLE001 — expected RLS WITH CHECK violation
                sp.rollback()
                insert_blocked = "row-level security" in str(e).lower()
            assert insert_blocked, "E: cross-tenant INSERT must be RLS-blocked"
            c.execute(text("RESET ROLE"))
        finally:
            trans.rollback()


def test_coverage_no_tenant_fk_table_without_rls(rt_engine):
    """Every table with a FK to tenants must have RLS enabled (no silent gap)."""
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


def test_identity_tables_have_both_restrictive_fences(rt_engine):
    with rt_engine.connect() as c:
        for tbl in ("persons", "organizations"):
            fences = {
                r[0] for r in c.execute(text(
                    "SELECT policyname FROM pg_policies WHERE tablename=:t AND permissive='RESTRICTIVE'"
                ), {"t": tbl})
            }
            assert f"{tbl}_owner_strict_fence" in fences, f"{tbl} missing owner-side fence"
            assert f"{tbl}_viewer_strict_fence" in fences, f"{tbl} missing viewer-side fence"
