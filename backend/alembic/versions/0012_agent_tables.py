"""agent registry and calibration tables

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # agent_registry
    op.execute("""
        CREATE TABLE agent_registry (
            id                    uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            name                  text NOT NULL,
            version               text NOT NULL,
            owning_system         text NOT NULL DEFAULT 'contact-ops',
            owning_user_id        uuid,
            owning_tenant_id      uuid REFERENCES tenants(id),
            scope_mode            text NOT NULL DEFAULT 'shared' CHECK (scope_mode IN ('per_tenant','shared')),
            visibility            visibility_scope NOT NULL DEFAULT 'private',
            declared_capabilities text[] NOT NULL DEFAULT '{}',
            current_trust_tier    trust_tier NOT NULL DEFAULT 'propose_only',
            trust_tier_overrides  jsonb NOT NULL DEFAULT '{}',
            calibration_history   jsonb NOT NULL DEFAULT '[]',
            created_at            timestamptz NOT NULL DEFAULT now(),
            updated_at            timestamptz NOT NULL DEFAULT now(),
            deprecated_at         timestamptz,
            UNIQUE (name, version, owning_tenant_id)
        )
    """)
    op.execute("CREATE INDEX agent_registry_name_idx ON agent_registry(name)")
    op.execute("CREATE INDEX agent_registry_active_idx ON agent_registry(name) WHERE deprecated_at IS NULL")

    # agent_calibration
    op.execute("""
        CREATE TABLE agent_calibration (
            agent_id          uuid NOT NULL REFERENCES agent_registry(id) ON DELETE CASCADE,
            event_type        text NOT NULL,
            bucket_start      timestamptz NOT NULL,
            proposed_count    integer NOT NULL DEFAULT 0,
            applied_count     integer NOT NULL DEFAULT 0,
            approved_count    integer NOT NULL DEFAULT 0,
            rejected_count    integer NOT NULL DEFAULT 0,
            reverted_count    integer NOT NULL DEFAULT 0,
            brier_score       real,
            ece               real,
            PRIMARY KEY (agent_id, event_type, bucket_start)
        )
    """)
    op.execute("""
        CREATE INDEX agent_calibration_recent_idx
            ON agent_calibration(agent_id, event_type, bucket_start DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_calibration")
    op.execute("DROP TABLE IF EXISTS agent_registry")
