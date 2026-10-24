"""Idempotent RLS probe seed. The directory is in memory; no Postgres."""

from __future__ import annotations

import asyncio

import pytest

from contact_ops.ops.seed_rls_probes import (
    PROBE_SPECS,
    FoundTenant,
    NewTenant,
    SeedError,
    _safe_error_text,
    format_seed_output,
    raise_for_schema_gap,
    resolve_migration_dsn,
    seed_probes,
)

OWNER = "00000000-0000-0000-0000-000000000001"


class MemoryDirectory:
    def __init__(self, *, privileged: bool = True) -> None:
        self.privileged = privileged
        self.tenants: dict[str, FoundTenant] = {}
        self.persons: dict[str, int] = {}
        self.rows: dict[str, NewTenant] = {}
        self.gucs: list[tuple[str, str]] = []
        self.write_count = 0

    async def role_is_privileged(self) -> bool:
        return self.privileged

    async def tenant_by_slug(self, slug: str) -> FoundTenant | None:
        for tenant in self.tenants.values():
            if tenant.slug == slug:
                return tenant
        return None

    async def tenant_by_id(self, tenant_id: str) -> FoundTenant | None:
        return self.tenants.get(tenant_id)

    async def insert_tenant(self, tenant: NewTenant) -> str | None:
        if tenant.tenant_id in self.tenants or any(
            row.slug == tenant.slug for row in self.tenants.values()
        ):
            return None
        self.tenants[tenant.tenant_id] = FoundTenant(
            tenant.tenant_id, tenant.slug, "strict"
        )
        self.rows[tenant.tenant_id] = tenant
        self.write_count += 1
        return tenant.tenant_id

    async def person_count(self, tenant_id: str) -> int:
        return self.persons.get(tenant_id, 0)

    async def insert_person(
        self,
        *,
        tenant_id: str,
        display_name: str,
        vcard_uid: str,
        guc_name: str,
    ) -> None:
        self.gucs.append((guc_name, tenant_id))
        self.persons[tenant_id] = self.persons.get(tenant_id, 0) + 1
        self.write_count += 1
        assert display_name
        assert vcard_uid == f"rls-probe-{tenant_id}"


def _seed(directory: MemoryDirectory) -> list[str]:
    return asyncio.run(
        seed_probes(directory, owner_user_id=OWNER, guc_name="app.tenant_id")
    )


def test_seed_probes_is_idempotent_and_prints_the_env_line() -> None:
    directory = MemoryDirectory()
    first = _seed(directory)
    writes = directory.write_count
    gucs = list(directory.gucs)
    second = _seed(directory)

    assert first == [spec.preferred_id for spec in PROBE_SPECS]
    assert second == first
    assert directory.write_count == writes
    assert directory.gucs == gucs
    assert directory.persons == {tenant_id: 1 for tenant_id in first}
    created = directory.rows[first[0]]
    assert created.slug == "rls-probe-a"
    assert created.qdrant_namespace == "contact_ops__rls-probe-a"
    assert created.garage_bucket_prefix == "contact-ops-rls-probe-a"
    assert created.graph_name == "contact_ops__rls_probe_a"
    assert created.owner_user_id == OWNER
    assert format_seed_output(first) == (
        f"{first[0]},{first[1]}\n"
        f"RLS_SELFTEST_TENANT_IDS={first[0]},{first[1]}\n"
    )


def test_second_run_keeps_an_existing_strict_tenant_and_its_person() -> None:
    existing = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    directory = MemoryDirectory()
    directory.tenants[existing] = FoundTenant(existing, "rls-probe-a", "strict")
    directory.persons[existing] = 1

    first = _seed(directory)
    writes = directory.write_count
    second = _seed(directory)

    assert first[0] == existing
    assert first[1] == PROBE_SPECS[1].preferred_id
    assert second == first
    assert directory.write_count == writes
    assert directory.persons[existing] == 1
    assert directory.gucs == [("app.tenant_id", first[1])]


