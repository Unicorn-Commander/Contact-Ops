"""dedup blocking scale: statement_timeout guard + indexable blocking keys

Revision ID: 0045
Revises: 0044
Create Date: 2026-06-27

Three production-safety + scale fixes for the Dedup Agent's blocking stage,
prompted by a real incident: a 50k-contact tenant ran a stage1 blocking query
for ~6 hours, pegging two Postgres cores. Two root causes, both fixed here.

1. statement_timeout guard on contact_ops_runtime (the agent/worker role).
   A blocking query on a freshly bulk-imported tenant (e.g. a new user's first
   CardDAV sync) can pick a nested-loop / seq-scan plan before autovacuum has
   analyzed the new rows, and run for hours. A 120s per-statement cap turns that
   into a clean failure (logged, retried next tick) instead of a database-pegging
   runaway. 120s is far above any healthy agent query; after the blocking-key
   indexes below, even large tenants block in well under a second. The primary
   guard is applied in code (engine connect_args in agent_tasks.py, which needs
   no DB privilege); this role-level setting is defense-in-depth and degrades
   gracefully if the migration role cannot ALTER the runtime role.

2. Indexable blocking keys. stage1 blocking keys 3 (dmetaphone(family_name) +
   first initial of given_name) and 4 (email-domain + soundex(family_name))
   previously fetched a per-tenant Cartesian product of persons and filtered in
   Python, which is O(contacts^2). The rewritten blocking.py pushes the phonetic
   equality into SQL; these expression indexes make those joins index-driven.
   All functions used (dmetaphone, soundex, split_part, lower, left, upper) are
   IMMUTABLE (verified against the live Postgres) so the expression indexes are
   valid. fuzzystrmatch was enabled in 0022.

3. Tighter autovacuum analyze cadence on persons/emails so the planner keeps
   fresh statistics after bulk imports (defense-in-depth for fix 1's root cause:
   the runaway plan came from stale stats on just-inserted rows).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0045"
down_revision: Union[str, None] = "0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Role-level per-statement timeout (defense-in-depth; the in-code engine
    #    guard is primary). Degrade gracefully if the migration role lacks the
    #    privilege to ALTER the runtime role, or the role is absent in this env.
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE 'ALTER ROLE contact_ops_runtime SET statement_timeout = ''120s''';
        EXCEPTION
            WHEN insufficient_privilege OR undefined_object THEN
                RAISE NOTICE 'skipping ALTER ROLE contact_ops_runtime statement_timeout (%); relying on engine-level guard', SQLERRM;
        END $$;
        """
    )

    # 2. Indexable blocking keys (fuzzystrmatch enabled in 0022).
    #    Key 3: dmetaphone(family_name) + first initial of given_name, per tenant.
    op.execute(
        "CREATE INDEX IF NOT EXISTS persons_dmeta_block_idx "
        "ON persons (canonical_owner_tenant_id, dmetaphone(family_name), "
        "upper(left(given_name, 1))) "
        "WHERE family_name IS NOT NULL AND given_name IS NOT NULL"
    )
    #    Key 4: soundex(family_name) per tenant, joined on shared email domain.
    op.execute(
        "CREATE INDEX IF NOT EXISTS persons_soundex_block_idx "
        "ON persons (canonical_owner_tenant_id, soundex(family_name)) "
        "WHERE family_name IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS emails_domain_idx "
        "ON emails (split_part(lower(address::text), '@', 2))"
    )

    # 3. Keep planner stats fresh after bulk imports (root cause of runaway plans).
    op.execute("ALTER TABLE persons SET (autovacuum_analyze_scale_factor = 0.02)")
    op.execute("ALTER TABLE emails SET (autovacuum_analyze_scale_factor = 0.02)")


def downgrade() -> None:
    op.execute("ALTER TABLE emails RESET (autovacuum_analyze_scale_factor)")
    op.execute("ALTER TABLE persons RESET (autovacuum_analyze_scale_factor)")
    op.execute("DROP INDEX IF EXISTS emails_domain_idx")
    op.execute("DROP INDEX IF EXISTS persons_soundex_block_idx")
    op.execute("DROP INDEX IF EXISTS persons_dmeta_block_idx")
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE 'ALTER ROLE contact_ops_runtime RESET statement_timeout';
        EXCEPTION
            WHEN insufficient_privilege OR undefined_object THEN
                RAISE NOTICE 'skipping ALTER ROLE contact_ops_runtime RESET (%)' , SQLERRM;
        END $$;
        """
    )
