"""Seed tenant-owner user memberships.

Revision ID: 0039
Revises: 0038
Create Date: 2026-06-10
"""

from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO user_tenant_membership (
            user_uc_uid,
            tenant_id,
            role,
            status,
            is_default,
            added_by,
            added_at,
            updated_at
        )
        SELECT
            owner_user_id::text,
            id,
            'admin',
            'active',
            true,
            'migration:0039_seed_owner_membership',
            now(),
            now()
        FROM tenants
        WHERE owner_user_id IS NOT NULL
        ON CONFLICT (user_uc_uid, tenant_id) DO UPDATE
        SET
            role = CASE
                WHEN user_tenant_membership.status = 'active'
                THEN user_tenant_membership.role
                ELSE EXCLUDED.role
            END,
            status = CASE
                WHEN user_tenant_membership.status = 'active'
                THEN user_tenant_membership.status
                ELSE EXCLUDED.status
            END,
            is_default = user_tenant_membership.is_default OR EXCLUDED.is_default,
            updated_at = now()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM user_tenant_membership
        WHERE added_by = 'migration:0039_seed_owner_membership'
          AND role = 'admin'
          AND status = 'active'
        """
    )
