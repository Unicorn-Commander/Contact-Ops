"""phase 4.1b: user_tenant_membership + widened tenants_select

Revision ID: 0035
Revises: 0034
Create Date: 2026-06-01

Behavioral NO-OP at deploy. Adds the genuinely-missing user->tenant ACCESS
primitive (distinct from person_tenant_membership, which is a contact-VISIBILITY
lens) and widens tenants_select so a workspace switcher can list a user's other
memberships. With zero membership rows seeded, the widened policy's membership
branch matches nothing, so tenants_select is identical to today (home + children).

The actual switch ENFORCEMENT (assert_active_membership at every set-point) and
the switch endpoint land in 4.3; this migration only creates the table + RLS and
the read-widening. Aaron's own memberships are seeded as a SEPARATE, explicit
data step at deploy time (it needs his real uc_uid), NOT baked here, so this
migration stays deterministic and cleanly reversible.

RLS on the table itself uses two PERMISSIVE SELECT shapes (OR-combined):
  * utm_tenant_scope: a tenant's admins see/manage that tenant's rows.
  * utm_self: a user sees their OWN rows across tenants, via the app.uc_uid GUC
    that 4.0a binds on every session.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- the access primitive --------------------------------------------------
    op.execute("""
        CREATE TABLE user_tenant_membership (
            user_uc_uid      text NOT NULL,
            tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            role             text NOT NULL DEFAULT 'member'
                                CHECK (role IN ('viewer','member','manager','admin')),
            status           text NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active','suspended','revoked')),
            is_default       boolean NOT NULL DEFAULT false,
            consent_basis    consent_basis NULL,
            added_by         text NULL,
            added_at         timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            last_switched_at timestamptz NULL,
            PRIMARY KEY (user_uc_uid, tenant_id)
        )
    """)
    op.execute("CREATE INDEX utm_user_active_idx ON user_tenant_membership(user_uc_uid) WHERE status = 'active'")
    op.execute("CREATE INDEX utm_tenant_idx ON user_tenant_membership(tenant_id)")
    op.execute("ALTER TABLE user_tenant_membership ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_tenant_membership FORCE ROW LEVEL SECURITY")

    # (a) tenant-scoped management: a tenant's session manages its own rows.
    op.execute("""
        CREATE POLICY utm_tenant_scope ON user_tenant_membership FOR ALL
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (tenant_id = current_tenant_id())
            WITH CHECK (tenant_id = current_tenant_id())
    """)
    # (b) self-across-tenants read: a user sees their own membership rows, so the
    #     switcher (and the widened tenants_select below) can enumerate workspaces.
    op.execute("""
        CREATE POLICY utm_self ON user_tenant_membership FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (user_uc_uid = nullif(current_setting('app.uc_uid', true), ''))
    """)

    # --- widen tenants_select so the switcher can LIST other workspaces --------
    # No-op while no membership rows exist (the IN-subquery is empty). Reads
    # app.uc_uid inside RLS; no SECURITY DEFINER cross-tenant read, no infra-field
    # exposure (list_tenants projects only label fields for non-home workspaces).
    op.execute("DROP POLICY tenants_select ON tenants")
    op.execute("""
        CREATE POLICY tenants_select ON tenants FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (
                id = current_tenant_id()
                OR parent_tenant_id = current_tenant_id()
                OR id IN (
                    SELECT tenant_id FROM user_tenant_membership
                    WHERE user_uc_uid = nullif(current_setting('app.uc_uid', true), '')
                      AND status = 'active'
                )
            )
    """)


def downgrade() -> None:
    # Restore the verbatim 0015 tenants_select.
    op.execute("DROP POLICY tenants_select ON tenants")
    op.execute("""
        CREATE POLICY tenants_select ON tenants FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (id = current_tenant_id() OR parent_tenant_id = current_tenant_id())
    """)
    # Drop the table (its policies drop with it).
    op.execute("DROP TABLE IF EXISTS user_tenant_membership")
