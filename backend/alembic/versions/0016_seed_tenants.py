"""seed default tenants

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-21

"""
import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_OWNER_USER_ID = "00000000-0000-0000-0000-000000000001"

TENANTS = [
    {
        "slug": "aaron-personal",
        "kind": "personal",
        "display_name": "Aaron Personal",
        "qdrant_namespace": "contact_ops__aaron-personal",
        "garage_bucket_prefix": "contact-ops-aaron-personal",
    },
    {
        "slug": "magic-unicorn-llc",
        "kind": "magic_unicorn_internal",
        "display_name": "Magic Unicorn LLC",
        "qdrant_namespace": "contact_ops__magic-unicorn-llc",
        "garage_bucket_prefix": "contact-ops-magic-unicorn-llc",
    },
    {
        "slug": "centerdeep",
        "kind": "brand",
        "display_name": "Centerdeep",
        "qdrant_namespace": "contact_ops__centerdeep",
        "garage_bucket_prefix": "contact-ops-centerdeep",
    },
]


def upgrade() -> None:
    owner_user_id = os.environ.get("SEED_OWNER_USER_ID", DEFAULT_OWNER_USER_ID)
    rows = [{**tenant, "owner_user_id": owner_user_id} for tenant in TENANTS]
    op.get_bind().execute(sa.text("""
        INSERT INTO tenants (
            slug,
            kind,
            display_name,
            owner_user_id,
            qdrant_namespace,
            garage_bucket_prefix
        )
        VALUES (
            :slug,
            :kind,
            :display_name,
            :owner_user_id,
            :qdrant_namespace,
            :garage_bucket_prefix
        )
        ON CONFLICT (slug) DO NOTHING
    """), rows)


def downgrade() -> None:
    op.execute("""
        DELETE FROM tenants
        WHERE slug IN ('aaron-personal', 'magic-unicorn-llc', 'centerdeep')
    """)
