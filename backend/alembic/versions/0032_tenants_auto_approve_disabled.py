"""tenant auto approval opt-out

Revision ID: 0032
Revises: 0031
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE tenants
        ADD COLUMN auto_approve_disabled boolean NOT NULL DEFAULT false
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS auto_approve_disabled")
