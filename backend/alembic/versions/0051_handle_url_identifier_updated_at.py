"""add updated_at + touch_updated triggers to im_handles / urls / identifiers

Revision ID: 0051
Revises: 0050
Create Date: 2026-09-15

Additive: adds an ``updated_at timestamptz NOT NULL DEFAULT now()`` column to the
three contact-point tables and wires the existing ``touch_updated()`` trigger
(created in 0015) onto each, so every UPDATE bumps ``updated_at``. This gives the
im_handle / url / identifier update tools the same optimistic-concurrency etag
the email/phone tables already have (their etag is ``updated_at``).

No data backfill is needed: the column DEFAULT ``now()`` populates existing rows,
and the trigger only fires on future UPDATEs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES: tuple[str, ...] = ("im_handles", "urls", "identifiers")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        # touch_updated() (created in 0015) bumps updated_at (+ etag where present)
        # on every BEFORE UPDATE. im_handles/urls/identifiers have no etag column,
        # so it only refreshes updated_at here.
        op.execute(
            f"""
            CREATE TRIGGER trg_touch_updated_{table}
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION touch_updated()
            """
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_touch_updated_{table} ON {table}")
        op.drop_column(table, "updated_at")
