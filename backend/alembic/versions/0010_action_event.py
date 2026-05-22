"""action_event (append-only audit log)

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # action_event — append-only event log
    op.execute("""
        CREATE TABLE action_event (
            event_id            uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            event_type          text NOT NULL,
            event_version       integer NOT NULL DEFAULT 1,
            tenant_id           uuid NOT NULL REFERENCES tenants(id),
            aggregate_type      entity_kind NOT NULL,
            aggregate_id        uuid NOT NULL,
            affected_ids        uuid[] NOT NULL DEFAULT '{}',
            payload             jsonb NOT NULL,
            actor               jsonb NOT NULL,
            actor_type          actor_type NOT NULL,
            human_authority     uuid,
            confidence          numeric(4,3),
            evidence            jsonb NOT NULL DEFAULT '{}',
            rationale           text,
            status              event_status NOT NULL DEFAULT 'proposed',
            proposed_at         timestamptz NOT NULL DEFAULT now(),
            applied_at          timestamptz,
            approved_by         uuid,
            reverted_by_event_id uuid,
            supersedes_event_id uuid,
            valid_from          timestamptz NOT NULL DEFAULT now(),
            valid_to            timestamptz,
            content_hash        bytea NOT NULL,
            prev_event_hash     bytea,
            signature           bytea,
            CONSTRAINT ae_human_no_conf CHECK (actor_type <> 'human' OR confidence IS NULL),
            CONSTRAINT ae_window_ok CHECK (valid_window_ok(valid_from, valid_to))
        )
    """)
    op.execute("REVOKE UPDATE, DELETE ON action_event FROM contact_ops_app")
    op.execute("GRANT INSERT, SELECT ON action_event TO contact_ops_app")
    op.execute("GRANT INSERT, SELECT ON action_event TO contact_ops_audit")
    op.execute("""
        ALTER TABLE action_event
            ADD CONSTRAINT ae_reverted_by_fk
            FOREIGN KEY (reverted_by_event_id) REFERENCES action_event(event_id) ON DELETE SET NULL
    """)
    op.execute("""
        ALTER TABLE action_event
            ADD CONSTRAINT ae_supersedes_fk
            FOREIGN KEY (supersedes_event_id) REFERENCES action_event(event_id) ON DELETE SET NULL
    """)

    op.execute("CREATE INDEX ae_tenant_time_idx ON action_event(tenant_id, proposed_at DESC)")
    op.execute("CREATE INDEX ae_aggregate_idx ON action_event(aggregate_type, aggregate_id, proposed_at DESC)")
    op.execute("CREATE INDEX ae_status_idx ON action_event(tenant_id, status) WHERE status IN ('proposed','approved')")
    op.execute("CREATE INDEX ae_event_type_idx ON action_event(event_type, proposed_at DESC)")
    op.execute("CREATE INDEX ae_actor_gin ON action_event USING gin (actor jsonb_path_ops)")
    op.execute("CREATE INDEX ae_evidence_gin ON action_event USING gin (evidence jsonb_path_ops)")
    op.execute("CREATE INDEX ae_human_authority_idx ON action_event(human_authority)")
    op.execute("CREATE INDEX ae_inbox_idx ON action_event(tenant_id, proposed_at DESC) WHERE status = 'proposed' AND confidence BETWEEN 0.75 AND 0.95")
    op.execute("CREATE INDEX ae_low_conf_idx ON action_event(tenant_id, proposed_at DESC) WHERE status = 'proposed' AND confidence < 0.75")
    op.execute("CREATE INDEX ae_affected_gin ON action_event USING gin (affected_ids)")
    op.execute("CREATE INDEX ae_supersedes_idx ON action_event(supersedes_event_id) WHERE supersedes_event_id IS NOT NULL")
    op.execute("COMMENT ON TABLE action_event IS 'Append-only event log. UPDATE/DELETE revoked from app role.'")

    # FK: field_provenance.set_by_event_id → action_event.event_id
    op.execute("""
        ALTER TABLE field_provenance
            ADD CONSTRAINT fp_event_fk FOREIGN KEY (set_by_event_id) REFERENCES action_event(event_id)
    """)
    op.execute("ALTER TABLE field_provenance ALTER COLUMN set_by_event_id SET NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE field_provenance ALTER COLUMN set_by_event_id DROP NOT NULL")
    op.execute("ALTER TABLE field_provenance DROP CONSTRAINT IF EXISTS fp_event_fk")
    op.execute("ALTER TABLE action_event DROP CONSTRAINT IF EXISTS ae_supersedes_fk")
    op.execute("ALTER TABLE action_event DROP CONSTRAINT IF EXISTS ae_reverted_by_fk")
    op.execute("REVOKE INSERT, SELECT ON action_event FROM contact_ops_app")
    op.execute("REVOKE INSERT, SELECT ON action_event FROM contact_ops_audit")
    op.execute("GRANT UPDATE, DELETE ON action_event TO contact_ops_app")
    op.execute("DROP TABLE IF EXISTS action_event")
