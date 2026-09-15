"""typed names: person_names (legal/preferred/nickname/aka/former/maiden/stage)

Revision ID: 0040
Revises: 0039
Create Date: 2026-09-15

Adds the person_names table — typed alternate names on a person. Distinct from
persons.nicknames (kept in sync for vCard NICKNAME) and from person_alias (a
merge-redirect table, NOT alternate names). Search + dedup match on every
name_type.

RLS mirrors persons (same parent-visibility form as the other person-child
tables in 0036): SELECT is a bare parent-visibility EXISTS (RLS-subject, so it
inherits the parent's gated+fenced isolation), a RESTRICTIVE parent-visibility
fence, and owner-only MODIFY.

Backfill: seeds one person_names row (name_type='nickname') per existing
persons.nicknames entry so existing data is immediately searchable by nickname.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _parent_visible() -> str:
    return "EXISTS (SELECT 1 FROM persons p WHERE p.id = person_names.person_id)"


def _owner_scope() -> str:
    return (
        "EXISTS ("
        "SELECT 1 FROM persons p WHERE p.id = person_names.person_id "
        "AND p.canonical_owner_tenant_id = current_tenant_id()"
        ")"
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE name_type AS ENUM (
            'legal', 'preferred', 'nickname', 'aka', 'former', 'maiden', 'stage'
        )
        """
    )
    op.execute(
        """
        CREATE TABLE person_names (
            id          uuid PRIMARY KEY DEFAULT uuidv7_generate(),
            person_id   uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
            name_type   name_type NOT NULL,
            value       text NOT NULL,
            given_name  text,
            family_name text,
            source_id   uuid,
            confidence  numeric(4,3) NOT NULL DEFAULT 1.000,
            valid_from  date,
            valid_to    date,
            is_primary  boolean NOT NULL DEFAULT false,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX person_names_person_idx ON person_names(person_id, name_type, lower(value))"
    )
    op.execute("CREATE INDEX person_names_value_lookup_idx ON person_names(lower(value), name_type)")
    op.execute(
        "CREATE UNIQUE INDEX person_names_uniq ON person_names(person_id, name_type, lower(value))"
    )

    # RLS — mirrors persons (see 0036 child-table form).
    op.execute("ALTER TABLE person_names ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE person_names FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY person_names_select ON person_names FOR SELECT
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING ({_parent_visible()})
        """
    )
    op.execute(
        f"""
        CREATE POLICY person_names_parent_fence ON person_names AS RESTRICTIVE FOR ALL
            TO contact_ops_app, contact_ops_ro, contact_ops_audit
            USING ({_parent_visible()})
        """
    )
    op.execute(
        f"""
        CREATE POLICY person_names_modify ON person_names FOR ALL TO contact_ops_app
            USING ({_owner_scope()})
            WITH CHECK ({_owner_scope()})
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON person_names TO contact_ops_app")

    # Backfill nickname person_names from persons.nicknames.
    op.execute(
        """
        INSERT INTO person_names (person_id, name_type, value, is_primary)
        SELECT p.id, 'nickname', n, false
        FROM persons p
        CROSS JOIN LATERAL unnest(p.nicknames) AS n
        WHERE p.nicknames IS NOT NULL
          AND cardinality(p.nicknames) > 0
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS person_names")
    op.execute("DROP TYPE IF EXISTS name_type")
