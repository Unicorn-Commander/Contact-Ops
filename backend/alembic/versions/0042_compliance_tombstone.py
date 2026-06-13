"""Two-phase erasure tombstone columns (P-00075 compliance engine).

Right-to-erasure (Art.17) uses a two-phase model: an erasure request TOMBSTONES
the person (sets tombstoned_at + purge_after = now + grace) and redacts direct
PII, then the retention sweep HARD-PURGES it once purge_after passes — giving a
real undo window and guarding a mistaken/abusive request. These columns back
that model + the sweep's selection query. Both nullable (a normal live person
has neither set). DORMANT: nothing writes them until COMPLIANCE_ENGINE_ENABLED.

This is the canonical 0042 — the migration coordinator for this wave (other
buckets deliberately took zero-migration paths so the head stays linear).

Revision ID: 0042
Revises: 0041
Create Date: 2026-06-13
"""

from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "0042"
down_revision: Union[str, None] = "0041"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE persons
          ADD COLUMN tombstoned_at timestamptz,
          ADD COLUMN purge_after  timestamptz
        """
    )
    # Partial index: the retention sweep scans for tombstoned rows whose grace
    # window has elapsed. Indexing only tombstoned rows keeps it tiny (the vast
    # majority of persons are live and excluded).
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_persons_purge_after
          ON persons (purge_after)
          WHERE tombstoned_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_persons_purge_after")
    op.execute("ALTER TABLE persons DROP COLUMN IF EXISTS purge_after")
    op.execute("ALTER TABLE persons DROP COLUMN IF EXISTS tombstoned_at")
