"""federation: data_intel_link

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE data_intel_link (
            id                  uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            entity_type         text NOT NULL CHECK (entity_type IN ('person','organization')),
            contact_ops_id      uuid NOT NULL,
            data_intel_id       uuid NOT NULL,
            tenant_id           uuid NOT NULL REFERENCES tenants(id),
            publish_consent     boolean NOT NULL DEFAULT false,
            last_published_at   timestamptz,
            last_pulled_at      timestamptz,
            publish_etag        text,
            pull_etag           text,
            created_at          timestamptz NOT NULL DEFAULT now(),
            UNIQUE (entity_type, contact_ops_id),
            UNIQUE (entity_type, data_intel_id)
        )
    """)
    op.execute("CREATE INDEX dil_tenant_idx ON data_intel_link(tenant_id)")
    op.execute("CREATE INDEX dil_publish_pending_idx ON data_intel_link(tenant_id, last_published_at) WHERE publish_consent = true")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data_intel_link")
