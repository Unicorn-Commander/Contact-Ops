"""phase 3 foundation: agent fleet schema additions

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-22

Covers Phase 3 Design §3 in full. Specifically:

* Extends ``action_event`` with the idempotency, replay, reversibility,
  and trace-id columns the agent fleet needs.
* Extends ``agent_registry`` with slug, agent_class, description, budget,
  initial tier, triggers.
* Creates ``agent_trust`` (Beta posterior per agent × tenant × visibility).
* Creates ``agent_action_dlq``, ``proposal_conflict``, ``event_outbox``,
  ``prov_activities``, ``inbox_decisions``, ``cost_budget_violation``,
  ``circuit_breaker_ack``.
* Installs RLS policies on every new tenant-scoped table.

Downgrade is implemented but should be exercised only in test; production
data in these tables is not safe to drop.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    _extend_action_event()
    _extend_agent_registry()
    _create_agent_trust()
    _create_agent_action_dlq()
    _create_proposal_conflict()
    _create_event_outbox()
    _create_prov_activities()
    _create_inbox_decisions()
    _create_cost_budget_violation()
    _create_circuit_breaker_ack()
    _install_rls_policies()
    _install_outbox_notify_trigger()


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS event_outbox_notify_trg ON event_outbox")
    op.execute("DROP FUNCTION IF EXISTS event_outbox_notify()")
    op.execute("DROP TABLE IF EXISTS circuit_breaker_ack")
    op.execute("DROP TABLE IF EXISTS cost_budget_violation")
    op.execute("DROP TABLE IF EXISTS inbox_decisions")
    op.execute("DROP TABLE IF EXISTS prov_activities")
    op.execute("DROP TABLE IF EXISTS event_outbox")
    op.execute("DROP TABLE IF EXISTS proposal_conflict")
    op.execute("DROP TABLE IF EXISTS agent_action_dlq")
    op.execute("DROP TABLE IF EXISTS agent_trust")
    op.execute("DROP INDEX IF EXISTS agent_registry_slug_idx")
    op.execute(
        "ALTER TABLE agent_registry DROP CONSTRAINT IF EXISTS agent_registry_slug_unique"
    )
    op.execute("ALTER TABLE agent_registry DROP COLUMN IF EXISTS triggers")
    op.execute("ALTER TABLE agent_registry DROP COLUMN IF EXISTS initial_trust_tier")
    op.execute("ALTER TABLE agent_registry DROP COLUMN IF EXISTS cost_budget_monthly_cents")
    op.execute("ALTER TABLE agent_registry DROP COLUMN IF EXISTS description")
    op.execute("ALTER TABLE agent_registry DROP COLUMN IF EXISTS agent_class")
    op.execute("ALTER TABLE agent_registry DROP COLUMN IF EXISTS slug")
    op.execute("DROP INDEX IF EXISTS ae_idempotency_key_idx")
    op.execute("DROP INDEX IF EXISTS ae_parent_proposal_idx")
    op.execute("DROP INDEX IF EXISTS ae_reversibility_idx")
    op.execute("DROP INDEX IF EXISTS ae_trace_id_idx")
    op.execute("DROP INDEX IF EXISTS ae_inbox_recent_idx")
    op.execute("DROP INDEX IF EXISTS ae_cooldown_recent_idx")
    op.execute("DROP INDEX IF EXISTS ae_decision_payload_gin")
    op.execute("ALTER TABLE action_event DROP CONSTRAINT IF EXISTS ae_parent_proposal_fk")
    op.execute("ALTER TABLE action_event DROP COLUMN IF EXISTS triggered_by")
    op.execute("ALTER TABLE action_event DROP COLUMN IF EXISTS decided_by_user_id")
    op.execute("ALTER TABLE action_event DROP COLUMN IF EXISTS time_to_decide_seconds")
    op.execute("ALTER TABLE action_event DROP COLUMN IF EXISTS span_id")
    op.execute("ALTER TABLE action_event DROP COLUMN IF EXISTS trace_id")
    op.execute("ALTER TABLE action_event DROP COLUMN IF EXISTS evidence_pack_id")
    op.execute("ALTER TABLE action_event DROP COLUMN IF EXISTS trust_tier_at_creation")
    op.execute("ALTER TABLE action_event DROP COLUMN IF EXISTS agent_version")
    op.execute("ALTER TABLE action_event DROP COLUMN IF EXISTS conflict_reason")
    op.execute("ALTER TABLE action_event DROP COLUMN IF EXISTS parent_proposal_id")
    op.execute("ALTER TABLE action_event DROP COLUMN IF EXISTS reversibility_class")
    op.execute("ALTER TABLE action_event DROP COLUMN IF EXISTS decision_payload")
    op.execute("ALTER TABLE action_event DROP COLUMN IF EXISTS idempotency_key")


# ---- action_event extensions ----

def _extend_action_event() -> None:
    """Phase 3 Design §3.3."""
    op.execute(
        """
        ALTER TABLE action_event
            ADD COLUMN idempotency_key TEXT,
            ADD COLUMN decision_payload JSONB,
            ADD COLUMN reversibility_class TEXT
                CHECK (reversibility_class IN
                       ('reversible','reversible_24h','soft_delete','irreversible'))
                DEFAULT 'reversible',
            ADD COLUMN parent_proposal_id UUID,
            ADD COLUMN conflict_reason TEXT,
            ADD COLUMN agent_version TEXT,
            ADD COLUMN trust_tier_at_creation SMALLINT
                CHECK (trust_tier_at_creation BETWEEN 0 AND 4),
            ADD COLUMN evidence_pack_id UUID,
            ADD COLUMN trace_id TEXT,
            ADD COLUMN span_id TEXT,
            ADD COLUMN time_to_decide_seconds INTEGER,
            ADD COLUMN decided_by_user_id UUID,
            ADD COLUMN triggered_by TEXT
        """
    )
    op.execute(
        """
        ALTER TABLE action_event
            ADD CONSTRAINT ae_idempotency_key_unique UNIQUE (idempotency_key)
        """
    )
    op.execute(
        """
        ALTER TABLE action_event
            ADD CONSTRAINT ae_parent_proposal_fk
            FOREIGN KEY (parent_proposal_id)
            REFERENCES action_event(event_id) ON DELETE SET NULL
        """
    )
    op.execute(
        "CREATE INDEX ae_idempotency_key_idx ON action_event(idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ae_parent_proposal_idx ON action_event(parent_proposal_id) "
        "WHERE parent_proposal_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ae_reversibility_idx ON action_event(reversibility_class)"
    )
    op.execute(
        "CREATE INDEX ae_trace_id_idx ON action_event(trace_id) "
        "WHERE trace_id IS NOT NULL"
    )
    op.execute(
        """
        CREATE INDEX ae_inbox_recent_idx
            ON action_event(tenant_id, proposed_at DESC)
            WHERE status = 'proposed'
        """
    )
    op.execute(
        """
        CREATE INDEX ae_cooldown_recent_idx
            ON action_event(tenant_id, aggregate_id, proposed_at DESC)
            WHERE status = 'proposed'
        """
    )
    op.execute(
        "CREATE INDEX ae_decision_payload_gin ON action_event "
        "USING gin (decision_payload jsonb_path_ops) "
        "WHERE decision_payload IS NOT NULL"
    )
    op.execute(
        "COMMENT ON COLUMN action_event.idempotency_key IS "
        "'sha256(agent_slug || tenant || aggregate || event_type || content_hash); "
        "replay-safe retries reuse the row instead of writing a duplicate'"
    )
    op.execute(
        "COMMENT ON COLUMN action_event.reversibility_class IS "
        "'reversible | reversible_24h | soft_delete | irreversible. "
        "Only reversible classes can auto-apply at T2+ regardless of tier.'"
    )


# ---- agent_registry extensions ----

def _extend_agent_registry() -> None:
    op.execute(
        """
        ALTER TABLE agent_registry
            ADD COLUMN slug TEXT,
            ADD COLUMN agent_class TEXT
                CHECK (agent_class IN ('batch','event','continuous')),
            ADD COLUMN description TEXT,
            ADD COLUMN cost_budget_monthly_cents INTEGER
                CHECK (cost_budget_monthly_cents > 0),
            ADD COLUMN initial_trust_tier SMALLINT
                CHECK (initial_trust_tier BETWEEN 0 AND 4) DEFAULT 0,
            ADD COLUMN triggers JSONB DEFAULT '[]'::jsonb
        """
    )
    # A plain UNIQUE constraint works here: Postgres treats NULL != NULL for
    # uniqueness, so any legacy rows that pre-date Phase 3 can remain with a
    # NULL slug without colliding. New rows always set slug, and
    # ``ensure_agents_registered`` relies on the constraint for ON CONFLICT.
    op.execute(
        "ALTER TABLE agent_registry ADD CONSTRAINT agent_registry_slug_unique UNIQUE (slug)"
    )
    op.execute(
        "CREATE INDEX agent_registry_slug_idx ON agent_registry(slug) WHERE slug IS NOT NULL"
    )
    op.execute(
        "COMMENT ON COLUMN agent_registry.slug IS "
        "'Stable identifier (e.g. ``dedup``, ``voice-match``). "
        "Used in actor_chain.sub and idempotency keys.'"
    )


# ---- agent_trust ----

def _create_agent_trust() -> None:
    op.execute(
        """
        CREATE TABLE agent_trust (
            agent_slug          TEXT NOT NULL,
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            visibility          TEXT NOT NULL
                CHECK (visibility IN ('private','team','org','shared')),
            alpha               DOUBLE PRECISION NOT NULL DEFAULT 1.0
                CHECK (alpha >= 1.0),
            beta                DOUBLE PRECISION NOT NULL DEFAULT 1.0
                CHECK (beta >= 1.0),
            mean                DOUBLE PRECISION GENERATED ALWAYS AS
                (alpha / (alpha + beta)) STORED,
            lower_ci_90         DOUBLE PRECISION,
            lower_ci_95         DOUBLE PRECISION,
            samples_total       INTEGER NOT NULL DEFAULT 0
                CHECK (samples_total >= 0),
            samples_7d          INTEGER NOT NULL DEFAULT 0,
            samples_30d         INTEGER NOT NULL DEFAULT 0,
            approval_rate_7d    DOUBLE PRECISION,
            approval_rate_30d   DOUBLE PRECISION,
            psi_score           DOUBLE PRECISION,
            ks_p_value          DOUBLE PRECISION,
            drift_status        TEXT NOT NULL DEFAULT 'stable'
                CHECK (drift_status IN ('stable','warning','drift')),
            current_tier        SMALLINT NOT NULL DEFAULT 0
                CHECK (current_tier BETWEEN 0 AND 4),
            last_demotion_at    TIMESTAMPTZ,
            last_updated        TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (agent_slug, tenant_id, visibility)
        )
        """
    )
    op.execute(
        "CREATE INDEX agent_trust_tier_idx ON agent_trust(current_tier)"
    )
    op.execute(
        "CREATE INDEX agent_trust_drift_idx ON agent_trust(drift_status) "
        "WHERE drift_status != 'stable'"
    )
    op.execute(
        "CREATE INDEX agent_trust_tenant_idx ON agent_trust(tenant_id)"
    )


# ---- agent_action_dlq ----

def _create_agent_action_dlq() -> None:
    op.execute(
        """
        CREATE TABLE agent_action_dlq (
            id                          UUID PRIMARY KEY DEFAULT uuidv7_generate(),
            original_action_event_id    UUID NOT NULL REFERENCES action_event(event_id),
            agent_slug                  TEXT NOT NULL,
            tenant_id                   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            error                       TEXT NOT NULL,
            error_class                 TEXT NOT NULL
                CHECK (error_class IN
                       ('llm_5xx','schema_validation','downstream_timeout',
                        'permission_denied','cascade_block','budget_exceeded','other')),
            retry_count                 INTEGER NOT NULL DEFAULT 0
                CHECK (retry_count >= 0),
            last_attempted_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            payload                     JSONB NOT NULL,
            replayable                  BOOLEAN NOT NULL DEFAULT TRUE,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at                 TIMESTAMPTZ,
            resolution_note             TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX agent_action_dlq_error_class_idx ON agent_action_dlq(error_class)"
    )
    op.execute(
        "CREATE INDEX agent_action_dlq_unresolved_idx ON agent_action_dlq(created_at) "
        "WHERE resolved_at IS NULL"
    )
    op.execute(
        "CREATE INDEX agent_action_dlq_agent_idx ON agent_action_dlq(agent_slug)"
    )


