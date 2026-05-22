"""phase 3 inbox backend: MCP tool substrate (Phase 3.3a)

Revision ID: 0024
Revises: 0021
Create Date: 2026-05-22

NOTE ON CHAIN: written off ``phase-3-foundation`` (0021). When ``phase-3-dedup``
(0022) and ``phase-3-voice-match`` (0023) merge, rebase ``down_revision`` to
``"0023"``. The schema additions here are orthogonal to either of those tracks.

Covers the schema gaps the Phase 3.3 design doc references but Foundation did
not land. Specifically:

* ``agent_suppression_rules`` — backs ``reject_proposal(mode="mute")``.
  Suppresses future proposals matching ``(agent_slug, aggregate_type,
  aggregate_id, field_name)``. NULL ``aggregate_id`` = "all entities";
  NULL ``field_name`` = "all fields". Default expiry +90 days.
* ``action_event.target_tenant_id`` — populated by cross-tenant agents
  (CardDAV reconciliation, Data Intel bridge). Drives the
  ``cross_tenant`` derived flag the inbox UI reads.
* ``action_event.snoozed_until`` — a snoozed proposal is just
  ``status='proposed' AND snoozed_until > now()``. Avoids the ENUM ALTER
  dance that ``ADD VALUE 'snoozed'`` would require under Alembic.
* ``action_event.evidence_pack_id`` FK to ``prov_activities(id)`` — the
  column already exists from Foundation; this just adds the constraint.
* ``inbox_decisions.decision`` CHECK extended with ``'undo'`` and
  ``'revert'``. Undo = reverse a same-session approval (≤30s window).
  Revert = reverse a T0 auto-applied decision (≤5min window).
* Trigger ``proposal_conflict_clear_snooze_trg`` — when a
  ``proposal_conflict`` row is inserted, both involved proposals lose
  their snooze (Linear-pattern). Sub-second response without a beat
  task.
* RLS policies for the new ``agent_suppression_rules`` table.

Downgrade is implemented but should not be run in production.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    _extend_action_event()
    _add_evidence_pack_fk()
    _create_agent_suppression_rules()
    _extend_inbox_decisions_check()
    _install_conflict_clear_snooze_trigger()
    _install_agent_suppression_rules_rls()


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agent_suppression_rules_tenant_select ON agent_suppression_rules")
    op.execute("DROP POLICY IF EXISTS agent_suppression_rules_tenant_modify ON agent_suppression_rules")
    op.execute("ALTER TABLE IF EXISTS agent_suppression_rules DISABLE ROW LEVEL SECURITY")

    op.execute("DROP TRIGGER IF EXISTS proposal_conflict_clear_snooze_trg ON proposal_conflict")
    op.execute("DROP FUNCTION IF EXISTS proposal_conflict_clear_snooze()")

    _restore_inbox_decisions_check()

    op.execute("DROP TABLE IF EXISTS agent_suppression_rules")

    op.execute(
        "ALTER TABLE action_event "
        "DROP CONSTRAINT IF EXISTS ae_evidence_pack_fk"
    )
    op.execute("DROP INDEX IF EXISTS ae_snoozed_until_idx")
    op.execute("DROP INDEX IF EXISTS ae_target_tenant_idx")
    op.execute("ALTER TABLE action_event DROP COLUMN IF EXISTS snoozed_until")
    op.execute("ALTER TABLE action_event DROP COLUMN IF EXISTS target_tenant_id")


# ---- action_event additions ----

def _extend_action_event() -> None:
    op.execute(
        """
        ALTER TABLE action_event
            ADD COLUMN target_tenant_id UUID
                REFERENCES tenants(id) ON DELETE SET NULL,
            ADD COLUMN snoozed_until TIMESTAMPTZ
        """
    )
    op.execute(
        "CREATE INDEX ae_target_tenant_idx ON action_event(target_tenant_id) "
        "WHERE target_tenant_id IS NOT NULL"
    )
    op.execute(
        """
        CREATE INDEX ae_snoozed_until_idx
            ON action_event(snoozed_until)
            WHERE status = 'proposed' AND snoozed_until IS NOT NULL
        """
    )
    op.execute(
        "COMMENT ON COLUMN action_event.target_tenant_id IS "
        "'Set by cross-tenant agents when a proposal targets a different "
        "tenant than the proposing tenant. NULL = same-tenant proposal.'"
    )
    op.execute(
        "COMMENT ON COLUMN action_event.snoozed_until IS "
        "'A snoozed proposal stays status=proposed but is filtered out of "
        "the inbox until now() >= snoozed_until. The inbox-snooze-flipper "
        "beat task nulls this out on expiry; the proposal_conflict trigger "
        "clears it on conflict.'"
    )


def _add_evidence_pack_fk() -> None:
    """Promote ``action_event.evidence_pack_id`` to a real FK.

    Foundation created the column but not the constraint (the table it
    points at, ``prov_activities``, is created in the same migration so
    forward-referencing was awkward). With both tables now extant we can
    add the constraint.
    """
    op.execute(
        """
        ALTER TABLE action_event
            ADD CONSTRAINT ae_evidence_pack_fk
            FOREIGN KEY (evidence_pack_id)
            REFERENCES prov_activities(id)
            ON DELETE SET NULL
        """
    )


# ---- agent_suppression_rules ----

def _create_agent_suppression_rules() -> None:
    op.execute(
        """
        CREATE TABLE agent_suppression_rules (
            id                      UUID PRIMARY KEY DEFAULT uuidv7_generate(),
            tenant_id               UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            agent_slug              TEXT NOT NULL,
            aggregate_type          TEXT,
            aggregate_id            UUID,
            field_name              TEXT,
            expires_at              TIMESTAMPTZ NOT NULL
                DEFAULT (now() + INTERVAL '90 days'),
            created_by              UUID NOT NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            note                    TEXT
        )
        """
    )
    # Lookup index: queries filter by tenant + agent + (optionally
    # aggregate + field). The "WHERE expires_at > now()" partial-index
    # predicate is not allowed (now() is not IMMUTABLE). Callers add the
    # expiry filter at query time; the standalone expiry_idx below
    # supports the snoozed-flipper sweeper.
    op.execute(
        """
        CREATE INDEX agent_suppression_rules_lookup_idx
            ON agent_suppression_rules
            (tenant_id, agent_slug, aggregate_id, field_name)
        """
    )
    op.execute(
        "CREATE INDEX agent_suppression_rules_expiry_idx "
        "ON agent_suppression_rules(expires_at)"
    )
    op.execute(
        "COMMENT ON TABLE agent_suppression_rules IS "
        "'Per-tenant mute rules created via reject_proposal(mode=\"mute\"). "
        "NULL aggregate_id means all entities matching agent_slug + field_name. "
        "NULL field_name means all fields. Default expiry: +90 days.'"
    )


def _install_agent_suppression_rules_rls() -> None:
    op.execute("ALTER TABLE agent_suppression_rules ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY agent_suppression_rules_tenant_select
            ON agent_suppression_rules
            FOR SELECT
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )
    op.execute(
        """
        CREATE POLICY agent_suppression_rules_tenant_modify
            ON agent_suppression_rules
            FOR ALL
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


