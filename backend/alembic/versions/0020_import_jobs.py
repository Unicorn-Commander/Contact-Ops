"""import jobs and merge candidates

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE source_type ADD VALUE IF NOT EXISTS 'ios_contacts_export'")
    op.execute("ALTER TYPE source_type ADD VALUE IF NOT EXISTS 'nextcloud'")
    op.execute(
        """
        CREATE TABLE import_jobs (
            id uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            source text NOT NULL,
            source_uri text,
            status text NOT NULL CHECK (
                status IN ('queued','running','completed','failed','cancelled')
            ),
            progress numeric(5,4) NOT NULL DEFAULT 0,
            stats jsonb NOT NULL DEFAULT jsonb_build_object(
                'created', 0,
                'merged', 0,
                'candidates', 0,
                'errors', 0
            ),
            error_log jsonb NOT NULL DEFAULT '[]',
            started_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz,
            cancel_requested_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX import_jobs_tenant_status_idx ON import_jobs (tenant_id, status)")
    op.execute(
        """
        CREATE TABLE merge_candidates (
            id uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            source_person_id uuid REFERENCES persons(id) ON DELETE CASCADE,
            candidate_person_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            source_type source_type NOT NULL,
            source_record_id text,
            confidence numeric(4,3) NOT NULL DEFAULT 0.850,
            reason text NOT NULL,
            status text NOT NULL DEFAULT 'pending',
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX merge_candidates_tenant_status_idx ON merge_candidates (tenant_id, status)"
    )
    for table in ["import_jobs", "merge_candidates"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_select ON {table} FOR SELECT
                TO contact_ops_app, contact_ops_ro, contact_ops_audit
                USING (tenant_id = current_tenant_id())
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_modify ON {table} FOR ALL
                TO contact_ops_app
                USING (tenant_id = current_tenant_id())
                WITH CHECK (tenant_id = current_tenant_id())
            """
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO contact_ops_app")
        op.execute(f"GRANT SELECT ON {table} TO contact_ops_ro")
        op.execute(f"GRANT SELECT ON {table} TO contact_ops_audit")


def downgrade() -> None:
    for table in ["merge_candidates", "import_jobs"]:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_modify ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_select ON {table}")
        op.execute(f"ALTER TABLE IF EXISTS {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE IF EXISTS {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE IF EXISTS merge_candidates")
    op.execute("DROP TABLE IF EXISTS import_jobs")
