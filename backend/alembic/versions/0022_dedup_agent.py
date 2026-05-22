"""dedup agent: merge candidates, splink models, source calibration

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-22

Phase 3.1 of Contact-Ops. Adds dedup-specific tables that Foundation's 0021
doesn't cover:

* ``dedup_pair_candidates``  — pairs that scored 0.40–0.65 and weren't merged
* ``splink_models``     — Splink model version store
* ``source_calibration`` — per-source confidence multipliers

Downgrade is implemented but should be exercised only in test; production
data in these tables is not safe to drop.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch")
    _create_dedup_pair_candidates()
    _create_splink_models()
    _create_source_calibration()
    _seed_source_calibration()
    _install_rls_policies()


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS source_calibration")
    op.execute("DROP TABLE IF EXISTS splink_models")
    op.execute("DROP TABLE IF EXISTS dedup_pair_candidates")
    op.execute("DROP EXTENSION IF EXISTS fuzzystrmatch")


# ---- dedup_pair_candidates ----

def _create_dedup_pair_candidates() -> None:
    op.execute(
        """
        CREATE TABLE dedup_pair_candidates (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id),
            person_a_id         UUID NOT NULL REFERENCES persons(id),
            person_b_id         UUID NOT NULL REFERENCES persons(id),
            score               DOUBLE PRECISION NOT NULL,
            score_breakdown     JSONB NOT NULL,
            blocking_keys       TEXT[] NOT NULL,
            last_scored_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            next_eligible_at    TIMESTAMPTZ,
            resolved_at         TIMESTAMPTZ,
            resolution          TEXT,
            CONSTRAINT dedup_pair_candidates_pair_unique UNIQUE (tenant_id, person_a_id, person_b_id),
            CONSTRAINT dedup_pair_candidates_ordered CHECK (person_a_id < person_b_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX dedup_pair_candidates_score_idx ON dedup_pair_candidates (tenant_id, score DESC) "
        "WHERE resolved_at IS NULL"
    )
    op.execute(
        "CREATE INDEX dedup_pair_candidates_re_eligible_idx ON dedup_pair_candidates (next_eligible_at) "
        "WHERE resolved_at IS NULL AND next_eligible_at IS NOT NULL"
    )


# ---- splink_models ----

def _create_splink_models() -> None:
    op.execute(
        """
        CREATE TABLE splink_models (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id),
            model_version       TEXT NOT NULL,
            settings_json       JSONB NOT NULL,
            trained_on_pairs    INTEGER NOT NULL,
            labeled_match_count INTEGER NOT NULL,
            labeled_non_match_count INTEGER NOT NULL,
            em_iterations       INTEGER NOT NULL,
            em_converged        BOOLEAN NOT NULL,
            trained_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            active              BOOLEAN NOT NULL DEFAULT FALSE,
            UNIQUE (tenant_id, model_version)
        )
        """
    )
    op.execute(
        "CREATE INDEX splink_models_active_idx ON splink_models (tenant_id) "
        "WHERE active = true"
    )


# ---- source_calibration ----

def _create_source_calibration() -> None:
    op.execute(
        """
        CREATE TABLE source_calibration (
            tenant_id           UUID NOT NULL REFERENCES tenants(id),
            source_kind         TEXT NOT NULL,
            confidence_multiplier DOUBLE PRECISION NOT NULL DEFAULT 0.85,
            samples_total       INTEGER NOT NULL DEFAULT 0,
            approved_count      INTEGER NOT NULL DEFAULT 0,
            rejected_count      INTEGER NOT NULL DEFAULT 0,
            last_recalibrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, source_kind)
        )
        """
    )


def _seed_source_calibration() -> None:
    """Seed per-tenant source calibration with sensible defaults."""
    op.execute(
        "INSERT INTO source_calibration "
        "(tenant_id, source_kind, confidence_multiplier) "
        "SELECT t.id, s.source_kind, s.multiplier "
        "FROM tenants t "
        "CROSS JOIN (VALUES "
        "    ('iOS'::text, 0.9::double precision), "
        "    ('Google', 0.7), "
        "    ('LinkedIn', 0.8), "
        "    ('Nextcloud', 0.85), "
        "    ('iCloud', 0.95) "
        ") AS s(source_kind, multiplier) "
        "ON CONFLICT (tenant_id, source_kind) DO NOTHING"
    )


# ---- RLS policies ----

def _install_rls_policies() -> None:
    tables = [
        "dedup_pair_candidates",
        "splink_models",
        "source_calibration",
    ]
    for tbl in tables:
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {tbl}
                USING (tenant_id::text = current_setting('app.tenant_id', true))
                WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
            """
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO contact_ops_app"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO contact_ops_audit"
        )
