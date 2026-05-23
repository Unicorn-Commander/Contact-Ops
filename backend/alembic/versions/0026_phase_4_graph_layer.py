"""phase 4 graph layer

Revision ID: 0026
Revises: 0025
Create Date: 2026-05-23

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE tenants
        SET graph_mode = COALESCE(NULLIF(graph_mode, ''), 'per_org_graph'),
            graph_name = COALESCE(
                graph_name,
                'contact_ops__' || regexp_replace(lower(replace(slug, '-', '_')), '[^a-z0-9_]+', '_', 'g')
            )
        WHERE graph_name IS NULL OR graph_mode IS NULL OR graph_mode = ''
        """
    )
    op.execute("ALTER TABLE tenants ALTER COLUMN graph_mode SET DEFAULT 'per_org_graph'")

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS gso_status_created_idx
        ON graph_sync_outbox(status, created_at)
        """
    )

    op.execute(
        """
        CREATE TABLE graph_sync_dlq (
            id              uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            outbox_id       uuid NOT NULL UNIQUE REFERENCES graph_sync_outbox(id) ON DELETE CASCADE,
            tenant_id       uuid NOT NULL REFERENCES tenants(id),
            graph_name      text NOT NULL,
            entity_kind     text NOT NULL,
            entity_id       uuid NOT NULL,
            op              text NOT NULL,
            payload         jsonb NOT NULL,
            attempts        integer NOT NULL,
            last_error      text NOT NULL,
            promoted_at     timestamptz NOT NULL DEFAULT now(),
            replayed_at     timestamptz,
            created_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX graph_sync_dlq_tenant_idx ON graph_sync_dlq(tenant_id, promoted_at DESC)")
    op.execute("CREATE INDEX graph_sync_dlq_graph_idx ON graph_sync_dlq(graph_name)")

    op.execute("ALTER TABLE graph_sync_dlq ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE graph_sync_dlq FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY graph_sync_dlq_select ON graph_sync_dlq FOR SELECT
        USING (tenant_id = current_tenant_id())
        """
    )
    op.execute(
        """
        CREATE POLICY graph_sync_dlq_modify ON graph_sync_dlq FOR ALL TO contact_ops_app
        USING (tenant_id = current_tenant_id())
        WITH CHECK (tenant_id = current_tenant_id())
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS graph_sync_dlq_modify ON graph_sync_dlq")
    op.execute("DROP POLICY IF EXISTS graph_sync_dlq_select ON graph_sync_dlq")
    op.execute("DROP TABLE IF EXISTS graph_sync_dlq")
    op.execute("DROP INDEX IF EXISTS gso_status_created_idx")