def test_legacy_selfhost_slug_is_reused_and_not_rewritten() -> None:
    legacy_id = PROBE_SPECS[0].preferred_id
    directory = MemoryDirectory()
    directory.tenants[legacy_id] = FoundTenant(legacy_id, "selfhost-probe-a", "strict")

    first = _seed(directory)
    writes = directory.write_count
    second = _seed(directory)

    assert first[0] == legacy_id
    assert directory.tenants[legacy_id].slug == "selfhost-probe-a"
    assert "rls-probe-a" not in {tenant.slug for tenant in directory.tenants.values()}
    assert directory.persons[legacy_id] == 1
    assert second == first
    assert directory.write_count == writes


def test_non_strict_slug_writes_nothing() -> None:
    directory = MemoryDirectory()
    directory.tenants["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"] = FoundTenant(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "rls-probe-a", "standard"
    )

    with pytest.raises(SeedError, match="isolation_mode is standard"):
        _seed(directory)

    assert directory.write_count == 0
    assert directory.persons == {}


def test_unprivileged_role_writes_nothing() -> None:
    directory = MemoryDirectory(privileged=False)

    with pytest.raises(SeedError, match="BYPASSRLS"):
        _seed(directory)

    assert directory.write_count == 0


def test_preferred_id_owned_by_another_slug_is_not_taken() -> None:
    preferred = PROBE_SPECS[0].preferred_id
    directory = MemoryDirectory()
    directory.tenants[preferred] = FoundTenant(preferred, "aaron-personal", "strict")

    with pytest.raises(SeedError, match="rls-probe-a"):
        _seed(directory)

    assert directory.persons == {}
    assert set(directory.tenants) == {preferred}


def test_invalid_owner_and_guc_write_nothing() -> None:
    directory = MemoryDirectory()
    with pytest.raises(SeedError, match="SEED_OWNER_USER_ID"):
        asyncio.run(seed_probes(directory, owner_user_id="not-a-uuid", guc_name="app.tenant_id"))
    with pytest.raises(SeedError, match="TENANT_GUC_NAME"):
        asyncio.run(seed_probes(directory, owner_user_id=OWNER, guc_name="app tenant"))
    assert directory.write_count == 0


def test_cli_prints_only_the_ids_and_env_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from contact_ops.ops import seed_rls_probes as seed

    ids = [spec.preferred_id for spec in PROBE_SPECS]

    async def fake_run() -> list[str]:
        return ids

    monkeypatch.setattr(seed, "_run", fake_run)
    seed.main()
    out, err = capsys.readouterr()
    assert err == ""
    assert out == format_seed_output(ids)
    assert out.count("\n") == 2


def test_cli_does_not_print_a_dsn_from_an_unexpected_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from contact_ops.ops import seed_rls_probes as seed

    async def fake_run() -> list[str]:
        raise RuntimeError("${CONTACT_OPS_DATABASE_URL}")

    monkeypatch.setattr(seed, "_run", fake_run)
    with pytest.raises(SystemExit) as caught:
        seed.main()
    out, err = capsys.readouterr()
    assert caught.value.code == 1
    assert out == ""
    assert "secret-token" not in err
    assert "postgresql://" not in err
    assert "RuntimeError" in err


def test_missing_migration_url_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SeedError, match="MIGRATION_DATABASE_URL"):
        resolve_migration_dsn()


def test_schema_gap_and_safe_error_text_hide_urls() -> None:
    class Missing(Exception):
        sqlstate = "42P01"

    with pytest.raises(SeedError, match="alembic upgrade head"):
        raise_for_schema_gap(Missing("relation tenants does not exist"))
    assert _safe_error_text(RuntimeError("relation tenants does not exist")) == (
        "RuntimeError: relation tenants does not exist"
    )
    hidden = _safe_error_text(RuntimeError("${CONTACT_OPS_DATABASE_URL}"))
    assert hidden == "RuntimeError"
    assert "secret" not in hidden
