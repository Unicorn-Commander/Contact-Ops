"""Schema-version guard shared by prestart and readiness checks."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

EXPECTED_ALEMBIC_REVISION = "0045"


@dataclass(frozen=True)
class SchemaVersion:
    expected: str
    actual: str | None

    @property
    def matches(self) -> bool:
        return self.actual == self.expected


class SchemaVersionMismatchError(RuntimeError):
    """Raised when the DB schema revision does not match the running code."""

    def __init__(self, version: SchemaVersion) -> None:
        self.version = version
        super().__init__(
            "database schema revision mismatch: "
            f"expected {version.expected}, got {version.actual or '<none>'}"
        )


async def get_schema_version(engine: AsyncEngine) -> SchemaVersion:
    async with engine.connect() as conn:
        actual = await conn.scalar(text("SELECT version_num FROM alembic_version"))
    return SchemaVersion(
        expected=EXPECTED_ALEMBIC_REVISION,
        actual=str(actual) if actual else None,
    )


async def assert_schema_current(engine: AsyncEngine) -> SchemaVersion:
    version = await get_schema_version(engine)
    if not version.matches:
        raise SchemaVersionMismatchError(version)
    return version
