"""tenancy tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # tenants
    op.execute("""
        CREATE TABLE tenants (
            id              uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            slug            text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
            kind            tenant_kind NOT NULL,
            display_name    text NOT NULL,
            owner_user_id   uuid NOT NULL,
            parent_tenant_id uuid REFERENCES tenants(id),
            branding        jsonb NOT NULL DEFAULT '{}',
            retention_policy jsonb NOT NULL DEFAULT '{}',
            hipaa_mode      boolean NOT NULL DEFAULT false,
            gdpr_mode       boolean NOT NULL DEFAULT false,
            data_residency  text NOT NULL DEFAULT 'us-east',
            data_intel_publish_consent boolean NOT NULL DEFAULT false,
            carddav_enabled boolean NOT NULL DEFAULT false,
            graph_mode      text NOT NULL DEFAULT 'per_org_graph'
                CHECK (graph_mode IN ('shared','per_org_graph','per_org_instance')),
            graph_name      text,
            qdrant_namespace text NOT NULL,
            garage_bucket_prefix text NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            deprecated_at   timestamptz,
            etag            text NOT NULL DEFAULT ''
        )
    """)
    op.execute("CREATE INDEX tenants_owner_idx ON tenants(owner_user_id)")
    op.execute("CREATE INDEX tenants_parent_idx ON tenants(parent_tenant_id)")
    op.execute("CREATE INDEX tenants_hipaa_idx ON tenants(id) WHERE hipaa_mode = true")
    op.execute("COMMENT ON COLUMN tenants.hipaa_mode IS 'When true, tenant is fenced from cross-tenant auto-merge and Data Intel publish.'")
    op.execute("COMMENT ON COLUMN tenants.graph_mode IS 'shared = global graph; per_org_graph = own graph in shared FalkorDB instance; per_org_instance = its own FalkorDB container.'")

    # tenant_keys
    op.execute("""
        CREATE TABLE tenant_keys (
            tenant_id uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE RESTRICT,
            kms_key_arn text NOT NULL,
            dek_wrapped bytea NOT NULL,
            rotated_at timestamptz NOT NULL DEFAULT now(),
            rotation_due_at timestamptz NOT NULL
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tenant_keys")
    op.execute("DROP TABLE IF EXISTS tenants")
