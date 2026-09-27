"""Unit coverage for the boot RLS isolation contract."""

from __future__ import annotations

import pytest
from fastapi import Response
from starlette.requests import Request

import contact_ops.main as main_module
from contact_ops.core.config import Settings
from contact_ops.ops.rls_status import (
    RLSSelfTestError,
    evaluate_rls_isolation,
    get_rls_status,
    set_rls_status,
)
from contact_ops.routers.health import rls_health_check


def test_rls_selftest_ids_accept_comma_separated_uuid_values() -> None:
    settings = Settings(
        DATABASE_URL="postgresql+asyncpg://example.invalid/db",
        RLS_SELFTEST_TENANT_IDS=(
            "00000000-0000-0000-0000-000000000001, "
            "00000000-0000-0000-0000-000000000002"
        ),
    )

    assert settings.RLS_SELFTEST_TENANT_IDS == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ]


@pytest.mark.parametrize(
    "probe_ids",
    [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000001,00000000-0000-0000-0000-000000000001",
    ],
)
def test_rls_selftest_configuration_is_not_silently_two_tenant_ready(
    probe_ids: str,
) -> None:
    settings = Settings(
        DATABASE_URL="postgresql+asyncpg://example.invalid/db",
        RLS_SELFTEST_TENANT_IDS=probe_ids,
    )

    assert len(settings.RLS_SELFTEST_TENANT_IDS) != 2 or len(
        set(settings.RLS_SELFTEST_TENANT_IDS)
    ) != 2


def test_rls_decision_requires_zero_foreign_rows_in_both_directions() -> None:
    assert evaluate_rls_isolation(
        privileged=False, probe_counts=(1, 1), cross_counts=(0, 0)
    ) == "passed"
    for cross_counts in ((2, 0), (0, 2)):
        with pytest.raises(RLSSelfTestError):
            evaluate_rls_isolation(
                privileged=False, probe_counts=(1, 1), cross_counts=cross_counts
            )


def test_privileged_rls_decision_is_explicitly_inert() -> None:
    assert evaluate_rls_isolation(
        privileged=True, probe_counts=(1, 1), cross_counts=(4, 3)
    ) == "inert_privileged"


def test_rls_decision_rejects_unseeded_probe() -> None:
    with pytest.raises(RLSSelfTestError, match="no visible persons"):
        evaluate_rls_isolation(
            privileged=False, probe_counts=(1, 0), cross_counts=(0, 0)
        )


def test_migration_database_url_is_validated() -> None:
    with pytest.raises(ValueError):
        Settings(
            DATABASE_URL="postgresql+asyncpg://example.invalid/db",
            MIGRATION_DATABASE_URL="not-a-dsn",
        )
    with pytest.raises(ValueError, match="database name"):
        Settings(
            DATABASE_URL="postgresql+asyncpg://example.invalid/db",
            MIGRATION_DATABASE_URL="${CONTACT_OPS_DATABASE_URL}",
        )
    assert Settings(
        DATABASE_URL="postgresql+asyncpg://example.invalid/db",
        MIGRATION_DATABASE_URL=None,
    ).MIGRATION_DATABASE_URL is None
    assert Settings(
        DATABASE_URL="postgresql+asyncpg://example.invalid/db",
        MIGRATION_DATABASE_URL="",
    ).MIGRATION_DATABASE_URL == ""


class _FakeResult:
    def __init__(self, *, row=None, scalar=None):
        self._row = row
        self._scalar = scalar

    def first(self):
        return self._row

    def scalar_one(self):
        return self._scalar


class _FakeTransaction:
    async def rollback(self) -> None:
        return None


class _FakeConnection:
    def __init__(
        self,
        *,
        privileged=False,
        isolation="strict",
        probe_counts=(1, 1),
        cross_counts=(0, 0),
        error=None,
    ):
        self.privileged = privileged
        self.isolation = isolation
        self.probe_counts = iter(probe_counts)
        self.cross_counts = iter(cross_counts)
        self.error = error

    async def begin(self):
        return _FakeTransaction()

    async def execute(self, statement, params=None):
        sql = str(statement)
        if self.error is not None and "set_config" in sql:
            raise self.error
        if "FROM pg_roles" in sql:
            return _FakeResult(row=(self.privileged, self.privileged))
        if "set_config" in sql:
            return _FakeResult()
        if "current_tenant_isolation" in sql:
            return _FakeResult(scalar=self.isolation)
        if "canonical_owner_tenant_id =" in sql:
            return _FakeResult(scalar=next(self.probe_counts))
        if "canonical_owner_tenant_id <>" in sql:
            return _FakeResult(scalar=next(self.cross_counts))
        raise AssertionError(f"unexpected SQL: {sql}")


