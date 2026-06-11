"""Add tenants.plan_tier for entitlement (P-00075 §4 monetization).

Per-tenant subscription tier gating MCP tools by compute cost (free < pro <
enterprise; policy lives in contact_ops.mcp.entitlement). New self-served
tenants default to 'free' via the server_default; EXISTING tenants predate the
gate and are grandfathered to 'enterprise' here so nothing they use today
breaks when ENTITLEMENT_ENFORCED is later flipped on.

Revision ID: 0041
Revises: 0040
Create Date: 2026-06-11
"""

from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE tenants
        ADD COLUMN plan_tier text NOT NULL DEFAULT 'free'
        """
    )
    op.execute(
        """
        ALTER TABLE tenants
        ADD CONSTRAINT tenants_plan_tier_check
        CHECK (plan_tier IN ('free', 'pro', 'enterprise'))
        """
    )
    # Grandfather every pre-existing tenant to enterprise (the migration role
    # bypasses RLS, same as 0039's owner-membership seed). New rows created
    # after this migration get 'free' from the server_default.
    op.execute("UPDATE tenants SET plan_tier = 'enterprise'")


def downgrade() -> None:
    op.execute("ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_plan_tier_check")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS plan_tier")