# ---- inbox_decisions.decision CHECK extension ----

def _extend_inbox_decisions_check() -> None:
    """Drop the Foundation CHECK and recreate with ``undo`` + ``revert`` added.

    Postgres auto-names inline column CHECK constraints as
    ``<table>_<column>_check``, so we can drop by name directly. The
    earlier introspection query did not work because Postgres rewrites
    ``decision IN (...)`` into ``decision = ANY (ARRAY[...])`` at parse
    time, so the LIKE pattern never matched.
    """
    op.execute(
        "ALTER TABLE inbox_decisions DROP CONSTRAINT IF EXISTS "
        "inbox_decisions_decision_check"
    )
    op.execute(
        """
        ALTER TABLE inbox_decisions
            ADD CONSTRAINT inbox_decisions_decision_check
            CHECK (decision IN (
                'approve','reject','snooze','mute','escalate',
                'dismiss_duplicate','keep_both','undo','revert'
            ))
        """
    )
    # Foundation's tier_assigned CHECK is ``BETWEEN 1 AND 4`` (human
    # decisions only). ``revert`` of a T0 auto-apply legitimately writes
    # tier 0; widen the constraint here so the new decision values can
    # coexist with their natural tier.
    op.execute(
        "ALTER TABLE inbox_decisions DROP CONSTRAINT IF EXISTS "
        "inbox_decisions_tier_assigned_check"
    )
    op.execute(
        """
        ALTER TABLE inbox_decisions
            ADD CONSTRAINT inbox_decisions_tier_assigned_check
            CHECK (tier_assigned BETWEEN 0 AND 4)
        """
    )


def _restore_inbox_decisions_check() -> None:
    op.execute(
        """
        ALTER TABLE inbox_decisions
            DROP CONSTRAINT IF EXISTS inbox_decisions_tier_assigned_check
        """
    )
    op.execute(
        """
        ALTER TABLE inbox_decisions
            ADD CONSTRAINT inbox_decisions_tier_assigned_check
            CHECK (tier_assigned BETWEEN 1 AND 4)
        """
    )
    op.execute(
        """
        ALTER TABLE inbox_decisions
            DROP CONSTRAINT IF EXISTS inbox_decisions_decision_check
        """
    )
    op.execute(
        """
        ALTER TABLE inbox_decisions
            ADD CONSTRAINT inbox_decisions_decision_check
            CHECK (decision IN (
                'approve','reject','snooze','mute','escalate',
                'dismiss_duplicate','keep_both'
            ))
        """
    )


# ---- proposal_conflict trigger: clear snooze on conflict ----

def _install_conflict_clear_snooze_trigger() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION proposal_conflict_clear_snooze()
        RETURNS TRIGGER AS $$
        BEGIN
            UPDATE action_event
            SET snoozed_until = NULL
            WHERE event_id IN (NEW.primary_proposal_id, NEW.conflicting_proposal_id)
              AND snoozed_until IS NOT NULL;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER proposal_conflict_clear_snooze_trg
            AFTER INSERT ON proposal_conflict
            FOR EACH ROW
            EXECUTE FUNCTION proposal_conflict_clear_snooze();
        """
    )
