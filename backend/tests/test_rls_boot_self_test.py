"""Unit coverage for the boot RLS isolation contract."""

from __future__ import annotations

import pytest
from fastapi import Response
from starlette.requests import Request

from contact_ops.core.config import Settings
from contact_ops.ops.rls_status import (
    RLSSelfTestError,
    evaluate_rls_isolation,
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
    with pytest.raises(RLSSelfTestError):
        evaluate_rls_isolation(
            privileged=False, probe_counts=(1, 1), cross_counts=(0, 2)
        )


def test_privileged_rls_decision_is_explicitly_inert() -> None:
    assert evaluate_rls_isolation(
        privileged=True, probe_counts=(1, 1), cross_counts=(4, 3)
    ) == "inert_privileged"


def test_migration_database_url_is_validated() -> None:
    with pytest.raises(ValueError):
        Settings(
            DATABASE_URL="postgresql+asyncpg://example.invalid/db",
            MIGRATION_DATABASE_URL="not-a-dsn",
        )


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
