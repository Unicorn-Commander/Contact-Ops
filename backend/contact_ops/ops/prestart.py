"""Run DB migrations, assert schema revision, then exec uvicorn."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import structlog
from alembic.config import Config

from alembic import command
from contact_ops.core.config import get_settings
from contact_ops.core.database import engine
from contact_ops.ops.rls_probe_preflight import (
    PreflightDecision,
    PreflightUnavailable,
    SEED_COMMAND,
    asyncpg_dsn,
    decide_rls_probe_preflight,
    load_preflight_facts,
    probe_gap_note,
    should_refuse_before_migrate,
    should_warn_after_migrate,
)
from contact_ops.ops.schema_guard import assert_schema_current

logger = structlog.get_logger(__name__)


# Fixed 64-bit key for the Postgres session advisory lock that serialises
# ``alembic upgrade head`` across backend replicas. Two replicas booting at once
# would otherwise both run the migration and race (duplicate DDL, revision-table
# contention). The first replica to boot takes the lock and migrates; the others
# block on the lock, then run an upgrade that is a no-op (already at head). The
# value is arbitrary but MUST be stable across replicas — derived from the ASCII
# of "CoOp" so it reads in ``pg_locks`` and can't collide with app-level locks by
# accident.
_MIGRATION_ADVISORY_LOCK_KEY = 0x436F4F70  # 1131368304 — ASCII "CoOp"


def _log_probe_gap(reason: str, *, after_migrate: bool) -> None:
    logger.error(
        "rls_probe_preflight_missing_after_migrate"
        if after_migrate
        else "rls_probe_preflight_refused",
        reason=reason,
        seed_command=SEED_COMMAND,
        note=probe_gap_note(),
    )


def _preflight_decision(*, fatal: bool) -> PreflightDecision | None:
    """Read the runtime role and the probe tenants. Does not migrate.

    ``fatal`` exits when the database cannot be classified. The post-migration
    recheck passes ``fatal=False`` so a fresh database still reaches uvicorn,
    where the existing boot self-test refuses.
    """
    settings = get_settings()
    migration_url = settings.MIGRATION_DATABASE_URL or settings.DATABASE_URL
    # Alembic env.py reads this name. Set it before either the preflight admin
    # connection or the upgrade so both see the configured migration URL.
    os.environ["MIGRATION_DATABASE_URL"] = migration_url
    try:
        facts = asyncio.run(
            load_preflight_facts(
                runtime_dsn=settings.DATABASE_URL,
                admin_dsn=migration_url,
                configured_ids=tuple(settings.RLS_SELFTEST_TENANT_IDS),
            )
        )
    except PreflightUnavailable as exc:
        logger.error(
            "rls_probe_preflight_unreachable",
            error_type=exc.error_type,
            seed_command=SEED_COMMAND,
            note=probe_gap_note(),
        )
        if fatal:
            raise SystemExit(1) from None
        return None
    return decide_rls_probe_preflight(facts)


def _run_migrations() -> None:
    settings = get_settings()
    # Alembic env.py reads MIGRATION_DATABASE_URL/ALEMBIC_DATABASE_URL/DATABASE_URL
    # from os.environ. Use Settings so the declared config field is honored.
    migration_url = settings.MIGRATION_DATABASE_URL or settings.DATABASE_URL
    os.environ["MIGRATION_DATABASE_URL"] = migration_url
    cfg_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(cfg_path))

    # Hold a session-level advisory lock on a DEDICATED connection for the whole
    # upgrade so only one replica migrates at a time. The lock lives server-side,
    # tied to this TCP session, so it survives across ``command.upgrade`` (which
    # opens its own asyncpg connections + event loop internally). We keep the
    # lock connection open — but idle — for the duration and release it after.
    import asyncio

    import asyncpg

    dsn = asyncpg_dsn(migration_url)
    lock_loop = asyncio.new_event_loop()
    lock_conn = None
    try:
        lock_conn = lock_loop.run_until_complete(asyncpg.connect(dsn))
        logger.info("alembic_migration_lock_acquiring", key=_MIGRATION_ADVISORY_LOCK_KEY)
        lock_loop.run_until_complete(
            lock_conn.execute("SELECT pg_advisory_lock($1)", _MIGRATION_ADVISORY_LOCK_KEY)
        )
        logger.info("alembic_migration_lock_acquired", key=_MIGRATION_ADVISORY_LOCK_KEY)

        logger.info("alembic_upgrade_starting", target="head")
        command.upgrade(cfg, "head")
        logger.info("alembic_upgrade_completed", target="head")
    finally:
        if lock_conn is not None:
            try:
                lock_loop.run_until_complete(
                    lock_conn.execute(
                        "SELECT pg_advisory_unlock($1)", _MIGRATION_ADVISORY_LOCK_KEY
                    )
                )
            finally:
                lock_loop.run_until_complete(lock_conn.close())
        lock_loop.close()


async def _assert_schema() -> None:
    version = await assert_schema_current(engine)
    logger.info(
        "schema_version_current",
        expected=version.expected,
        actual=version.actual,
    )


def _finish_schema() -> None:
    asyncio.run(_assert_schema())


def main() -> None:
    # An existing database whose runtime role would fail the boot RLS self-test
    # must not be migrated first. A fresh database has no tenants table yet, so
    # it still migrates; the boot self-test then refuses until the probes exist.
    before = _preflight_decision(fatal=True)
    if before is None or should_refuse_before_migrate(before):
        if before is not None:
            _log_probe_gap(before.reason, after_migrate=False)
        raise SystemExit(1)
    _run_migrations()
    if before.action == "defer":
        after = _preflight_decision(fatal=False)
        if after is None:
            _log_probe_gap("preflight_unreachable", after_migrate=True)
        elif should_warn_after_migrate(before, after):
            _log_probe_gap(after.reason, after_migrate=True)
    _finish_schema()
    os.execv(  # noqa: S606 - prestart intentionally replaces itself with uvicorn.
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "contact_ops.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8501",
        ],
    )


if __name__ == "__main__":
    main()
