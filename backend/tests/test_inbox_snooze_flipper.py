"""Per-tenant behaviour tests for the inbox-snooze-flipper Celery task.

The flipper runs as the NOBYPASSRLS ``contact_ops_runtime`` role: with no
``app.tenant_id`` bound it sees ZERO action_event rows under RLS
(``ae_modify`` is ``USING (tenant_id = current_tenant_id())`` and the GUC is
unset), so the fix enumerates tenants via the admin DSN then flips RLS-bound
per tenant. These tests prove that round-trip against the testcontainer
Postgres:

  * an elapsed snooze on a proposed action_event is cleared and the returned
    ``flipped`` count reflects it, while a different tenant's still-future
    snooze is left untouched (per-tenant loop + WHERE clause);
  * BOTH tenants' elapsed snoozes flip when each has one (the loop visits
    every enumerated tenant);
  * the public sync Celery task drives ``asyncio.run`` and returns the count;
  * no admin DSN -> safe no-op (the enumeration short-circuits);
  * the documented bug: the SAME UPDATE as the runtime role with NO tenant
    bound matches zero rows -- which is exactly why the per-tenant binding is
    required.

Seeding/asserting/cleanup use a superuser (BYPASSRLS) sync engine and REAL
commits, because the task opens its OWN engines and would not see a
rolled-back fixture transaction. The task's app-role DSN authenticates
directly as ``contact_ops_runtime`` (the rt_engine idiom from
test_p4_isolation_rls.py) so RLS is genuinely exercised.
"""
# ruff: noqa: E501

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text

from contact_ops.agents import inbox_snooze_flipper
from contact_ops.agents.inbox_snooze_flipper import _flip_async, flip_expired_snoozes
from contact_ops.core.config import get_settings

# ---- DSN derivation ----


def _sync_su_url(async_url: str) -> str:
    """Superuser (BYPASSRLS) sync DSN -- enumeration + seed/assert/cleanup."""
    return async_url.replace("postgresql+asyncpg://", "postgresql://")


def _runtime_async_url(async_url: str) -> str:
    """App-role async DSN authenticating AS contact_ops_runtime (NOBYPASSRLS)."""
    return async_url.replace("postgres:test@", "contact_ops_runtime:rtpw@")


# ---- fixtures ----


@pytest.fixture(scope="module")
def _runtime_role(postgres_container: str):
    """Ensure the contact_ops_runtime LOGIN role exists once (rt_engine idiom)."""
    eng = create_engine(_sync_su_url(postgres_container), future=True)
    with eng.begin() as c:
        c.execute(
            text(
                "DO $$ BEGIN CREATE ROLE contact_ops_runtime LOGIN PASSWORD 'rtpw' "
                "NOSUPERUSER NOBYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END $$"
            )
        )
        c.execute(text("GRANT contact_ops_app TO contact_ops_runtime"))
    eng.dispose()


