"""interactions and topics

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # interactions
    op.execute("""
        CREATE TABLE interactions (
            id                uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            tenant_id         uuid NOT NULL REFERENCES tenants(id),
            person_id         uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
            channel           contact_channel NOT NULL,
            direction         direction NOT NULL,
            occurred_at       timestamptz NOT NULL,
            duration_seconds  integer,
            message_uri       text,
            thread_uri        text,
            topic_ids         uuid[] NOT NULL DEFAULT '{}',
            sentiment         real,
            source_id         uuid REFERENCES sources(id),
            created_at        timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX interactions_person_time_idx ON interactions(person_id, occurred_at DESC)")
    op.execute("CREATE INDEX interactions_tenant_time_idx ON interactions(tenant_id, occurred_at DESC)")
    op.execute("CREATE INDEX interactions_channel_idx ON interactions(channel, occurred_at DESC)")
    op.execute("CREATE INDEX interactions_topic_gin ON interactions USING gin (topic_ids)")
    op.execute("CREATE INDEX interactions_message_uri_idx ON interactions(message_uri)")

    # topics
    op.execute("""
        CREATE TABLE topics (
            id              uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            tenant_id       uuid NOT NULL REFERENCES tenants(id),
            label           text NOT NULL,
            embedding       vector(1024),
            parent_topic_id uuid REFERENCES topics(id),
            created_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, label)
        )
    """)
    op.execute("CREATE INDEX topics_embed_hnsw ON topics USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS topics")
    op.execute("DROP TABLE IF EXISTS interactions")
