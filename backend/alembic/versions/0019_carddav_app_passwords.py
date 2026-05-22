"""carddav_app_passwords: per-device Basic-auth credential for CardDAV clients

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-22

CardDAV (RFC 6352) consumes HTTP Basic auth — iOS Contacts.app cannot
send bearer tokens. Rather than expose the user's Keycloak password,
Contact-Ops issues per-device "app passwords" stored only as bcrypt
hashes. Each password is shown once at generation time, then verified
by the CardDAV router against this table.

See Contact-Ops-MCP-Design.md §3 (System Topology) and
Contact-Ops-Phase-2-Codex-2-Prompt.md for the rationale and API surface.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE carddav_app_passwords (
            id                   uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            tenant_id            uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id              text NOT NULL,
            device_label         text NOT NULL,
            password_hash        text NOT NULL,
            last_4_chars         text NOT NULL,
            scopes               text[] NOT NULL DEFAULT ARRAY['carddav:read','carddav:write']::text[],
            created_at           timestamptz NOT NULL DEFAULT now(),
            created_by_actor_id  uuid,
            last_used_at         timestamptz,
            last_used_user_agent text,
            last_used_ip         inet,
            revoked_at           timestamptz,
            revoked_by_actor_id  uuid
        )
        """
    )
    op.execute(
        "CREATE INDEX carddav_app_passwords_user_active_idx "
        "ON carddav_app_passwords (user_id) WHERE revoked_at IS NULL"
    )
    op.execute(
        "CREATE INDEX carddav_app_passwords_tenant_idx "
        "ON carddav_app_passwords (tenant_id) WHERE revoked_at IS NULL"
    )

    op.execute("ALTER TABLE carddav_app_passwords ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE carddav_app_passwords FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY carddav_app_passwords_select ON carddav_app_passwords FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING (tenant_id = current_tenant_id())
        """
    )
    op.execute(
        """
        CREATE POLICY carddav_app_passwords_modify ON carddav_app_passwords FOR ALL
            TO contact_ops_app
            USING (tenant_id = current_tenant_id())
            WITH CHECK (tenant_id = current_tenant_id())
        """
    )

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON carddav_app_passwords TO contact_ops_app"
    )
    op.execute("GRANT SELECT ON carddav_app_passwords TO contact_ops_ro")
    op.execute("GRANT SELECT ON carddav_app_passwords TO contact_ops_audit")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS carddav_app_passwords_modify ON carddav_app_passwords")
    op.execute("DROP POLICY IF EXISTS carddav_app_passwords_select ON carddav_app_passwords")
    op.execute("ALTER TABLE IF EXISTS carddav_app_passwords NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE IF EXISTS carddav_app_passwords DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE IF EXISTS carddav_app_passwords")