@pytest.fixture
def su_engine(postgres_container: str):
    """Superuser sync engine for committed seed / assert / cleanup."""
    eng = create_engine(_sync_su_url(postgres_container), future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def runtime_settings(postgres_container: str, _runtime_role):
    """Settings whose app role = contact_ops_runtime, admin = postgres superuser."""
    return get_settings().model_copy(
        update={
            "DATABASE_URL": _runtime_async_url(postgres_container),
            "MIGRATION_DATABASE_URL": _sync_su_url(postgres_container),
        }
    )


# ---- seed helpers ----


def _insert_tenant(conn, tenant_id: uuid.UUID) -> None:
    conn.execute(
        text(
            """
            INSERT INTO tenants (id, slug, kind, display_name, owner_user_id,
                hipaa_mode, qdrant_namespace, garage_bucket_prefix)
            VALUES (CAST(:id AS uuid), :slug, 'brand', :name, CAST(:id AS uuid),
                false, :ns, :bkt)
            """
        ),
        {
            "id": str(tenant_id),
            "slug": f"snooze-test-{tenant_id.hex[:8]}",
            "name": f"Snooze {tenant_id.hex[:8]}",
            "ns": f"ns-{tenant_id.hex[:8]}",
            "bkt": f"bkt-{tenant_id.hex[:8]}",
        },
    )


def _insert_proposed_event(conn, *, tenant_id: uuid.UUID, snoozed_until: datetime) -> uuid.UUID:
    eid = uuid.uuid4()
    agg_id = uuid.uuid4()
    payload = {"display_name": "Snoozed"}
    actor = {"sub": "enrichment", "agent_version": "test-1.0", "act": {"sub": "system"}}
    decision = {
        "reversibility": "reversible",
        "tier_at_creation": 2,
        "auto_apply_eligible": False,
        "payload_after": payload,
        "payload_before": None,
    }
    raw_idem = f"{eid}|enrichment|{tenant_id}|{agg_id}|enrichment.field_set"
    content_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).digest()
    conn.execute(
        text(
            """
            INSERT INTO action_event (
                event_id, event_type, event_version,
                tenant_id, aggregate_type, aggregate_id,
                payload, actor, actor_type,
                confidence, evidence, rationale,
                status, content_hash, idempotency_key,
                decision_payload, reversibility_class,
                agent_version, trust_tier_at_creation, triggered_by,
                snoozed_until
            ) VALUES (
                CAST(:event_id AS uuid), 'enrichment.field_set', 1,
                CAST(:tenant AS uuid), 'person'::entity_kind, CAST(:agg AS uuid),
                CAST(:payload AS jsonb), CAST(:actor AS jsonb), 'agent'::actor_type,
                0.85, '{}'::jsonb, 'snooze flipper test',
                'proposed'::event_status, :content_hash, :idem,
                CAST(:decision AS jsonb), 'reversible',
                'test-1.0', 2, 'snooze-test',
                :snoozed_until
            )
            """
        ),
        {
            "event_id": str(eid),
            "tenant": str(tenant_id),
            "agg": str(agg_id),
            "payload": json.dumps(payload),
            "actor": json.dumps(actor),
            "content_hash": content_hash,
            "idem": hashlib.sha256(raw_idem.encode()).hexdigest(),
            "decision": json.dumps(decision),
            "snoozed_until": snoozed_until,
        },
    )
    return eid


def _clear_all_eligible(conn) -> None:
    """Zero out any pre-existing eligible snoozes (committed by other tests) so
    the GLOBAL flipped count reflects only this test's seed. Semantically a
    no-op for correctness -- those rows would be flipped anyway."""
    conn.execute(
        text(
            "UPDATE action_event SET snoozed_until = NULL "
            "WHERE status = 'proposed' AND snoozed_until IS NOT NULL AND snoozed_until <= now()"
        )
    )


def _snoozed_until(conn, eid: uuid.UUID):
    return conn.execute(
        text("SELECT snoozed_until FROM action_event WHERE event_id = :e"),
        {"e": str(eid)},
    ).scalar()


@pytest.fixture
def seeder(su_engine):
    """Commit tenants + proposed action_events; clean them up at teardown."""
    created: list[uuid.UUID] = []

    def make(*, snoozed_until: datetime) -> tuple[uuid.UUID, uuid.UUID]:
        tid = uuid.uuid4()
        with su_engine.begin() as c:
            _insert_tenant(c, tid)
            eid = _insert_proposed_event(c, tenant_id=tid, snoozed_until=snoozed_until)
        created.append(tid)
        return tid, eid

    yield make

    with su_engine.begin() as c:
        for tid in created:
            c.execute(text("DELETE FROM action_event WHERE tenant_id = :t"), {"t": str(tid)})
            c.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": str(tid)})


# ---- tests ----


