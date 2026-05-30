"""personal access tokens

Revision ID: 0031
Revises: 0030
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE event_status ADD VALUE IF NOT EXISTS 'resolved'")
    op.execute(
        """
        CREATE TABLE personal_access_tokens (
          id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id       uuid NOT NULL REFERENCES tenants(id),
          user_uc_uid     text NOT NULL,
          display_name    text NOT NULL,
          token_hash      bytea NOT NULL UNIQUE,
          last_4          text NOT NULL,
          scopes          text[] NOT NULL DEFAULT '{}',
          expires_at      timestamptz,
          created_at      timestamptz NOT NULL DEFAULT now(),
          last_used_at    timestamptz,
          revoked_at      timestamptz
        )
        """
    )
    op.execute(
        "CREATE INDEX pat_user_idx ON personal_access_tokens(tenant_id, user_uc_uid) "
        "WHERE revoked_at IS NULL"
    )
    op.execute(
        "CREATE INDEX pat_hash_idx ON personal_access_tokens(token_hash) "
        "WHERE revoked_at IS NULL"
    )
    op.execute("ALTER TABLE personal_access_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE personal_access_tokens FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY personal_access_tokens_select ON personal_access_tokens FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (tenant_id = current_tenant_id())
        """
    )
    op.execute(
        """
        CREATE POLICY personal_access_tokens_modify ON personal_access_tokens FOR ALL
            TO contact_ops_app
            USING (tenant_id = current_tenant_id())
            WITH CHECK (tenant_id = current_tenant_id())
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION lookup_personal_access_token(p_hash bytea)
        RETURNS TABLE (
            id uuid,
            tenant_id uuid,
            user_uc_uid text,
            display_name text,
            scopes text[],
            expires_at timestamptz,
            revoked_at timestamptz
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public
        AS $$
            SELECT id, tenant_id, user_uc_uid, display_name, scopes, expires_at, revoked_at
            FROM personal_access_tokens
            WHERE token_hash = p_hash
            LIMIT 1
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mark_personal_access_token_used(p_id uuid)
        RETURNS void
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public
        AS $$
            UPDATE personal_access_tokens
            SET last_used_at = now()
            WHERE id = p_id
        $$;
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON personal_access_tokens TO contact_ops_app")
    op.execute("GRANT SELECT ON personal_access_tokens TO contact_ops_ro")
    op.execute("GRANT SELECT ON personal_access_tokens TO contact_ops_audit")
    op.execute("GRANT EXECUTE ON FUNCTION lookup_personal_access_token(bytea) TO contact_ops_app")
    op.execute("GRANT EXECUTE ON FUNCTION mark_personal_access_token_used(uuid) TO contact_ops_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS mark_personal_access_token_used(uuid)")
    op.execute("DROP FUNCTION IF EXISTS lookup_personal_access_token(bytea)")
    op.execute("DROP POLICY IF EXISTS personal_access_tokens_modify ON personal_access_tokens")
    op.execute("DROP POLICY IF EXISTS personal_access_tokens_select ON personal_access_tokens")
    op.execute("ALTER TABLE IF EXISTS personal_access_tokens NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE IF EXISTS personal_access_tokens DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE IF EXISTS personal_access_tokens")
    _drop_status_partial_indexes()
    op.execute("ALTER TABLE action_event ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TABLE action_event ALTER COLUMN status TYPE text USING status::text")
    op.execute("DROP TYPE event_status")
    op.execute(
        "CREATE TYPE event_status AS ENUM "
        "('proposed','approved','applied','rejected','reverted','superseded')"
    )
    op.execute(
        "ALTER TABLE action_event ALTER COLUMN status TYPE event_status "
        "USING status::event_status"
    )
    op.execute("ALTER TABLE action_event ALTER COLUMN status SET DEFAULT 'proposed'")
    _create_status_partial_indexes()


def _drop_status_partial_indexes() -> None:
    for index_name in (
        "ae_status_idx",
        "ae_inbox_idx",
        "ae_low_conf_idx",
        "ae_inbox_recent_idx",
        "ae_cooldown_recent_idx",
        "ae_snoozed_until_idx",
        "ae_calibration_walk_idx",
    ):
        op.execute(f"DROP INDEX IF EXISTS {index_name}")


def _create_status_partial_indexes() -> None:
    op.execute(
        """
        CREATE INDEX ae_status_idx
        ON action_event(tenant_id, status)
        WHERE status IN ('proposed','approved')
        """
    )
    op.execute(
        """
        CREATE INDEX ae_inbox_idx
        ON action_event(tenant_id, proposed_at DESC)
        WHERE status = 'proposed' AND confidence BETWEEN 0.75 AND 0.95
        """
    )
    op.execute(
        """
        CREATE INDEX ae_low_conf_idx
        ON action_event(tenant_id, proposed_at DESC)
        WHERE status = 'proposed' AND confidence < 0.75
        """
    )
    op.execute(
        """
        CREATE INDEX ae_inbox_recent_idx
        ON action_event(tenant_id, proposed_at DESC)
        WHERE status = 'proposed'
        """
    )
    op.execute(
        """
        CREATE INDEX ae_cooldown_recent_idx
        ON action_event(tenant_id, aggregate_id, proposed_at DESC)
        WHERE status = 'proposed'
        """
    )
    op.execute(
        """
        CREATE INDEX ae_snoozed_until_idx
        ON action_event(snoozed_until)
        WHERE status = 'proposed' AND snoozed_until IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX ae_calibration_walk_idx
        ON action_event (proposed_at DESC, tenant_id)
        WHERE actor_type = 'agent'
          AND status IN ('approved', 'applied', 'rejected', 'reverted')
        """
    )
