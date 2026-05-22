"""acl_grant table for per-record sharing

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-22

Adds the ``acl_grant`` table that backs Phase 2 share_with_user / revoke_share
/ list_acl tools. RLS owned-by-tenant policy mirrors the rest of the schema.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE acl_grant (
            id                      uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            tenant_id               uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            subject_kind            text NOT NULL CHECK (subject_kind IN ('person','org')),
            subject_id              uuid NOT NULL,
            grantee_user_id         uuid,
            grantee_tenant_id       uuid REFERENCES tenants(id),
            scope                   text NOT NULL CHECK (scope IN ('read','write','merge')),
            reason                  text,
            granted_at              timestamptz NOT NULL DEFAULT now(),
            granted_by_actor_id     uuid,
            expires_at              timestamptz,
            revoked_at              timestamptz,
            revoked_by_actor_id     uuid,
            CONSTRAINT acl_grant_grantee_xor CHECK (
                (grantee_user_id IS NOT NULL)::int
                + (grantee_tenant_id IS NOT NULL)::int = 1
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX acl_grant_subject_idx"
        " ON acl_grant(subject_kind, subject_id) WHERE revoked_at IS NULL"
    )
    op.execute("CREATE INDEX acl_grant_tenant_idx ON acl_grant(tenant_id)")
    op.execute(
        "CREATE INDEX acl_grant_grantee_user_idx"
        " ON acl_grant(grantee_user_id) WHERE revoked_at IS NULL AND grantee_user_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX acl_grant_grantee_tenant_idx"
        " ON acl_grant(grantee_tenant_id)"
        " WHERE revoked_at IS NULL AND grantee_tenant_id IS NOT NULL"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX acl_grant_active_uniq ON acl_grant (
            tenant_id,
            subject_kind,
            subject_id,
            COALESCE(grantee_user_id, '00000000-0000-0000-0000-000000000000'::uuid),
            COALESCE(grantee_tenant_id, '00000000-0000-0000-0000-000000000000'::uuid),
            scope
        ) WHERE revoked_at IS NULL
        """
    )

    op.execute("ALTER TABLE acl_grant ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE acl_grant FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY acl_grant_select ON acl_grant FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (
                tenant_id = current_tenant_id()
                OR grantee_tenant_id = current_tenant_id()
            )
        """
    )
    op.execute(
        """
        CREATE POLICY acl_grant_modify ON acl_grant FOR ALL TO contact_ops_app
            USING (tenant_id = current_tenant_id())
            WITH CHECK (tenant_id = current_tenant_id())
        """
    )

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON acl_grant TO contact_ops_app"
    )
    op.execute("GRANT SELECT ON acl_grant TO contact_ops_ro, contact_ops_audit")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS acl_grant_modify ON acl_grant")
    op.execute("DROP POLICY IF EXISTS acl_grant_select ON acl_grant")
    op.execute("ALTER TABLE acl_grant NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE acl_grant DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE IF EXISTS acl_grant")
