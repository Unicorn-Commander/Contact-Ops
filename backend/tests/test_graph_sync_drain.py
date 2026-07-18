"""graph-sync-drain task: gating + no-admin-DSN no-op + beat wiring."""

from __future__ import annotations

import pytest

from contact_ops.agents import graph_sync_drain
from contact_ops.core.config import get_settings


def test_disabled_flag_no_ops() -> None:
    settings = get_settings()
    previous = settings.GRAPH_SYNC_DRAIN_ENABLED
    settings.GRAPH_SYNC_DRAIN_ENABLED = False
    try:
        result = graph_sync_drain.run_graph_sync()
    finally:
        settings.GRAPH_SYNC_DRAIN_ENABLED = previous
    assert result == {"drained": 0}


@pytest.mark.asyncio
async def test_no_admin_dsn_no_ops() -> None:
    settings = get_settings()
    previous = settings.MIGRATION_DATABASE_URL
    settings.MIGRATION_DATABASE_URL = None
    try:
        result = await graph_sync_drain._drain_async(settings)
    finally:
        settings.MIGRATION_DATABASE_URL = previous
    # No admin DSN → runtime role sees zero rows under RLS → drain no-ops.
    assert result == {"drained": 0}


def test_beat_schedule_registers_graph_sync_drain() -> None:
    from contact_ops.agents.runtime import make_celery_app

    app = make_celery_app()
    assert "graph-sync-drain" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["graph-sync-drain"]
    assert entry["task"] == "contact_ops.agents.tasks.run_graph_sync"
