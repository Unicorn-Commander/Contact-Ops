"""inbox-snooze-flipper — Celery beat task that re-surfaces snoozed proposals.

Runs every 5 minutes per Aaron's B5. UPDATEs ``action_event`` rows where
``snoozed_until <= now()`` to clear the snooze, so the inbox query starts
showing them again. Idempotent + cheap (indexed on ``ae_snoozed_until_idx``).

Registered by ``contact_ops.agents.runtime`` as part of the beat schedule.
The task name is the standard ``contact_ops.agents.tasks.run_*`` prefix the
runtime uses, with hyphens swapped to underscores per the existing routing.
"""

from __future__ import annotations

import os
from typing import Any

import structlog
from celery import shared_task
from sqlalchemy import create_engine, text

logger = structlog.get_logger(__name__)


def _sync_db_url() -> str:
    """Synchronous DSN for the Celery worker (asyncpg not in worker context)."""
    return os.environ.get(
        "CONTACT_OPS_SYNC_DATABASE_URL",
        os.environ.get(
            "DATABASE_URL", "${CONTACT_OPS_DATABASE_URL}"
        ),
    )


@shared_task(  # type: ignore[misc]
    name="contact_ops.agents.tasks.run_inbox_snooze_flipper",
    bind=True,
    acks_late=True,
)
def flip_expired_snoozes(self: Any, agent_slug: str = "inbox-snooze-flipper") -> dict[str, int]:
    """Clear ``snoozed_until`` for every action_event whose snooze expired.

    Returns ``{"flipped": <count>}`` for observability. The Calibration
    Daemon doesn't read this — it's purely operational telemetry.
    """
    engine = create_engine(_sync_db_url(), future=True)
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    UPDATE action_event
                    SET snoozed_until = NULL
                    WHERE status = 'proposed'
                      AND snoozed_until IS NOT NULL
                      AND snoozed_until <= now()
                    """
                )
            )
            count = result.rowcount or 0
    finally:
        engine.dispose()

    if count > 0:
        logger.info("inbox_snooze_flipper.flipped", count=count)
    return {"flipped": count}


__all__ = ["flip_expired_snoozes"]
