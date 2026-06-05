"""RLS isolation for the ingest seam (P-00059, requirement e) — sync-only.

Kept in its OWN module (no async tests) and modeled exactly on
test_p4_isolation_rls.py, because mixing the session-scoped ASYNC test engine with
a SYNC runtime-role engine in one file contends on the single-container connection
pool. Here, under the non-bypassrls ``contact_ops_runtime`` role with the tenant
GUC bound, Postgres POLICIES — not just a query's WHERE clause — are what isolate,
so this genuinely proves a caller bound to tenant A cannot read-or-create into
tenant B through the ingested-person columns.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

A = "a1000000-0000-0000-0000-000000000001"  # tenant A
B = "b2000000-0000-0000-0000-000000000001"  # tenant B
PA = "a1100000-0000-0000-0000-000000000001"  # person owned by A
PB = "b2200000-0000-0000-0000-000000000001"  # person a B-caller tries to forge


@pytest.fixture(scope="module")
def rt_engine(postgres_container: str):
    """Sync engine; ensures the contact_ops_runtime login role exists once."""
    sync_url = postgres_container.replace("postgresql+asyncpg://", "postgresql://")
    eng = create_engine(sync_url, future=True)
    with eng.begin() as c:
        c.execute(
            text(
                "DO $$ BEGIN CREATE ROLE contact_ops_runtime LOGIN PASSWORD 'rtpw' "
                "NOSUPERUSER NOBYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END $$"
            )
        )
        c.execute(text("GRANT contact_ops_app TO contact_ops_runtime"))
    yield eng
    eng.dispose()


def _as_runtime(c, tenant_id: str) -> None:
    c.execute(text("SET LOCAL ROLE contact_ops_runtime"))
    c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})


def test_ingest_rls_cross_tenant_read_and_write_blocked(rt_engine):
    with rt_engine.connect() as c:
        trans = c.begin()
        try:
            # Seed two tenants + an A-owned person carrying ingest provenance
            # (the migration-0040 columns), as superuser (RLS-bypassing).
            c.execute(
                text(
                    """
                    INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                        qdrant_namespace, garage_bucket_prefix) VALUES
                     (:a,'ingest-rls-a','brand','A',:a,'a-ns','a-bkt'),
                     (:b,'ingest-rls-b','brand','B',:b,'b-ns','b-bkt')
                    """
                ),
                {"a": A, "b": B},
            )
            c.execute(
                text(
                    "INSERT INTO persons (id, display_name, canonical_owner_tenant_id, "
                    "source_label, external_ref, consent_basis) "
                    "VALUES (:pa,'A Lead',:a,'NorthwindLeads','LV-A','opted_in')"
                ),
                {"pa": PA, "a": A},
            )

            # --- Bound to tenant B under the RLS-subject role ---
            _as_runtime(c, B)

            # (read) A's ingested person is invisible to B.
            seen = c.execute(
                text("SELECT count(*) FROM persons WHERE id = :pa"), {"pa": PA}
            ).scalar()
            assert seen == 0, "tenant B must NOT see tenant A's ingested person"

            # Also invisible via the external_ref dedup columns specifically.
            seen_ref = c.execute(
                text(
                    "SELECT count(*) FROM persons "
                    "WHERE source_label='NorthwindLeads' AND external_ref='LV-A'"
                )
            ).scalar()
            assert seen_ref == 0, "external_ref lookup must not cross the tenant wall"

            # (write) a B-bound INSERT forging tenant A ownership is RLS-blocked.
            sp = c.begin_nested()
            blocked = False
            try:
                c.execute(
                    text(
                        "INSERT INTO persons (id, display_name, canonical_owner_tenant_id, "
                        "source_label, external_ref, consent_basis) "
                        "VALUES (:pb,'forged',:a,'NorthwindLeads','LV-B','opted_in')"
                    ),
                    {"pb": PB, "a": A},
                )
                sp.commit()
            except Exception as exc:  # noqa: BLE001 — expected RLS WITH CHECK violation
                sp.rollback()
                blocked = "row-level security" in str(exc).lower()
            assert blocked, "B-bound INSERT into tenant A must be RLS-blocked"

            c.execute(text("RESET ROLE"))

            # --- Bound to tenant A: owner still sees its own ingested person ---
            _as_runtime(c, A)
            mine = c.execute(
                text("SELECT count(*) FROM persons WHERE id = :pa"), {"pa": PA}
            ).scalar()
            assert mine == 1, "owner tenant A must still see its own ingested person"
            c.execute(text("RESET ROLE"))
        finally:
            trans.rollback()
