"""tags and consent_records

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # tags
    op.execute("""
        CREATE TABLE tags (
            id          uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            slug        text NOT NULL,
            display     text NOT NULL,
            color       text,
            description text,
            parent_tag_id uuid REFERENCES tags(id),
            created_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, slug)
        )
    """)
    op.execute("CREATE INDEX tags_tenant_idx ON tags(tenant_id)")

    # consent_records
    op.execute("""
        CREATE TABLE consent_records (
            id                  uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            person_id           uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
            tenant_id           uuid NOT NULL REFERENCES tenants(id),
            purpose             text NOT NULL,
            basis               consent_basis NOT NULL,
            granted_at          timestamptz,
            withdrawn_at        timestamptz,
            evidence_asset_id   uuid REFERENCES media_assets(id),
            notes               text,
            created_at          timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX consent_person_idx ON consent_records(person_id)")
    op.execute("CREATE INDEX consent_purpose_idx ON consent_records(purpose)")
    op.execute("CREATE INDEX consent_active_idx ON consent_records(person_id, purpose) WHERE withdrawn_at IS NULL")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS consent_records")
    op.execute("DROP TABLE IF EXISTS tags")
