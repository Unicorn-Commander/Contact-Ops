"""notes log

Revision ID: 0029
Revises: 0028
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE notes (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL REFERENCES tenants(id),
            target_type text NOT NULL CHECK (target_type IN ('user','person','org')),
            target_id   text NOT NULL,
            author_type text NOT NULL CHECK (author_type IN ('user','agent')),
            author_id   text NOT NULL,
            text        text NOT NULL,
            source      jsonb,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX notes_target_idx "
        "ON notes(tenant_id, target_type, target_id, created_at DESC)"
    )
    op.execute("ALTER TABLE notes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE notes FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY notes_select ON notes FOR SELECT
        USING (tenant_id = current_tenant_id())
        """
    )
    op.execute(
        """
        CREATE POLICY notes_modify ON notes FOR ALL TO contact_ops_app
        USING (tenant_id = current_tenant_id())
        WITH CHECK (tenant_id = current_tenant_id())
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON notes TO contact_ops_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notes")
