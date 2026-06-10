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


def _run_migrations() -> None:
    settings = get_settings()
    # Alembic env.py reads MIGRATION_DATABASE_URL/ALEMBIC_DATABASE_URL/DATABASE_URL
    # from os.environ. Use Settings so the declared config field is honored.
    os.environ["MIGRATION_DATABASE_URL"] = (
        settings.MIGRATION_DATABASE_URL or settings.DATABASE_URL
    )
    cfg_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(cfg_path))
    logger.info("alembic_upgrade_starting", target="head")
    command.upgrade(cfg, "head")
    logger.info("alembic_upgrade_completed", target="head")


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
