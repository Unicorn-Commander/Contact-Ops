"""Workspace agent master switch (Agent Command Center kill-switch).

Adds tenants.agents_paused (+ reason / at / by) so an admin can stop the whole
agent fleet for a tenant in one move. BaseAgent.execute reads agents_paused at
the same chokepoint as the per-agent circuit breaker and refuses to run when it
is true. Distinct from tenants.auto_approve_disabled (migration 0032), which only
suppresses auto-APPLY; this stops agents from running at all.

agents_paused is NOT NULL DEFAULT false so existing tenants are unaffected (the
migration pauses no one). The reason/at/by columns are nullable audit detail the
Command Center surfaces; the authoritative who/when/why also lands in
prov_activities when the flag is flipped via set_agents_paused.

No new RLS policy is needed: the existing tenants_modify policy (FOR ALL TO
contact_ops_app, migration 0015) already lets the app role update its own tenant
row, and the isolation-ratchet trigger (0034) fires only on isolation_mode /
hipaa_mode, not on these columns.

Revision ID: 0044
Revises: 0043
Create Date: 2026-06-24
"""

from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "0044"
down_revision: Union[str, None] = "0043"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE tenants
            ADD COLUMN agents_paused boolean NOT NULL DEFAULT false,
            ADD COLUMN agents_paused_reason text,
            ADD COLUMN agents_paused_at timestamptz,
            ADD COLUMN agents_paused_by uuid
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE tenants
            DROP COLUMN IF EXISTS agents_paused_by,
            DROP COLUMN IF EXISTS agents_paused_at,
            DROP COLUMN IF EXISTS agents_paused_reason,
            DROP COLUMN IF EXISTS agents_paused
        """
    )
