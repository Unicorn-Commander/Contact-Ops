"""connector configs and runs

Revision ID: 0030
Revises: 0029
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE connector_configs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id),
            provider text NOT NULL CHECK (provider IN ('icloud','m365','gmail')),
            display_name text NOT NULL,
            configured_by text NOT NULL,
            status text NOT NULL
                CHECK (status IN ('configured','disconnected','error'))
                DEFAULT 'configured',
            last_error text,
            encrypted_payload bytea NOT NULL,
            configured_at timestamptz NOT NULL DEFAULT now(),
            last_pull_at timestamptz,
            last_pull_summary jsonb,
            UNIQUE(tenant_id, provider)
        )
        """
    )
    op.execute("CREATE INDEX connector_configs_tenant_idx ON connector_configs(tenant_id)")
    op.execute(
        """
        CREATE TABLE connector_runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id),
            connector_id uuid NOT NULL REFERENCES connector_configs(id) ON DELETE CASCADE,
            started_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz,
            status text NOT NULL
                CHECK (status IN ('running','succeeded','failed','partial'))
                DEFAULT 'running',
            parsed_count int DEFAULT 0,
            proposed_count int DEFAULT 0,
            deduped_count int DEFAULT 0,
            skipped_count int DEFAULT 0,
            error_message text,
            triggered_by text NOT NULL CHECK (triggered_by IN ('user','schedule','agent'))
        )
        """
    )
    op.execute(
        "CREATE INDEX connector_runs_connector_idx "
        "ON connector_runs(connector_id, started_at DESC)"
    )
    for table in ("connector_configs", "connector_runs"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_select ON {table} FOR SELECT
            USING (tenant_id = current_tenant_id())
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_modify ON {table} FOR ALL TO contact_ops_app
            USING (tenant_id = current_tenant_id())
            WITH CHECK (tenant_id = current_tenant_id())
            """
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO contact_ops_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS connector_runs")
    op.execute("DROP TABLE IF EXISTS connector_configs")
