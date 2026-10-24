"""Preflight decision and prestart wiring. No database."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from contact_ops.ops import prestart
from contact_ops.ops.rls_probe_preflight import (
    SEED_COMMAND,
    PreflightDecision,
    PreflightFacts,
    PreflightUnavailable,
    ProbeObservation,
    asyncpg_dsn,
    decide_rls_probe_preflight,
    load_preflight_facts,
    should_refuse_before_migrate,
    should_warn_after_migrate,
)

TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"


def _facts(**overrides: Any) -> PreflightFacts:
    base: dict[str, Any] = {
        "runtime_privileged": False,
        "tenants_table_exists": True,
        "persons_table_exists": True,
        "isolation_column_exists": True,
        "configured_ids": (TENANT_A, TENANT_B),
        "observations": (
            ProbeObservation(TENANT_A, True, "strict", 1),
            ProbeObservation(TENANT_B, True, "strict", 1),
        ),
    }
    base.update(overrides)
    return PreflightFacts(**base)


def _ready_pair() -> PreflightDecision:
    return decide_rls_probe_preflight(_facts())


def test_asyncpg_dsn_strips_driver_suffix() -> None:
    assert (
        asyncpg_dsn("postgresql+asyncpg://runtime:pw@db/contact_ops_db")
        == "${CONTACT_OPS_DATABASE_URL}"
    )
    assert asyncpg_dsn("postgres://admin:REMOVED@db/contact_ops_db").startswith("postgresql://")


def test_privileged_runtime_allows_an_empty_probe_list() -> None:
    decision = decide_rls_probe_preflight(
        _facts(runtime_privileged=True, configured_ids=(), observations=())
    )
    assert decision == PreflightDecision("allow", "runtime_privileged")
    assert should_refuse_before_migrate(decision) is False


def test_fresh_database_defers_so_the_first_migration_can_run() -> None:
    decision = decide_rls_probe_preflight(
        _facts(
            tenants_table_exists=False,
            persons_table_exists=False,
            isolation_column_exists=False,
            configured_ids=(),
            observations=(),
        )
    )
    assert decision == PreflightDecision("defer", "schema_not_ready")
    assert should_refuse_before_migrate(decision) is False


def test_missing_isolation_column_defers() -> None:
    decision = decide_rls_probe_preflight(_facts(isolation_column_exists=False))
    assert decision.action == "defer"
    assert decision.reason == "schema_not_ready"


def test_empty_probe_list_refuses_on_an_existing_non_privileged_database() -> None:
    decision = decide_rls_probe_preflight(
        _facts(configured_ids=(), observations=())
    )
    assert decision == PreflightDecision("refuse", "two_distinct_probe_tenants_required")
    assert should_refuse_before_migrate(decision) is True


@pytest.mark.parametrize(
    ("configured", "reason"),
    [
        ((TENANT_A,), "two_distinct_probe_tenants_required"),
        ((TENANT_A, TENANT_A), "two_distinct_probe_tenants_required"),
        ((TENANT_A, TENANT_A.upper()), "two_distinct_probe_tenants_required"),
        ((TENANT_A, TENANT_B, TENANT_A), "two_distinct_probe_tenants_required"),
        (("not-a-uuid", TENANT_B), "invalid_probe_tenant_id"),
        (("[]", TENANT_B), "invalid_probe_tenant_id"),
    ],
)
def test_probe_id_shape_is_refused(configured: tuple[str, ...], reason: str) -> None:
    decision = decide_rls_probe_preflight(_facts(configured_ids=configured))
    assert decision.action == "refuse"
    assert decision.reason == reason


def test_blank_items_are_ignored_when_two_real_ids_remain() -> None:
    decision = decide_rls_probe_preflight(
        _facts(configured_ids=("", f"  {TENANT_A}  ", TENANT_B))
    )
    assert decision == PreflightDecision("allow", "probes_ready")


def test_missing_not_strict_and_personless_probes_are_refused() -> None:
    missing = decide_rls_probe_preflight(
        _facts(observations=(ProbeObservation(TENANT_A, True, "strict", 1),))
    )
    assert missing.reason == "probe_tenant_missing"

    standard = decide_rls_probe_preflight(
        _facts(
            observations=(
                ProbeObservation(TENANT_A, True, "standard", 1),
                ProbeObservation(TENANT_B, True, "strict", 1),
            )
        )
    )
    assert standard.reason == "probe_tenant_not_strict"

    empty = decide_rls_probe_preflight(
        _facts(
            observations=(
                ProbeObservation(TENANT_A, True, "strict", 1),
                ProbeObservation(TENANT_B, True, "strict", 0),
            )
        )
    )
    assert empty.reason == "probe_tenant_has_no_person"


def test_two_strict_tenants_with_a_person_each_are_allowed() -> None:
    assert _ready_pair() == PreflightDecision("allow", "probes_ready")


def test_warn_after_migrate_only_when_the_fresh_database_is_still_not_ready() -> None:
    deferred = PreflightDecision("defer", "schema_not_ready")
    refused = PreflightDecision("refuse", "two_distinct_probe_tenants_required")
    allowed = PreflightDecision("allow", "probes_ready")
    assert should_warn_after_migrate(deferred, refused) is True
    assert should_warn_after_migrate(deferred, deferred) is True
    assert should_warn_after_migrate(deferred, allowed) is False
    assert should_warn_after_migrate(allowed, refused) is False


class _FakeConn:
    def __init__(self, answers: list[Any]) -> None:
        self.answers = answers
        self.queries: list[str] = []
        self.closed = False

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.queries.append(query)
        return self.answers.pop(0)

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.queries.append(query)
        return self.answers.pop(0)

    async def close(self) -> None:
        self.closed = True


def test_loader_reads_the_runtime_role_and_stops_when_tenants_are_absent() -> None:
    runtime = _FakeConn([{"rolsuper": False, "rolbypassrls": False}])
    admin = _FakeConn([False, False])
    seen: list[str] = []

    async def connect(dsn: str) -> _FakeConn:
        seen.append(dsn)
        return runtime if len(seen) == 1 else admin

    facts = asyncio.run(
        load_preflight_facts(
            runtime_dsn="postgresql+asyncpg://runtime:pw@db/contact_ops_db",
            admin_dsn="${CONTACT_OPS_DATABASE_URL}",
            configured_ids=(TENANT_A, TENANT_B),
            connect=connect,
        )
    )

    assert seen[0] == "${CONTACT_OPS_DATABASE_URL}"
    assert seen[1] == "${CONTACT_OPS_DATABASE_URL}"
    assert facts.runtime_privileged is False
    assert facts.tenants_table_exists is False
    assert facts.observations == ()
    assert not any("FROM tenants" in query for query in admin.queries)
    assert runtime.closed is True
    assert admin.closed is True
    decision = decide_rls_probe_preflight(facts)
    assert decision.action == "defer"


def test_loader_records_strict_probe_rows_from_the_admin_connection() -> None:
    runtime = _FakeConn([{"rolsuper": False, "rolbypassrls": True}])
    admin = _FakeConn(
        [
            True,
            True,
            True,
            {"isolation_mode": "strict", "person_count": 1},
            None,
        ]
    )

    async def connect(dsn: str) -> _FakeConn:
        return runtime if "runtime" in dsn else admin

    facts = asyncio.run(
        load_preflight_facts(
            runtime_dsn="${CONTACT_OPS_DATABASE_URL}",
            admin_dsn="${CONTACT_OPS_DATABASE_URL}",
            configured_ids=(TENANT_A, "not-a-uuid", TENANT_B),
            connect=connect,
        )
    )

    assert facts.runtime_privileged is True
    assert [item.tenant_id for item in facts.observations] == [TENANT_A, TENANT_B]
    assert facts.observations[0].exists is True
    assert facts.observations[0].person_count == 1
    assert facts.observations[1].exists is False


def test_loader_failure_is_a_type_name_only() -> None:
    async def connect(dsn: str) -> _FakeConn:
        raise ConnectionError("${CONTACT_OPS_DATABASE_URL}")

    with pytest.raises(PreflightUnavailable) as caught:
        asyncio.run(
            load_preflight_facts(
                runtime_dsn="${CONTACT_OPS_DATABASE_URL}",
                admin_dsn="${CONTACT_OPS_DATABASE_URL}",
                configured_ids=(),
                connect=connect,
            )
        )
    assert caught.value.error_type == "ConnectionError"
    assert "secret" not in str(caught.value)


def test_prestart_refuses_before_migrate(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        prestart,
        "_preflight_decision",
        lambda *, fatal: PreflightDecision("refuse", "probe_tenant_missing"),
    )
    monkeypatch.setattr(
        prestart,
        "_run_migrations",
        lambda: (_ for _ in ()).throw(AssertionError("migrated")),
    )
    monkeypatch.setattr(
        prestart.os,
        "execv",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("execv")),
    )
    monkeypatch.setattr(
        prestart.logger,
        "error",
        lambda event, **kwargs: events.append((event, kwargs)),
    )

    with pytest.raises(SystemExit) as caught:
        prestart.main()

    assert caught.value.code == 1
    assert events[0][0] == "rls_probe_preflight_refused"
    assert events[0][1]["seed_command"] == SEED_COMMAND
    assert "seed_rls_probes" in events[0][1]["note"]


def test_prestart_migrates_a_fresh_database_and_still_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def decide(*, fatal: bool) -> PreflightDecision:
        calls["n"] += 1
        if calls["n"] == 1:
            return PreflightDecision("defer", "schema_not_ready")
        return PreflightDecision("refuse", "two_distinct_probe_tenants_required")

    events: list[str] = []
    migrated = {"yes": False}
    monkeypatch.setattr(prestart, "_preflight_decision", decide)
    monkeypatch.setattr(
        prestart, "_run_migrations", lambda: migrated.__setitem__("yes", True)
    )
    monkeypatch.setattr(prestart, "_finish_schema", lambda: None)
    monkeypatch.setattr(
        prestart.os, "execv", lambda *args, **kwargs: (_ for _ in ()).throw(SystemExit(0))
    )
    monkeypatch.setattr(
        prestart.logger, "error", lambda event, **kwargs: events.append(event)
    )

    with pytest.raises(SystemExit) as caught:
        prestart.main()

    assert caught.value.code == 0
    assert migrated["yes"] is True
    assert events == ["rls_probe_preflight_missing_after_migrate"]


def test_prestart_unreachable_exits_before_migrate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(**kwargs: Any) -> PreflightFacts:
        raise PreflightUnavailable("ConnectionRefusedError")

    monkeypatch.setattr(prestart, "load_preflight_facts", boom)
    monkeypatch.setattr(
        prestart,
        "_run_migrations",
        lambda: (_ for _ in ()).throw(AssertionError("migrated")),
    )

    with pytest.raises(SystemExit) as caught:
        prestart._preflight_decision(fatal=True)

    assert caught.value.code == 1
