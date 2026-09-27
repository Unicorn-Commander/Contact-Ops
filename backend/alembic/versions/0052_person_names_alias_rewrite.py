"""person_names: add rewrite_alias_person_id trigger

Revision ID: 0052
Revises: 0051
Create Date: 2026-09-15

Adds person_names to the alias-rewrite trigger set from 0015. The
`rewrite_alias_person_id()` BEFORE INSERT/UPDATE trigger already fires on
emails / phones / postal_addresses / identifiers / im_handles, rewriting a
person_id that is a merge alias to its current canonical id on write. person_names
was missing from that set, so a name written against an alias id would not be
redirected to the canonical person. Single trigger, no function change.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0052"
down_revision: Union[str, None] = "0051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_rewrite_alias_person_id_names
        BEFORE INSERT OR UPDATE ON person_names
        FOR EACH ROW EXECUTE FUNCTION rewrite_alias_person_id()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_rewrite_alias_person_id_names ON person_names")
