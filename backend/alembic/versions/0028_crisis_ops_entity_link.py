"""crisis-ops federation: replace per-role link tables with the generic entity link

Revision ID: 0028
Revises: 0027
Create Date: 2026-05-27

Phase 5.2 corrected the Crisis-Ops integration model. Crisis-Ops stores every
party in one generic ``entities`` table ``(case_id, entity_key, entity_type, ...)``,
not the per-role tables (``clients``/``witnesses``) that 0027 assumed. The two
per-role link tables it created (``crisis_ops_client_link`` /
``crisis_ops_witness_link``) are the wrong shape and are verified EMPTY, so this
migration drops them and adds ``crisis_ops_entity_link`` keyed on
``(case_slug, entity_key)``. Only opted-in ``people``/``organizations`` entities
get a row; a row with both contact-ops FKs NULL is local-only.

See ``~/Documents/Contact-Ops-Phase-5.2-Crisis-Ops-Revised-Design.md``.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enable_tenant_rls(table: str) -> None:
    """Standard tenant-scoped RLS, identical to the other ``*_link`` tables (0027)."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_select ON {table} FOR SELECT
        USING (tenant_id = current_tenant_id())
        """
    )
    op.execute(
        f"""
        CREATE POLICY {table}_modify ON {table} FOR ALL TO contact_ops_app
        USING (tenant_id = current_tenant_id())
        WITH CHECK (tenant_id = current_tenant_id())
        """
    )


def _create_entity_link() -> None:
    op.execute(
        """
        CREATE TABLE crisis_ops_entity_link (
            case_slug             text NOT NULL,
            entity_key            text NOT NULL,
            entity_type           text NOT NULL,
            contact_ops_person_id uuid REFERENCES persons(id),
            contact_ops_org_id    uuid REFERENCES organizations(id),
            link_state            text NOT NULL DEFAULT 'proposed'
                CHECK (link_state IN ('proposed','linked','rejected','dormant')),
            tenant_id             uuid NOT NULL REFERENCES tenants(id),
            created_at            timestamptz NOT NULL DEFAULT now(),
            last_synced_at        timestamptz,
            PRIMARY KEY (case_slug, entity_key),
            CONSTRAINT crisis_ops_entity_link_single_target CHECK (
                (contact_ops_person_id IS NOT NULL)::integer
                + (contact_ops_org_id IS NOT NULL)::integer <= 1
            )
        )
        """
    )
    op.execute("CREATE INDEX crisis_ops_entity_link_tenant_idx ON crisis_ops_entity_link(tenant_id)")
    op.execute(
        "CREATE INDEX crisis_ops_entity_link_person_idx "
        "ON crisis_ops_entity_link(contact_ops_person_id) WHERE contact_ops_person_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX crisis_ops_entity_link_org_idx "
        "ON crisis_ops_entity_link(contact_ops_org_id) WHERE contact_ops_org_id IS NOT NULL"
    )
    op.execute(
        "COMMENT ON TABLE crisis_ops_entity_link IS "
        "'Phase 5.2: per-entity identity link for opted-in Crisis-Ops people/organizations. "
        "Both contact-ops FKs NULL = local-only. Linkage is propose-only (link_state starts proposed).'"
    )
    _enable_tenant_rls("crisis_ops_entity_link")


def _create_person_link_table(table: str, source_column: str) -> None:
    """Re-create a 0027-shaped per-role person link table (used only by downgrade)."""
    op.execute(
        f"""
        CREATE TABLE {table} (
            {source_column}        uuid PRIMARY KEY,
            tenant_id              uuid NOT NULL REFERENCES tenants(id),
            contact_ops_person_id  uuid NOT NULL REFERENCES persons(id),
            source_identifier      text,
            created_at             timestamptz NOT NULL DEFAULT now(),
            last_synced_at         timestamptz,
            sync_state             text NOT NULL DEFAULT 'active'
                CHECK (sync_state IN ('active','dormant','conflicted')),
            UNIQUE (tenant_id, source_identifier)
        )
        """
    )
    op.execute(f"CREATE INDEX {table}_tenant_idx ON {table}(tenant_id)")
    op.execute(f"CREATE INDEX {table}_person_idx ON {table}(contact_ops_person_id)")
    _enable_tenant_rls(table)


def upgrade() -> None:
    # Verified empty in production; safe to drop (RLS policies drop with the table).
    op.execute("DROP TABLE IF EXISTS crisis_ops_client_link")
    op.execute("DROP TABLE IF EXISTS crisis_ops_witness_link")
    _create_entity_link()


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS crisis_ops_entity_link")
    # Restore the 0027 per-role link tables so up -> down returns to the 0027 schema.
    _create_person_link_table("crisis_ops_client_link", "crisis_ops_client_id")
    _create_person_link_table("crisis_ops_witness_link", "crisis_ops_witness_id")