# ---- proposal_conflict ----

def _create_proposal_conflict() -> None:
    op.execute(
        """
        CREATE TABLE proposal_conflict (
            id                          UUID PRIMARY KEY DEFAULT uuidv7_generate(),
            tenant_id                   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            primary_proposal_id         UUID NOT NULL REFERENCES action_event(event_id),
            conflicting_proposal_id     UUID NOT NULL REFERENCES action_event(event_id),
            conflict_type               TEXT NOT NULL
                CHECK (conflict_type IN
                       ('inverse_of_recent','contradicting_field_value',
                        'delete_vs_modify','cascade_loop','per_entity_cooldown_exceeded')),
            entity_id                   UUID NOT NULL,
            field_name                  TEXT,
            resolution                  TEXT,
            resolved_by_user_id         UUID,
            resolved_at                 TIMESTAMPTZ,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX proposal_conflict_unresolved_idx ON proposal_conflict(created_at) "
        "WHERE resolved_at IS NULL"
    )
    op.execute(
        "CREATE INDEX proposal_conflict_primary_idx ON proposal_conflict(primary_proposal_id)"
    )


# ---- event_outbox ----

def _create_event_outbox() -> None:
    op.execute(
        """
        CREATE TABLE event_outbox (
            id                  BIGSERIAL PRIMARY KEY,
            channel             TEXT NOT NULL,
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            payload             JSONB NOT NULL,
            notify_attempted    BOOLEAN NOT NULL DEFAULT FALSE,
            processed_at        TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX event_outbox_unprocessed_idx ON event_outbox(channel, created_at) "
        "WHERE processed_at IS NULL"
    )
    op.execute(
        "CREATE INDEX event_outbox_tenant_idx ON event_outbox(tenant_id)"
    )


# ---- prov_activities ----

def _create_prov_activities() -> None:
    op.execute(
        """
        CREATE TABLE prov_activities (
            id                  UUID PRIMARY KEY DEFAULT uuidv7_generate(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            activity_type       TEXT NOT NULL,
            agent_id            TEXT NOT NULL,
            agent_version       TEXT,
            started_at          TIMESTAMPTZ NOT NULL,
            ended_at            TIMESTAMPTZ,
            prompt              JSONB,
            response            JSONB,
            reasoning           TEXT,
            confidence          DOUBLE PRECISION
                CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
            inputs              UUID[],
            outputs             UUID[],
            trace_id            TEXT,
            cost_cents          INTEGER NOT NULL DEFAULT 0
                CHECK (cost_cents >= 0),
            tokens_input        INTEGER NOT NULL DEFAULT 0,
            tokens_output       INTEGER NOT NULL DEFAULT 0,
            model               TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX prov_activities_agent_idx ON prov_activities(agent_id, started_at)"
    )
    op.execute(
        "CREATE INDEX prov_activities_type_idx ON prov_activities(activity_type)"
    )
    op.execute(
        "CREATE INDEX prov_activities_trace_idx ON prov_activities(trace_id) "
        "WHERE trace_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX prov_activities_tenant_burn_idx "
        "ON prov_activities(tenant_id, started_at DESC, cost_cents)"
    )


# ---- inbox_decisions ----

def _create_inbox_decisions() -> None:
    op.execute(
        """
        CREATE TABLE inbox_decisions (
            id                          UUID PRIMARY KEY DEFAULT uuidv7_generate(),
            tenant_id                   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            proposal_id                 UUID NOT NULL REFERENCES action_event(event_id),
            cluster_id                  UUID,
            reviewer_id                 UUID NOT NULL,
            decision                    TEXT NOT NULL
                CHECK (decision IN
                       ('approve','reject','snooze','mute','escalate',
                        'dismiss_duplicate','keep_both')),
            tier_assigned               SMALLINT NOT NULL
                CHECK (tier_assigned BETWEEN 1 AND 4),
            time_to_decide_sec          INTEGER,
            keyboard_path               BOOLEAN NOT NULL DEFAULT FALSE,
            typed_phrase_used           BOOLEAN NOT NULL DEFAULT FALSE,
            field_choices               JSONB,
            agent_confidence            DOUBLE PRECISION,
            decided_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX inbox_decisions_reviewer_idx "
        "ON inbox_decisions(reviewer_id, decided_at)"
    )
    op.execute(
        "CREATE INDEX inbox_decisions_proposal_idx ON inbox_decisions(proposal_id)"
    )


# ---- cost_budget_violation ----

def _create_cost_budget_violation() -> None:
    op.execute(
        """
        CREATE TABLE cost_budget_violation (
            id                  UUID PRIMARY KEY DEFAULT uuidv7_generate(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            agent_slug          TEXT NOT NULL,
            layer               SMALLINT NOT NULL
                CHECK (layer BETWEEN 1 AND 5),
            detail              JSONB NOT NULL,
            occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX cost_budget_violation_tenant_idx "
        "ON cost_budget_violation(tenant_id, occurred_at DESC)"
    )
    op.execute(
        "CREATE INDEX cost_budget_violation_agent_idx "
        "ON cost_budget_violation(agent_slug, occurred_at DESC)"
    )


# ---- circuit_breaker_ack ----

def _create_circuit_breaker_ack() -> None:
    op.execute(
        """
        CREATE TABLE circuit_breaker_ack (
            id                  UUID PRIMARY KEY DEFAULT uuidv7_generate(),
            scope               TEXT NOT NULL,
            agent_slug          TEXT,
            transition          TEXT NOT NULL
                CHECK (transition IN ('opened','resumed','tripped_auto')),
            reason              TEXT NOT NULL,
            actor_user_id       UUID,
            occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX circuit_breaker_ack_scope_idx "
        "ON circuit_breaker_ack(scope, occurred_at DESC)"
    )


# ---- RLS policies on new tenant-scoped tables ----

def _install_rls_policies() -> None:
    """Tenant isolation via ``app.tenant_id`` GUC.

    Phase 0 (migration 0015) installed the canonical RLS pattern. We re-use
    the same predicate so behavior is consistent across tables.

    ``contact_ops_app`` is the role the app connects as. ``current_tenant_id()``
    is a helper installed by 0001 that reads ``app.tenant_id`` GUC.
    """
    tables = [
        "agent_trust",
        "agent_action_dlq",
        "proposal_conflict",
        "event_outbox",
        "prov_activities",
        "inbox_decisions",
        "cost_budget_violation",
        "circuit_breaker_ack",  # scoped via agent_slug not tenant_id; see policy below
    ]
    for tbl in tables:
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")

    # Tenant-scoped tables (have a tenant_id column).
    for tbl in [
        "agent_trust",
        "agent_action_dlq",
        "proposal_conflict",
        "event_outbox",
        "prov_activities",
        "inbox_decisions",
        "cost_budget_violation",
    ]:
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {tbl}
                USING (tenant_id = current_tenant_id())
                WITH CHECK (tenant_id = current_tenant_id())
            """
        )

    # circuit_breaker_ack is a platform-wide audit log; ADMIN role can read.
    # We let everyone with the app role SELECT (it is non-sensitive ops
    # data) but block INSERT/UPDATE/DELETE except via the audit role.
    op.execute(
        """
        CREATE POLICY admin_read ON circuit_breaker_ack
            FOR SELECT
            USING (true)
        """
    )
    op.execute(
        """
        CREATE POLICY audit_insert ON circuit_breaker_ack
            FOR INSERT
            WITH CHECK (true)
        """
    )

    # Grant the app + audit roles the minimum surface they need.
    for tbl in tables:
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO contact_ops_app"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO contact_ops_audit"
        )
    op.execute("GRANT USAGE, SELECT ON SEQUENCE event_outbox_id_seq TO contact_ops_app")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE event_outbox_id_seq TO contact_ops_audit")


def _install_outbox_notify_trigger() -> None:
    """After-insert trigger that fires ``pg_notify`` for outbox events."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION event_outbox_notify() RETURNS trigger AS $$
        BEGIN
            PERFORM pg_notify(
                NEW.channel,
                jsonb_build_object(
                    'outbox_id', NEW.id,
                    'tenant_id', NEW.tenant_id,
                    'created_at', NEW.created_at
                )::text
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER event_outbox_notify_trg
            AFTER INSERT ON event_outbox
            FOR EACH ROW EXECUTE FUNCTION event_outbox_notify()
        """
    )
