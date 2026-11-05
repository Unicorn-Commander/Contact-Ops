"""Fail-closed TTL scope and shadow telemetry for the retention sweep."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from contact_ops.agents import retention_sweep
from contact_ops.core.config import Settings


def _settings(scope: str, *, shadow: bool = True) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://example.invalid/contact_ops_db",
        MIGRATION_DATABASE_URL="${CONTACT_OPS_DATABASE_URL}",
        RETENTION_SWEEP_SHADOW=shadow,
        RETENTION_SWEEP_TTL_SCOPE=scope,
        RETENTION_SWEEP_BATCH_LIMIT=50,
    )


@pytest.mark.asyncio
async def test_empty_scope_keeps_tombstones_only() -> None:
    """An empty scope reaches the existing tombstone branch, never TTL."""
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(all=lambda: [("tombstone-person", "tombstone")])
    )

    selected = await retention_sweep._candidates_for_tenant(
        db, settings=_settings(""), limit=50
    )

    assert selected == ["tombstone-person"]
    statement, params = db.execute.await_args.args
    query = str(statement)
    assert params["ttl_scope"] == []
    assert "tombstoned_at IS NOT NULL AND purge_after <= now()" in query
    assert "person_tenant_membership" in query
    assert "&& CAST(:ttl_scope AS text[])" in query


@pytest.mark.asyncio
async def test_empty_scope_logs_once_per_pass(monkeypatch) -> None:
    monkeypatch.setattr(
        retention_sweep,
        "_enumerate_tenant_ids",
        AsyncMock(return_value=[]),
    )

    with capture_logs() as logs:
        await retention_sweep._sweep_async(_settings(""))

    empty_scope_logs = [
        item for item in logs if item.get("event") == "retention_sweep_ttl_scope_empty"
    ]
    assert len(empty_scope_logs) == 1


@pytest.mark.asyncio
async def test_scoped_ttl_query_uses_exact_tag_scope() -> None:
    """A configured tag scope is passed as an exact array overlap filter."""
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(all=lambda: [("website-lead-person", "ttl")])
    )

    selected = await retention_sweep._candidates_for_tenant(
        db,
        settings=_settings(" website-assistant-lead,other-scoped-tag "),
        limit=50,
    )

    assert selected == ["website-lead-person"]
    _statement, params = db.execute.await_args.args
    assert params["ttl_scope"] == ["website-assistant-lead", "other-scoped-tag"]


@pytest.mark.asyncio
async def test_unscoped_and_legal_hold_rows_are_not_ttl_candidates() -> None:
    """The SQL keeps an unscoped aged person and legal holds out of TTL."""
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: []))

    assert await retention_sweep._candidates_for_tenant(
        db, settings=_settings("website-assistant-lead"), limit=50
    ) == []

    statement, _params = db.execute.await_args.args
    query = str(statement)
    assert "legal_hold = false" in query
    assert "COALESCE(retention_class::text, 'operational_2y')" in query


@pytest.mark.asyncio
async def test_shadow_reports_reason_counts_without_purging(monkeypatch) -> None:
    """Shadow mode emits both reason counts and does not open a purge unit."""
    settings = _settings("website-assistant-lead")
    monkeypatch.setattr(
        retention_sweep,
        "_enumerate_tenant_ids",
        AsyncMock(return_value=["tenant-fictional"]),
    )
    monkeypatch.setattr(
        retention_sweep,
        "_candidate_rows_for_tenant",
        AsyncMock(
            return_value=[
                ("tombstone-person", "tombstone"),
                ("website-lead-person", "ttl"),
            ]
        ),
    )

    engine = MagicMock()
    engine.dispose = AsyncMock()
    monkeypatch.setattr(retention_sweep, "create_async_engine", MagicMock(return_value=engine))

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.rollback = AsyncMock()
    session_maker = MagicMock(return_value=session)
    monkeypatch.setattr(
        retention_sweep, "async_sessionmaker", MagicMock(return_value=session_maker)
    )
    monkeypatch.setattr(retention_sweep, "bind_session_context", AsyncMock())

    with capture_logs() as logs:
        summary = await retention_sweep._sweep_async(settings)

    event = next(item for item in logs if item.get("event") == "retention_would_purge")
    assert summary["candidates"] == 2
    assert summary["purged"] == 0
    assert event["tenant_id"] == "tenant-fictional"
    assert event["tombstone"] == 1
    assert event["ttl"] == 1
    assert "person_ids" not in event
    assert "count" not in event
    session.commit.assert_not_called()
