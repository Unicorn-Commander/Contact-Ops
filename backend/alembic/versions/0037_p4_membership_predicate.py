"""phase 4.3a: user_is_active_member() predicate for the membership gate

Revision ID: 0037
Revises: 0036
Create Date: 2026-06-01

A SECURITY DEFINER predicate the app calls at tenant-binding set-points to enforce
that a human session has an active user_tenant_membership for its tenant (design
§4.4 — membership is the PRIMARY gate, not defense-in-depth). DEFINER because the
check must be correct regardless of the session's GUC/RLS state (the gate runs
while deciding whether to bind the tenant), and so it is reusable from the PAT and
Brigade validation paths too.

Additive and a behavioral NO-OP until MEMBERSHIP_GATE_ENFORCED is flipped on in
config (and memberships are seeded) — creating the function changes nothing on its
own.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION user_is_active_member(p_uc_uid text, p_tenant_id uuid)
        RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
        AS $$
            SELECT EXISTS (
                SELECT 1 FROM user_tenant_membership
                WHERE user_uc_uid = p_uc_uid
                  AND tenant_id = p_tenant_id
                  AND status = 'active'
            )
        $$
    """)
    op.execute(
        "GRANT EXECUTE ON FUNCTION user_is_active_member(text, uuid) "
        "TO contact_ops_app, contact_ops_ro, contact_ops_audit"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS user_is_active_member(text, uuid)")
