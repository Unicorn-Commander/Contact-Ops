"""persons.category: PERSONAL (owner network) or PROSPECT (sourced lead).

Revision ID: 0053
Revises: 0052
Create Date: 2026-09-29

The enum type is created and committed before any statement uses it. Postgres
will not accept a new enum label in the same transaction that created it, and
this migration runs inside Alembic's single transaction.

Backfill is tag-scoped and idempotent: a membership carrying
``sourced-prospect`` becomes PROSPECT. Downgrade sets those same rows back to
PERSONAL before the column and type are dropped.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0053"
down_revision: Union[str, None] = "0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Applied tag slug (tag_person lowercases and turns spaces into hyphens).
SOURCED_PROSPECT_TAG = "sourced-prospect"

_TAGGED_SOURCED_PROSPECT = """
EXISTS (
    SELECT 1
    FROM person_tenant_membership AS m
    CROSS JOIN LATERAL unnest(COALESCE(m.tags, ARRAY[]::text[])) AS tag
    WHERE m.person_id = p.id
      AND lower(btrim(tag)) = 'sourced-prospect'
)"""

# Idempotent: a second run updates zero rows.
BACKFILL_PROSPECT_SQL = (
    """
UPDATE persons AS p
SET category = 'PROSPECT'::contact_category
WHERE p.category IS DISTINCT FROM 'PROSPECT'::contact_category
  AND """
    + _TAGGED_SOURCED_PROSPECT
)

# Downgrade restores only the rows the backfill could have changed.
RESTORE_PERSONAL_FOR_SOURCED_PROSPECT_SQL = (
    """
UPDATE persons AS p
SET category = 'PERSONAL'::contact_category
WHERE p.category IS DISTINCT FROM 'PERSONAL'::contact_category
  AND """
    + _TAGGED_SOURCED_PROSPECT
)


def upgrade() -> None:
    # Commit the new type before ADD COLUMN / UPDATE cast to it.
    with op.get_context().autocommit_block():
        op.execute(
            """
            DO $$
            BEGIN
                CREATE TYPE contact_category AS ENUM ('PERSONAL', 'PROSPECT');
            EXCEPTION
                WHEN duplicate_object THEN NULL;
            END
            $$
            """
        )
    op.execute(
        """
        ALTER TABLE persons
            ADD COLUMN IF NOT EXISTS category contact_category NOT NULL DEFAULT 'PERSONAL'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS persons_owner_category_idx
            ON persons (canonical_owner_tenant_id, category)
        """
    )
    op.execute(BACKFILL_PROSPECT_SQL)


def downgrade() -> None:
    op.execute(RESTORE_PERSONAL_FOR_SOURCED_PROSPECT_SQL)
    op.execute("DROP INDEX IF EXISTS persons_owner_category_idx")
    op.execute("ALTER TABLE persons DROP COLUMN IF EXISTS category")
    op.execute("DROP TYPE IF EXISTS contact_category")
