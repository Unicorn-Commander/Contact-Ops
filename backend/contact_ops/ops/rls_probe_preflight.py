"""Decide whether prestart may run ``alembic upgrade head``.

The boot RLS self-test in ``contact_ops.main`` still refuses to serve traffic
when a non-privileged role has no two strict probe tenants. This module only
moves that discovery to *before* the migration when the database already has a
``tenants`` table, so an upgrade cannot leave a migrated schema behind a
crash-looping process.

A fresh database (no ``tenants`` table, or no ``isolation_mode`` column yet)
is deferred: prestart migrates, then logs that the probes are missing. It does
not weaken the boot self-test.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

SEED_COMMAND = "python -m contact_ops.ops.seed_rls_probes"

PreflightAction = Literal["allow", "refuse", "defer"]


class PreflightUnavailable(RuntimeError):
    """The runtime role or the catalog could not be read. ``error_type`` is safe to log."""

    def __init__(self, error_type: str) -> None:
        self.error_type = error_type
        super().__init__(error_type)


@dataclass(frozen=True)
class ProbeObservation:
    tenant_id: str
    exists: bool
    isolation_mode: str | None
    person_count: int


@dataclass(frozen=True)
class PreflightFacts:
    runtime_privileged: bool
    tenants_table_exists: bool
    persons_table_exists: bool
    isolation_column_exists: bool
    configured_ids: tuple[str, ...]
    observations: tuple[ProbeObservation, ...]


@dataclass(frozen=True)
class PreflightDecision:
    action: PreflightAction
    reason: str


def asyncpg_dsn(url: str) -> str:
    """Strip a SQLAlchemy driver suffix so ``asyncpg.connect`` accepts the DSN."""
    for driver in (
        "postgresql+asyncpg://",
        "postgresql+psycopg://",
        "postgresql+psycopg2://",
        "postgres://",
    ):
        if url.startswith(driver):
            return "postgresql://" + url.split("://", 1)[1]
    return url


def _canonical_configured_ids(raw: Sequence[str]) -> list[str] | None:
    """Return canonical UUID strings, or None when any non-blank item is not a UUID."""
    canonical: list[str] = []
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        try:
            canonical.append(str(UUID(text)))
        except ValueError:
            return None
    return canonical


def decide_rls_probe_preflight(facts: PreflightFacts) -> PreflightDecision:
    """Pure decision. A privileged runtime role matches the boot self-test and is allowed.

    Schema that cannot express the probe contract yet (no tenants table, no
    persons table, or no ``isolation_mode`` column) is deferred so the migration
    that creates it can still run.
    """
    if facts.runtime_privileged:
        return PreflightDecision("allow", "runtime_privileged")
    schema_ready = (
        facts.tenants_table_exists
        and facts.persons_table_exists
        and facts.isolation_column_exists
    )
    if not schema_ready:
        return PreflightDecision("defer", "schema_not_ready")

    canonical = _canonical_configured_ids(facts.configured_ids)
    if canonical is None:
        return PreflightDecision("refuse", "invalid_probe_tenant_id")
    if len(canonical) != 2 or len(set(canonical)) != 2:
        return PreflightDecision("refuse", "two_distinct_probe_tenants_required")

    observed = {item.tenant_id: item for item in facts.observations}
    for tenant_id in canonical:
        row = observed.get(tenant_id)
        if row is None or not row.exists:
            return PreflightDecision("refuse", "probe_tenant_missing")
        if row.isolation_mode != "strict":
            return PreflightDecision("refuse", "probe_tenant_not_strict")
        if row.person_count < 1:
            return PreflightDecision("refuse", "probe_tenant_has_no_person")
    return PreflightDecision("allow", "probes_ready")


def should_refuse_before_migrate(decision: PreflightDecision) -> bool:
    return decision.action == "refuse"


def should_warn_after_migrate(before: PreflightDecision, after: PreflightDecision) -> bool:
    """A fresh database migrates, then the boot self-test still enforces the probes."""
    return before.action == "defer" and after.action != "allow"


def probe_gap_note() -> str:
    return (
        f"Run `{SEED_COMMAND}` with MIGRATION_DATABASE_URL, "
        "then set RLS_SELFTEST_TENANT_IDS to the printed line. "
        "The boot RLS self-test is unchanged."
    )


async def load_preflight_facts(
    *,
    runtime_dsn: str,
    admin_dsn: str,
    configured_ids: Sequence[str],
    connect: Any = None,
) -> PreflightFacts:
    """Read the runtime role on ``runtime_dsn`` and the catalog on the admin DSN.

    The admin connection is required because the runtime role cannot see tenants
    it is not bound to. Observations are existence facts, not an RLS proof; the
    boot self-test still proves isolation.
    """
    if connect is None:
        import asyncpg

        connect = asyncpg.connect

    runtime = None
    admin = None
    try:
        runtime = await connect(asyncpg_dsn(runtime_dsn))
        admin = await connect(asyncpg_dsn(admin_dsn))
        role = await runtime.fetchrow(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        privileged = bool(role and (role["rolsuper"] or role["rolbypassrls"]))
        tenants_table_exists = bool(
            await admin.fetchval("SELECT to_regclass('public.tenants') IS NOT NULL")
        )
        persons_table_exists = bool(
            await admin.fetchval("SELECT to_regclass('public.persons') IS NOT NULL")
        )
        isolation_column_exists = False
        observations: list[ProbeObservation] = []
        if tenants_table_exists:
            isolation_column_exists = bool(
                await admin.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'tenants'
                          AND column_name = 'isolation_mode'
                    )
                    """
                )
            )
        if tenants_table_exists and persons_table_exists and isolation_column_exists:
            for raw in configured_ids:
                text = str(raw).strip()
                if not text:
                    continue
                try:
                    tenant_id = str(UUID(text))
                except ValueError:
                    continue
                row = await admin.fetchrow(
                    """
                    SELECT t.isolation_mode,
                           (
                               SELECT count(*)
                               FROM persons p
                               WHERE p.canonical_owner_tenant_id = t.id
                           ) AS person_count
                    FROM tenants t
                    WHERE t.id = $1::uuid
                    """,
                    tenant_id,
                )
                if row is None:
                    observations.append(
                        ProbeObservation(tenant_id, False, None, 0)
                    )
                else:
                    observations.append(
                        ProbeObservation(
                            tenant_id,
                            True,
                            str(row["isolation_mode"]),
                            int(row["person_count"]),
                        )
                    )
        return PreflightFacts(
            runtime_privileged=privileged,
            tenants_table_exists=tenants_table_exists,
            persons_table_exists=persons_table_exists,
            isolation_column_exists=isolation_column_exists,
            configured_ids=tuple(configured_ids),
            observations=tuple(observations),
        )
    except PreflightUnavailable:
        raise
    except Exception as exc:
        raise PreflightUnavailable(type(exc).__name__) from exc
    finally:
        if runtime is not None:
            await runtime.close()
        if admin is not None:
            await admin.close()
