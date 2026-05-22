"""merge tables: merge_history, person_alias, organization_alias

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # merge_history
    op.execute("""
        CREATE TABLE merge_history (
            id                      uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            tenant_id               uuid NOT NULL REFERENCES tenants(id),
            entity_type             text NOT NULL CHECK (entity_type IN ('person','organization')),
            kept_id                 uuid NOT NULL,
            removed_id              uuid NOT NULL,
            score                   numeric(5,4) NOT NULL,
            signals                 jsonb NOT NULL DEFAULT '{}',
            performed_at            timestamptz NOT NULL DEFAULT now(),
            performed_by_actor_id   uuid,
            performed_event_id      uuid REFERENCES action_event(event_id),
            reversible_until        timestamptz,
            reverted_at             timestamptz,
            reverted_event_id       uuid REFERENCES action_event(event_id)
        )
    """)
    op.execute("CREATE INDEX merge_history_kept_idx ON merge_history(entity_type, kept_id)")
    op.execute("CREATE INDEX merge_history_removed_idx ON merge_history(entity_type, removed_id)")
    op.execute("CREATE INDEX merge_history_reversible_idx ON merge_history(reversible_until) WHERE reverted_at IS NULL")

    # person_alias
    op.execute("""
        CREATE TABLE person_alias (
            alias_id                 uuid PRIMARY KEY,
            current_canonical_id     uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
            merged_at_event_id       uuid NOT NULL REFERENCES action_event(event_id),
            created_at               timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX person_alias_canonical_idx ON person_alias(current_canonical_id)")

    # organization_alias
    op.execute("""
        CREATE TABLE organization_alias (
            alias_id                 uuid PRIMARY KEY,
            current_canonical_id     uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            merged_at_event_id       uuid NOT NULL REFERENCES action_event(event_id),
            created_at               timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX org_alias_canonical_idx ON organization_alias(current_canonical_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS organization_alias")
    op.execute("DROP TABLE IF EXISTS person_alias")
    op.execute("DROP TABLE IF EXISTS merge_history")
