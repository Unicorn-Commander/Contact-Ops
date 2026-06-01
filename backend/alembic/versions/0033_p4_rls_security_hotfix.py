"""phase 4.0b security hotfix: RLS on previously-unprotected identity-derived tables

Revision ID: 0033
Revises: 0032
Create Date: 2026-05-31

Closes the SIX tables that had NO row-level security despite contact_ops_app
holding GRANT SELECT on them: ``sources``, ``tags``, ``topics``,
``tenant_keys``, ``person_alias``, ``organization_alias``. With RLS now actually
enforced at the runtime role (Phase 4.0a), these were a live cross-tenant read
hole. ``tenant_keys`` holds per-tenant wrapped DEKs + KMS ARNs (a secret-grade
leak), and the alias tables are a merge-lineage oracle. ``tenant_keys`` was NOT
in the design's audit list; a coverage cross-check (every tenant-FK table
lacking RLS, run on the proof DB) surfaced it.

Kept as a SEPARATE migration from the isolation-mode dial (0034+) on purpose, so
a feature rollback can never revert this security fix.

Scope:
  * sources / tags / topics / tenant_keys  -> standard tenant-equality RLS.
    ``sources.tenant_id`` is nullable, so NULL rows fail closed under
    ``= current_tenant_id()`` (the correct default). If a code path inserts
    NULL-tenant sources this would block that write; verified against the
    source-insert path before deploy. ``tenant_keys`` is 0-row and unreferenced
    by app code today, so its RLS is risk-free now and pre-empts a future leak.
  * person_alias / organization_alias -> owner-OR-membership RLS, scoped by
    walking to the owning canonical person/organization (these tables have no
    ``tenant_id``). The STRICT-mode fence for these is added later in 0036
    alongside the dial; this migration deliberately does NOT reference
    ``owner_isolation_mode`` (which does not exist until 0034).
  * crisis_ops_entity_link_select -> realign the TO clause from PUBLIC (0028) to
    contact_ops_app/ro/audit, matching every other ``_select`` policy. No-op for
    the runtime role, which is a member of contact_ops_app; pure hardening.
  * catalogue_contacts / catalogue_companies -> ``security_invoker = true``. These
    backward-compat views previously ran with their (superuser) owner's rights
    and so bypassed caller RLS. Confirmed via cross-repo grep (2026-05-31) to have
    zero consumers in- or out-of-repo, so this closes a latent hole with no
    behavioral effect on the app.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Tables that carry a tenant_id and just need standard tenant-equality RLS.
_TENANT_EQ_TABLES = ["sources", "tags", "topics", "tenant_keys"]

# (alias table, owning table, owning membership table, membership fk column)
_ALIAS_TABLES = [
    ("person_alias", "persons", "person_tenant_membership", "person_id"),
    ("organization_alias", "organizations", "organization_tenant_membership", "organization_id"),
]


def upgrade() -> None:
    # --- sources / tags / topics: tenant-equality RLS -------------------------
    for tbl in _TENANT_EQ_TABLES:
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {tbl}_select ON {tbl} FOR SELECT
                TO contact_ops_app, contact_ops_ro, contact_ops_audit
                USING (tenant_id = current_tenant_id())
        """)
        op.execute(f"""
            CREATE POLICY {tbl}_modify ON {tbl} FOR ALL TO contact_ops_app
                USING (tenant_id = current_tenant_id())
                WITH CHECK (tenant_id = current_tenant_id())
        """)

    # --- person_alias / organization_alias: owner-OR-membership RLS -----------
    # No tenant_id on these tables; scope by walking to the owning canonical row.
    # NOTE: the strict-mode fence is added in 0036 (it needs owner_isolation_mode
    # from 0034), so this policy is intentionally dial-free.
    for alias_tbl, owner_tbl, membership_tbl, membership_fk in _ALIAS_TABLES:
        op.execute(f"ALTER TABLE {alias_tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {alias_tbl} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {alias_tbl}_select ON {alias_tbl} FOR SELECT
                TO contact_ops_app, contact_ops_ro, contact_ops_audit
                USING (
                    EXISTS (
                        SELECT 1 FROM {owner_tbl} o
                        WHERE o.id = {alias_tbl}.current_canonical_id
                          AND (
                              o.canonical_owner_tenant_id = current_tenant_id()
                              OR EXISTS (
                                  SELECT 1 FROM {membership_tbl} m
                                  WHERE m.{membership_fk} = o.id
                                    AND m.tenant_id = current_tenant_id()
                                    AND m.visibility <> 'archived'
                              )
                          )
                    )
                )
        """)
        op.execute(f"""
            CREATE POLICY {alias_tbl}_modify ON {alias_tbl} FOR ALL TO contact_ops_app
                USING (
                    EXISTS (
                        SELECT 1 FROM {owner_tbl} o
                        WHERE o.id = {alias_tbl}.current_canonical_id
                          AND o.canonical_owner_tenant_id = current_tenant_id()
                    )
                )
                WITH CHECK (
                    EXISTS (
                        SELECT 1 FROM {owner_tbl} o
                        WHERE o.id = {alias_tbl}.current_canonical_id
                          AND o.canonical_owner_tenant_id = current_tenant_id()
                    )
                )
        """)

    # --- crisis_ops_entity_link_select: realign TO clause (was PUBLIC) ---------
    op.execute("DROP POLICY IF EXISTS crisis_ops_entity_link_select ON crisis_ops_entity_link")
    op.execute("""
        CREATE POLICY crisis_ops_entity_link_select ON crisis_ops_entity_link FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (tenant_id = current_tenant_id())
    """)

    # --- catalogue views: respect the caller's RLS ----------------------------
    op.execute("ALTER VIEW catalogue_contacts SET (security_invoker = true)")
    op.execute("ALTER VIEW catalogue_companies SET (security_invoker = true)")


def downgrade() -> None:
    op.execute("ALTER VIEW catalogue_companies RESET (security_invoker)")
    op.execute("ALTER VIEW catalogue_contacts RESET (security_invoker)")

    # Restore the original PUBLIC crisis select policy (verbatim 0028 shape).
    op.execute("DROP POLICY IF EXISTS crisis_ops_entity_link_select ON crisis_ops_entity_link")
    op.execute("""
        CREATE POLICY crisis_ops_entity_link_select ON crisis_ops_entity_link FOR SELECT
            USING (tenant_id = current_tenant_id())
    """)

    for alias_tbl, _owner_tbl, _membership_tbl, _membership_fk in _ALIAS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {alias_tbl}_modify ON {alias_tbl}")
        op.execute(f"DROP POLICY IF EXISTS {alias_tbl}_select ON {alias_tbl}")
        op.execute(f"ALTER TABLE {alias_tbl} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {alias_tbl} DISABLE ROW LEVEL SECURITY")

    for tbl in _TENANT_EQ_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {tbl}_modify ON {tbl}")
        op.execute(f"DROP POLICY IF EXISTS {tbl}_select ON {tbl}")
        op.execute(f"ALTER TABLE {tbl} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY")