def test_flip_clears_expired_and_leaves_future(su_engine, seeder, runtime_settings):
    """Tenant A's elapsed snooze is cleared; tenant B's future snooze is left;
    the per-tenant loop binds each tenant and the returned count is exact."""
    past = datetime.now(UTC) - timedelta(hours=1)
    future = datetime.now(UTC) + timedelta(hours=2)
    with su_engine.begin() as c:
        _clear_all_eligible(c)
    _tid_a, eid_a = seeder(snoozed_until=past)
    _tid_b, eid_b = seeder(snoozed_until=future)

    result = asyncio.run(_flip_async(runtime_settings))

    assert result == {"flipped": 1}
    with su_engine.begin() as c:
        assert _snoozed_until(c, eid_a) is None, "elapsed snooze must be cleared"
        assert _snoozed_until(c, eid_b) is not None, "future snooze must be untouched"


def test_flip_visits_every_enumerated_tenant(su_engine, seeder, runtime_settings):
    """Both tenants carry an elapsed snooze -> both flip (the loop reaches every
    tenant the admin DSN enumerates, each RLS-bound to its own row)."""
    past = datetime.now(UTC) - timedelta(hours=1)
    with su_engine.begin() as c:
        _clear_all_eligible(c)
    _tid_a, eid_a = seeder(snoozed_until=past)
    _tid_b, eid_b = seeder(snoozed_until=past)

    result = asyncio.run(_flip_async(runtime_settings))

    assert result == {"flipped": 2}
    with su_engine.begin() as c:
        assert _snoozed_until(c, eid_a) is None
        assert _snoozed_until(c, eid_b) is None


def test_sync_task_entrypoint_drives_async_and_returns_count(
    su_engine, seeder, runtime_settings, monkeypatch
):
    """The registered sync task (get_settings -> asyncio.run -> dict) flips a
    real elapsed snooze end-to-end via the public Celery entry point."""
    past = datetime.now(UTC) - timedelta(hours=1)
    with su_engine.begin() as c:
        _clear_all_eligible(c)
    _tid, eid = seeder(snoozed_until=past)

    monkeypatch.setattr(inbox_snooze_flipper, "get_settings", lambda: runtime_settings)
    result = flip_expired_snoozes.apply(
        kwargs={"agent_slug": "inbox-snooze-flipper"}
    ).get()

    assert result == {"flipped": 1}
    with su_engine.begin() as c:
        assert _snoozed_until(c, eid) is None


def test_no_admin_dsn_is_safe_noop(runtime_settings, monkeypatch):
    """No admin DSN -> enumeration short-circuits -> {"flipped": 0}, no DB hit.
    Exercises the sync wrapper + asyncio.run + the fail-soft guard."""
    noadmin = runtime_settings.model_copy(update={"MIGRATION_DATABASE_URL": None})
    monkeypatch.setattr(inbox_snooze_flipper, "get_settings", lambda: noadmin)

    result = flip_expired_snoozes.apply(
        kwargs={"agent_slug": "inbox-snooze-flipper"}
    ).get()

    assert result == {"flipped": 0}


def test_runtime_role_without_binding_matches_zero_rows(
    su_engine, seeder, postgres_container, _runtime_role
):
    """Documents the bug the fix addresses: as contact_ops_runtime (NOBYPASSRLS)
    with NO app.tenant_id bound, RLS hides every row, so the snooze-clearing
    UPDATE matches ZERO rows. The per-tenant binding is what makes it work."""
    past = datetime.now(UTC) - timedelta(hours=1)
    with su_engine.begin() as c:
        _clear_all_eligible(c)
    _tid, eid = seeder(snoozed_until=past)

    rt_sync = _sync_su_url(_runtime_async_url(postgres_container))
    rt_eng = create_engine(rt_sync, future=True)
    try:
        with rt_eng.begin() as c:
            n = c.execute(
                text(
                    "UPDATE action_event SET snoozed_until = NULL "
                    "WHERE status = 'proposed' AND snoozed_until IS NOT NULL "
                    "AND snoozed_until <= now()"
                )
            ).rowcount
    finally:
        rt_eng.dispose()

    assert n == 0, "unbound runtime role must match zero rows under RLS"
    with su_engine.begin() as c:
        assert _snoozed_until(c, eid) is not None, "row must remain snoozed"
