"""Unit coverage for the boot RLS isolation contract."""

from __future__ import annotations

import pytest

from contact_ops.core.config import Settings


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
