"""grant UPDATE on action_event to contact_ops_app

Revision ID: 0038
Revises: 0037
Create Date: 2026-06-07

Migration 0017 did ``REVOKE UPDATE, DELETE ON action_event FROM contact_ops_app``
for an append-only audit intent. That intent is contradicted by the shipped code:
the proposal/inbox lifecycle performs in-place UPDATEs on action_event in ~14 call
sites (proposal_emit stamps idempotency_key/decision_payload; inbox_mutations does
status transitions approve/reject/snooze; dedup_agent / quality_filter update
status). It was masked until 2026-05-30 because the app connected as
contact_ops_admin (ALL privileges); the RLS cutover that evening moved the app to
the least-privilege contact_ops_runtime role (member of contact_ops_app), at which
point every connector pull / import / inbox decision began failing with
"permission denied for table action_event".

This restores UPDATE for the app role so the lifecycle works. (DELETE stays
revoked — the model supersedes rows via supersedes_event_id, never deletes.)
Note for future: if true append-only auditing is desired, the ~14 UPDATE sites
should be refactored to append superseding events instead, and this grant dropped.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("GRANT UPDATE ON action_event TO contact_ops_app")


def downgrade() -> None:
    op.execute("REVOKE UPDATE ON action_event FROM contact_ops_app")
