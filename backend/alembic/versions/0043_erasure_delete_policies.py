"""Tenant-scoped DELETE RLS policies for erasure (P-00075 compliance engine).

The runtime role (contact_ops_app, NOBYPASSRLS) had SELECT/INSERT/UPDATE policies
on persons + the erasure-orphan tables but NO permissive DELETE policy — so a
DELETE matched 0 rows under RLS and the Art.17 purge (and the older delete_person
tool) silently failed to remove anything. These add a permissive, tenant-scoped
DELETE policy mirroring each table's existing modify/select scope, so an erasure
can actually hard-delete within (and only within) the caller's tenant.

CASCADE children (emails/phones/addresses/etc.) need no policy — FK cascade
deletes run with referential-integrity privileges that bypass RLS once the parent
person row is deleted. field_provenance has no tenant column (scoped differently)
so its explicit purge stays a no-op for now — provenance metadata, not PII.

Revision ID: 0043
Revises: 0042
Create Date: 2026-06-13
"""

from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "0043"
down_revision: Union[str, None] = "0042"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


# (table, tenant column) — the runtime role may DELETE a row only when the row's
# tenant column equals the GUC-bound current_tenant_id().
_TABLES: tuple[tuple[str, str], ...] = (
    ("persons", "canonical_owner_tenant_id"),
    ("dedup_pair_candidates", "tenant_id"),
    ("facts", "tenant_id"),
    ("notes", "tenant_id"),
    ("merge_history", "tenant_id"),
)


def upgrade() -> None:
    for table, tcol in _TABLES:
        op.execute(
            f"""
            CREATE POLICY {table}_delete ON {table} FOR DELETE
                TO contact_ops_app
                USING ({tcol} = current_tenant_id())
            """
        )


def downgrade() -> None:
    for table, _ in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_delete ON {table}")
