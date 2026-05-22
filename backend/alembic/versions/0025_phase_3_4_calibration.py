"""phase 3.4 calibration daemon: calibration_run_log + warning_streak column

Revision ID: 0025
Revises: 0024
Create Date: 2026-05-22

Schema additions Phase 3.4 needs that Foundation didn't ship:

* ``agent_trust.warning_streak`` SMALLINT — counts consecutive
  PSI>=0.10 drift-warning days; resets to 0 on stable, demotes on 3.
* ``calibration_run_log`` table — bounds the per-pass walk window
  (``last_run_at`` is the WHERE clause for the next pass) and
  records per-pass telemetry (posteriors_updated, drifts_evaluated,
  promotes_proposed, demotes_applied, fleet_revert_rate_pct).
* Indexes on action_event the daemon's daily walks need:
  - ``ae_actor_sub_idx`` for the per-agent grouping
  - ``ae_calibration_walk_idx`` partial index for status IN
    (approved/applied/rejected/reverted) AND actor_type='agent'

RLS: ``calibration_run_log`` is platform-tenant (no tenant_id column);
read access is granted to all ops roles for dashboard scrape.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    _extend_agent_trust()
    _create_calibration_run_log()
    _add_walk_indexes()


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ae_calibration_walk_idx")
    op.execute("DROP INDEX IF EXISTS ae_actor_sub_idx")
    op.execute("DROP TABLE IF EXISTS calibration_run_log")
    op.execute("ALTER TABLE agent_trust DROP COLUMN IF EXISTS warning_streak")


def _extend_agent_trust() -> None:
    op.execute(
        """
        ALTER TABLE agent_trust
            ADD COLUMN warning_streak SMALLINT NOT NULL DEFAULT 0
                CHECK (warning_streak >= 0)
        """
    )
    op.execute(
        "COMMENT ON COLUMN agent_trust.warning_streak IS "
        "'Consecutive daily PSI>=0.10 readings. Reset to 0 on stable; "
        "demotes the tier on the 3rd consecutive warning day.'"
    )


def _create_calibration_run_log() -> None:
    op.execute(
        """
        CREATE TABLE calibration_run_log (
            id                          UUID PRIMARY KEY,
            started_at                  TIMESTAMPTZ NOT NULL,
            ended_at                    TIMESTAMPTZ,
            posteriors_updated          INTEGER NOT NULL DEFAULT 0,
            drifts_evaluated            INTEGER NOT NULL DEFAULT 0,
            tier_promotes_proposed      INTEGER NOT NULL DEFAULT 0,
            tier_demotes_applied        INTEGER NOT NULL DEFAULT 0,
            fleet_revert_rate_pct       DOUBLE PRECISION,
            error_message               TEXT,
            CONSTRAINT calibration_run_log_started_before_ended
                CHECK (ended_at IS NULL OR ended_at >= started_at)
        )
        """
    )
    op.execute(
        "CREATE INDEX calibration_run_log_ended_at_idx "
        "ON calibration_run_log(ended_at DESC NULLS LAST)"
    )
    op.execute(
        "COMMENT ON TABLE calibration_run_log IS "
        "'One row per CalibrationDaemon pass. last_run_at bounds the next "
        "pass walk window. Platform-tenant table (no RLS).'"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON calibration_run_log TO contact_ops_app"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON calibration_run_log TO contact_ops_audit"
    )


def _add_walk_indexes() -> None:
    """Per-pass walk speed: the daemon's posteriors query groups by
    ``actor->>'sub'`` so an expression index helps. The partial index
    on status restricts to the four states posteriors care about."""
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ae_actor_sub_idx
            ON action_event ((actor->>'sub'))
            WHERE actor_type = 'agent'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ae_calibration_walk_idx
            ON action_event (proposed_at DESC, tenant_id)
            WHERE actor_type = 'agent'
              AND status IN ('approved', 'applied', 'rejected', 'reverted')
        """
    )
