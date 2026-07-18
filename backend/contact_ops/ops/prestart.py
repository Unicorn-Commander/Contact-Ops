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


def _asyncpg_dsn(url: str) -> str:
    """Strip any SQLAlchemy driver suffix so ``asyncpg.connect`` accepts the DSN."""
    for driver in (
        "postgresql+asyncpg://",
        "postgresql+psycopg://",
        "postgresql+psycopg2://",
        "postgres://",
    ):
        if url.startswith(driver):
            return "postgresql://" + url.split("://", 1)[1]
    return url


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

    dsn = _asyncpg_dsn(migration_url)
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


def main() -> None:
    _run_migrations()
    asyncio.run(_assert_schema())
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
