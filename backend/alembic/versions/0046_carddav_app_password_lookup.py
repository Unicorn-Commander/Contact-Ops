"""carddav app-password auth lookup under RLS

Revision ID: 0046
Revises: 0045
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION lookup_carddav_app_passwords(p_user_id text)
        RETURNS TABLE (
            app_password_id uuid,
            tenant_id uuid,
            user_id text,
            password_hash text,
            scopes text[],
            tenant_slug text,
            tenant_hipaa boolean
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT
                cap.id AS app_password_id,
                cap.tenant_id,
                cap.user_id,
                cap.password_hash,
                cap.scopes,
                t.slug AS tenant_slug,
                t.hipaa_mode AS tenant_hipaa
            FROM carddav_app_passwords AS cap
            JOIN tenants AS t ON t.id = cap.tenant_id
            WHERE cap.user_id = p_user_id
              AND cap.revoked_at IS NULL
              AND user_is_active_member(p_user_id, cap.tenant_id)
            ORDER BY cap.created_at DESC
        $$;
        """
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION lookup_carddav_app_passwords(text) TO contact_ops_app"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS lookup_carddav_app_passwords(text)")
