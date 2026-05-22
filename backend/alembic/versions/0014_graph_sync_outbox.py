"""graph_sync_outbox

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE graph_sync_outbox (
            id            uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            entity_kind   text NOT NULL CHECK (entity_kind IN (
                'person','organization','edge:works_at','edge:has_email','edge:has_phone','edge:has_address',
                'edge:knows','edge:family_of','edge:reports_to','edge:counsel_for','edge:witness_for',
                'edge:party_to','edge:mentioned_in','edge:duplicate_of','edge:other')),
            entity_id     uuid NOT NULL,
            op            text NOT NULL CHECK (op IN ('upsert','delete')),
            payload       jsonb NOT NULL,
            tenant_id     uuid NOT NULL REFERENCES tenants(id),
            graph_name    text NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            attempts      integer NOT NULL DEFAULT 0,
            last_attempt_at timestamptz,
            last_error    text,
            status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','dlq'))
        )
    """)
    op.execute("CREATE INDEX gso_pending_idx ON graph_sync_outbox(created_at) WHERE status = 'pending'")
    op.execute("CREATE INDEX gso_dlq_idx ON graph_sync_outbox(graph_name) WHERE status = 'dlq'")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS graph_sync_outbox")
