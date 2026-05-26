"""phase 5 ecosystem federation links

Revision ID: 0027
Revises: 0026
Create Date: 2026-05-26

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERSON_LINK_TABLES = {
    "listing_ops_link": "listing_ops_user_id",
    "crisis_ops_client_link": "crisis_ops_client_id",
    "crisis_ops_witness_link": "crisis_ops_witness_id",
    "project_ops_user_link": "project_ops_user_id",
    "meeting_ops_speaker_link": "meeting_ops_speaker_id",
    "stable_workspace_member_link": "stable_workspace_member_id",
}


def _create_person_link_table(table: str, source_column: str) -> None:
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
    op.execute(
        f"CREATE INDEX {table}_person_idx ON {table}(contact_ops_person_id)"
    )
    _enable_tenant_rls(table)


def _enable_tenant_rls(table: str) -> None:
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


def upgrade() -> None:
    for table, source_column in PERSON_LINK_TABLES.items():
        _create_person_link_table(table, source_column)

    op.execute(
        """
        CREATE TABLE crisis_ops_opposing_counsel_link (
            crisis_ops_opposing_counsel_id uuid PRIMARY KEY,
            tenant_id                      uuid NOT NULL REFERENCES tenants(id),
            contact_ops_person_id          uuid NOT NULL REFERENCES persons(id),
            case_slug                      text NOT NULL,
            source_identifier              text,
            created_at                     timestamptz NOT NULL DEFAULT now(),
            last_synced_at                 timestamptz,
            sync_state                     text NOT NULL DEFAULT 'active'
                CHECK (sync_state IN ('active','dormant','conflicted')),
            UNIQUE (tenant_id, case_slug, source_identifier)
        )
        """
    )
    op.execute(
        "CREATE INDEX crisis_ops_opposing_counsel_link_tenant_idx "
        "ON crisis_ops_opposing_counsel_link(tenant_id)"
    )
    op.execute(
        "CREATE INDEX crisis_ops_opposing_counsel_link_person_idx "
        "ON crisis_ops_opposing_counsel_link(contact_ops_person_id)"
    )
    _enable_tenant_rls("crisis_ops_opposing_counsel_link")

    op.execute(
        """
        CREATE TABLE crisis_ops_judge_link (
            crisis_ops_judge_id    uuid PRIMARY KEY,
            tenant_id              uuid NOT NULL REFERENCES tenants(id),
            contact_ops_person_id  uuid NOT NULL REFERENCES persons(id),
            case_slug              text NOT NULL,
            source_identifier      text,
            created_at             timestamptz NOT NULL DEFAULT now(),
            last_synced_at         timestamptz,
            sync_state             text NOT NULL DEFAULT 'active'
                CHECK (sync_state IN ('active','dormant','conflicted')),
            UNIQUE (tenant_id, case_slug, source_identifier)
        )
        """
    )
    op.execute("CREATE INDEX crisis_ops_judge_link_tenant_idx ON crisis_ops_judge_link(tenant_id)")
    op.execute(
        "CREATE INDEX crisis_ops_judge_link_person_idx "
        "ON crisis_ops_judge_link(contact_ops_person_id)"
    )
    _enable_tenant_rls("crisis_ops_judge_link")

    op.execute(
        """
        CREATE TABLE project_ops_client_link (
            project_ops_client_id  uuid PRIMARY KEY,
            tenant_id              uuid NOT NULL REFERENCES tenants(id),
            contact_ops_org_id     uuid REFERENCES organizations(id),
            contact_ops_person_id  uuid REFERENCES persons(id),
            source_identifier      text,
            created_at             timestamptz NOT NULL DEFAULT now(),
            last_synced_at         timestamptz,
            sync_state             text NOT NULL DEFAULT 'active'
                CHECK (sync_state IN ('active','dormant','conflicted')),
            CHECK (
                (contact_ops_org_id IS NOT NULL)::integer
                + (contact_ops_person_id IS NOT NULL)::integer = 1
            ),
            UNIQUE (tenant_id, source_identifier)
        )
        """
    )
    op.execute("CREATE INDEX project_ops_client_link_tenant_idx ON project_ops_client_link(tenant_id)")
    op.execute(
        "CREATE INDEX project_ops_client_link_org_idx "
        "ON project_ops_client_link(contact_ops_org_id)"
    )
    op.execute(
        "CREATE INDEX project_ops_client_link_person_idx "
        "ON project_ops_client_link(contact_ops_person_id)"
    )
    _enable_tenant_rls("project_ops_client_link")

    op.execute(
        """
        CREATE TABLE brigade_agent_link (
            brigade_agent_id              text PRIMARY KEY,
            tenant_id                     uuid NOT NULL REFERENCES tenants(id),
            contact_ops_agent_registry_id uuid NOT NULL REFERENCES agent_registry(id),
            source_identifier             text,
            created_at                    timestamptz NOT NULL DEFAULT now(),
            last_synced_at                timestamptz,
            sync_state                    text NOT NULL DEFAULT 'active'
                CHECK (sync_state IN ('active','dormant','conflicted')),
            UNIQUE (tenant_id, source_identifier)
        )
        """
    )
    op.execute("CREATE INDEX brigade_agent_link_tenant_idx ON brigade_agent_link(tenant_id)")
    op.execute(
        "CREATE INDEX brigade_agent_link_agent_idx "
        "ON brigade_agent_link(contact_ops_agent_registry_id)"
    )
    _enable_tenant_rls("brigade_agent_link")

    op.execute(
        """
        CREATE TABLE consumer_webhook_subscription (
            consumer_app_id   text NOT NULL,
            tenant_id         uuid NOT NULL REFERENCES tenants(id),
            url               text NOT NULL,
            event_kinds       text[] NOT NULL,
            hmac_secret       text NOT NULL,
            active            boolean NOT NULL DEFAULT true,
            created_at        timestamptz NOT NULL DEFAULT now(),
            last_delivered_at timestamptz,
            PRIMARY KEY (consumer_app_id, tenant_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX consumer_webhook_subscription_app_idx "
        "ON consumer_webhook_subscription(consumer_app_id)"
    )
    op.execute(
        "CREATE INDEX consumer_webhook_subscription_tenant_idx "
        "ON consumer_webhook_subscription(tenant_id)"
    )
    _enable_tenant_rls("consumer_webhook_subscription")


def downgrade() -> None:
    for table in (
        "consumer_webhook_subscription",
        "brigade_agent_link",
        "project_ops_client_link",
        "crisis_ops_judge_link",
        "crisis_ops_opposing_counsel_link",
        "stable_workspace_member_link",
        "meeting_ops_speaker_link",
        "project_ops_user_link",
        "crisis_ops_witness_link",
        "crisis_ops_client_link",
        "listing_ops_link",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")