class _FakeEngine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        engine = self

        class _ConnectionContext:
            async def __aenter__(self):
                return engine.connection

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _ConnectionContext()


def _boot_settings(
    probe_ids: str = (
        "00000000-0000-0000-0000-000000000001,"
        "00000000-0000-0000-0000-000000000002"
    ),
) -> Settings:
    return Settings(
        DATABASE_URL="postgresql+asyncpg://example.invalid/db",
        RLS_SELFTEST_TENANT_IDS=probe_ids,
    )


@pytest.mark.asyncio
async def test_boot_empty_probe_list_refuses_non_privileged_role(monkeypatch) -> None:
    connection = _FakeConnection(privileged=False)
    monkeypatch.setattr("contact_ops.core.database.engine", _FakeEngine(connection))
    with pytest.raises(RLSSelfTestError, match="exactly two"):
        await main_module._rls_boot_self_test(_boot_settings(""))


@pytest.mark.asyncio
async def test_boot_empty_probe_list_is_inert_for_privileged_role(monkeypatch) -> None:
    connection = _FakeConnection(privileged=True)
    monkeypatch.setattr("contact_ops.core.database.engine", _FakeEngine(connection))
    await main_module._rls_boot_self_test(_boot_settings(""))
    assert get_rls_status() == {"status": "inert_privileged"}


@pytest.mark.asyncio
async def test_boot_strict_mode_gate_refuses_non_strict_probe(monkeypatch) -> None:
    connection = _FakeConnection(isolation="standard")
    monkeypatch.setattr("contact_ops.core.database.engine", _FakeEngine(connection))
    with pytest.raises(RLSSelfTestError, match="strict isolation"):
        await main_module._rls_boot_self_test(_boot_settings())


@pytest.mark.asyncio
async def test_boot_diagnostic_error_is_re_raised(monkeypatch) -> None:
    diagnostic_error = RuntimeError("driver detail includes probe UUID")
    connection = _FakeConnection(error=diagnostic_error)
    monkeypatch.setattr("contact_ops.core.database.engine", _FakeEngine(connection))
    with pytest.raises(RuntimeError, match="driver detail") as caught:
        await main_module._rls_boot_self_test(_boot_settings())
    assert caught.value is diagnostic_error


def _request_with_claims(claims: dict) -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/health/rls"})
    request.state.jwt_claims = claims
    return request


@pytest.mark.asyncio
async def test_rls_health_redacts_probe_details_for_ordinary_callers() -> None:
    set_rls_status(
        "passed",
        reason="self_test_passed",
        bound_tenant="00000000-0000-0000-0000-000000000001",
        other_tenant="00000000-0000-0000-0000-000000000002",
        cross_tenant_person_counts=[0, 0],
    )
    body = await rls_health_check(_request_with_claims({}), Response())
    assert body == {"status": "passed", "reason": "self_test_passed"}
    assert "bound_tenant" not in body
    assert "other_tenant" not in body
    assert "cross_tenant_person_counts" not in body


@pytest.mark.asyncio
async def test_rls_health_uses_redacted_production_success_shape() -> None:
    set_rls_status(
        "passed",
        bound_tenant="00000000-0000-0000-0000-000000000001",
        other_tenant="00000000-0000-0000-0000-000000000002",
        cross_tenant_person_counts=[0, 0],
    )
    status = get_rls_status()
    assert "reason" not in status
    response = Response()
    body = await rls_health_check(_request_with_claims({}), response)
    assert response.status_code == 200
    assert body == {"status": "passed", "reason": "self_test_passed"}


@pytest.mark.asyncio
async def test_rls_health_details_require_admin_debug() -> None:
    set_rls_status(
        "failed",
        reason="cross_tenant_leak",
        bound_tenant="tenant-a",
        other_tenant="tenant-b",
        cross_tenant_person_counts=[1, 0],
    )
    ordinary = await rls_health_check(
        _request_with_claims(
            {"realm_access": {"roles": ["ADMIN"]}, "scope": "contacts:read"}
        ),
        Response(),
    )
    detail = await rls_health_check(
        _request_with_claims(
            {"realm_access": {"roles": ["ADMIN"]}, "scope": "admin:debug"}
        ),
        Response(),
    )
    assert ordinary == {"status": "failed", "reason": "cross_tenant_leak"}
    assert detail["bound_tenant"] == "tenant-a"
    assert detail["cross_tenant_person_counts"] == [1, 0]
