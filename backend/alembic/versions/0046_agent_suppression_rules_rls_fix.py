"""agent_suppression_rules: fix RLS policies and add FORCE ROW LEVEL SECURITY

Revision ID: 0046
Revises: 0045
Create Date: 2026-07-18

Bug: migration 0024 installed RLS policies on agent_suppression_rules that
reference current_setting('app.current_tenant', true)::uuid — a GUC the app
never sets (the app only sets app.tenant_id). The canonical helper
current_tenant_id() reads app.tenant_id. As a result the policies always
evaluated tenant_id = NULL, making every row invisible and blocking all writes.

This migration:
1. DROP the two broken policies.
2. CREATE new policies using current_tenant_id() (matching every other RLS
   policy in the codebase).
3. ALTER TABLE ... FORCE ROW LEVEL SECURITY (was ENABLE-only, meaning the
   table owner — the migration superuser — bypassed RLS entirely).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0046"
down_revision: Union[str, None] = "0045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Drop the broken policies from 0024.
    op.execute("DROP POLICY IF EXISTS agent_suppression_rules_tenant_select ON agent_suppression_rules")
    op.execute("DROP POLICY IF EXISTS agent_suppression_rules_tenant_modify ON agent_suppression_rules")

    # 2. Create new policies using current_tenant_id() — the canonical helper
    #    that reads app.tenant_id (the GUC the app actually sets).
    op.execute("""
        CREATE POLICY agent_suppression_rules_tenant_select
            ON agent_suppression_rules
            FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (tenant_id = current_tenant_id())
    """)
    op.execute("""
        CREATE POLICY agent_suppression_rules_tenant_modify
            ON agent_suppression_rules
            FOR ALL
            TO contact_ops_app
            USING (tenant_id = current_tenant_id())
            WITH CHECK (tenant_id = current_tenant_id())
    """)

    # 3. FORCE RLS so even the table owner is subject to policies.
    op.execute("ALTER TABLE agent_suppression_rules FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Restore the original (broken) state from migration 0024.
    op.execute("ALTER TABLE agent_suppression_rules NO FORCE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS agent_suppression_rules_tenant_modify ON agent_suppression_rules")
    op.execute("DROP POLICY IF EXISTS agent_suppression_rules_tenant_select ON agent_suppression_rules")

    # Re-create the original policies using the wrong GUC name.
    op.execute("""
        CREATE POLICY agent_suppression_rules_tenant_select
            ON agent_suppression_rules
            FOR SELECT
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("""
        CREATE POLICY agent_suppression_rules_tenant_modify
            ON agent_suppression_rules
            FOR ALL
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
