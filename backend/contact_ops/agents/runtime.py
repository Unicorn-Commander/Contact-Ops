"""Celery + LISTEN/NOTIFY runtime for the agent fleet.

Single Celery app, single Redis broker (shared with the rest of the
ecosystem; uses db 9, not db 5). Workers partition into queues:

* ``agents.batch.high`` — Dedup, Tag
* ``agents.batch.low`` — CardDAV, Graph Sync, Communication Signal, Provenance
* ``agents.calibration`` — Calibration Daemon

Periodic schedules are built dynamically from the in-process registry at
process start. Adding a new agent is therefore just ``register_agent(...)``
in its module + Celery picks it up on next worker restart.

LISTEN/NOTIFY consumers run as separate uvicorn workers (one per
event-driven agent) outside Celery; this module exposes the helpers they
need to wire up subscriptions.
"""

from __future__ import annotations

import os
from typing import Any

import structlog
from celery import Celery
from celery.schedules import crontab

from contact_ops.agents.registry import AgentClass, list_agents

logger = structlog.get_logger(__name__)


CELERY_APP_NAME = "contact_ops.agents"


def _broker_url() -> str:
    """Phase 3 lives on Redis db 9 (db 5 is the existing app's allocation).

    Override with ``CONTACT_OPS_AGENTS_BROKER_URL`` for ops convenience.
    """
    explicit = os.environ.get("CONTACT_OPS_AGENTS_BROKER_URL")
    if explicit:
        return explicit
    base = os.environ.get("REDIS_URL", "redis://unicorn-redis:6379/5")
    # Force db 9 even if REDIS_URL points elsewhere.
    if base.endswith("/5"):
        return base[:-2] + "/9"
    if "/" in base.split("://", 1)[1]:
        head, _, _ = base.rpartition("/")
        return f"{head}/9"
    return base.rstrip("/") + "/9"


def _backend_url() -> str:
    return os.environ.get(
        "CONTACT_OPS_AGENTS_RESULT_BACKEND_URL", _broker_url()
    )


def make_celery_app() -> Celery:
    """Construct (or return the cached) Celery app for the agent fleet."""
    app = Celery(
        CELERY_APP_NAME,
        broker=_broker_url(),
        backend=_backend_url(),
    )

    app.conf.update(
        task_default_queue="agents.batch.low",
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_track_started=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        result_expires=86400,
        task_routes={
            "contact_ops.agents.tasks.run_dedup": {"queue": "agents.batch.high"},
            "contact_ops.agents.tasks.run_tag": {"queue": "agents.batch.high"},
            "contact_ops.agents.tasks.run_calibration": {"queue": "agents.calibration"},
        },
        timezone="UTC",
        enable_utc=True,
    )

    # Always-on beat schedule for the Calibration Daemon's daily walk;
    # other agents register their own schedule below.
    app.conf.beat_schedule = _build_beat_schedule()

    # Infrastructure beats that aren't agents — Phase 3.3a inbox snooze
    # re-flipper. Runs every 5 minutes per Aaron's B5.
    app.conf.beat_schedule.setdefault(
        "inbox-snooze-flipper",
        {
            "task": "contact_ops.agents.tasks.run_inbox_snooze_flipper",
            "schedule": crontab(minute="*/5"),
            "kwargs": {"agent_slug": "inbox-snooze-flipper"},
        },
    )

    # Retention sweep -- daily PII age-out + purge (DORMANT behind
    # RETENTION_SWEEP_ENABLED). Daily at 04:00 UTC (low-traffic) per the
    # task's daily Celery beat task intent.
    app.conf.beat_schedule.setdefault(
        "retention-sweep",
        {
            "task": "contact_ops.agents.tasks.run_retention_sweep",
            "schedule": crontab(minute="0", hour="4"),
            "kwargs": {"agent_slug": "retention-sweep"},
        },
    )

    # Force-import infra tasks so the worker registers them at startup.
    # The import has the side-effect of binding the @shared_task to
    # this Celery app via Celery's task registry.
    from contact_ops.agents import (
        inbox_snooze_flipper,  # noqa: F401
        retention_sweep,  # noqa: F401
    )

    return app


def _build_beat_schedule() -> dict[str, dict[str, Any]]:
    """Build a Celery beat schedule from every BATCH-class registered agent.

    Trigger entries on a BATCH agent are either ``"manual"`` or a cron
    expression (5 or 6 space-separated fields). We translate cron entries
    into ``celery.schedules.crontab`` instances keyed by slug.
    """
    schedule: dict[str, dict[str, Any]] = {}
    for agent_def in list_agents():
        if agent_def.agent_class != AgentClass.BATCH:
            continue
        for idx, trigger in enumerate(agent_def.triggers):
            if trigger == "manual":
                continue
            try:
                ct = _crontab_from_string(trigger)
            except ValueError as exc:
                logger.warning(
                    "agent_cron_skipped",
                    agent=agent_def.slug,
                    trigger=trigger,
                    reason=str(exc),
                )
                continue
            entry_name = (
                f"{agent_def.slug}-{idx}"
                if len(agent_def.triggers) > 1
                else agent_def.slug
            )
            schedule[entry_name] = {
                "task": f"contact_ops.agents.tasks.run_{agent_def.slug.replace('-', '_')}",
                "schedule": ct,
                "kwargs": {"agent_slug": agent_def.slug},
            }
    return schedule


def _crontab_from_string(expr: str) -> crontab:
    """Translate ``"minute hour dom month dow"`` into ``crontab``.

    Supports the 5-field POSIX subset only. Anything else raises ValueError.
    """
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"need 5 cron fields, got {len(parts)}: {expr!r}")
    minute, hour, dom, month, dow = parts
    return crontab(
        minute=minute,
        hour=hour,
        day_of_month=dom,
        month_of_year=month,
        day_of_week=dow,
    )


# A module-level singleton; Celery's CLI discovers it via
# ``celery -A contact_ops.agents.runtime worker --loglevel=info``.
celery_app = make_celery_app()


async def ensure_agents_registered(db) -> None:  # type: ignore[no-untyped-def]
    """Idempotently upsert in-process registry into Postgres ``agent_registry``.

    Called by the FastAPI startup hook so a fresh deploy automatically reflects
    new agents. Existing rows update their version + budget + tier defaults;
    deletes are explicit (via the admin CLI).
    """
    from sqlalchemy import text

    for agent_def in list_agents():
        await db.execute(
            text(
                """
                INSERT INTO agent_registry (
                    name, version, owning_system, scope_mode, visibility,
                    declared_capabilities, current_trust_tier,
                    calibration_history,
                    slug, agent_class, description,
                    cost_budget_monthly_cents, initial_trust_tier, triggers
                ) VALUES (
                    :name, :version, 'contact-ops', 'shared', 'private',
                    :caps, 'propose_only',
                    '[]'::jsonb,
                    :slug, :agent_class, :description,
                    :budget, :initial_tier, CAST(:triggers AS jsonb)
                )
                ON CONFLICT (slug) DO UPDATE
                SET version = EXCLUDED.version,
                    agent_class = EXCLUDED.agent_class,
                    description = EXCLUDED.description,
                    cost_budget_monthly_cents = EXCLUDED.cost_budget_monthly_cents,
                    initial_trust_tier = EXCLUDED.initial_trust_tier,
                    triggers = EXCLUDED.triggers,
                    declared_capabilities = EXCLUDED.declared_capabilities,
                    updated_at = now()
                """
            ),
            {
                "name": agent_def.name,
                "version": agent_def.version,
                "caps": list(agent_def.declared_capabilities),
                "slug": agent_def.slug,
                "agent_class": agent_def.agent_class.value,
                "description": agent_def.description,
                "budget": agent_def.cost_budget_monthly_cents,
                "initial_tier": int(agent_def.initial_trust_tier),
                "triggers": __import__("json").dumps(list(agent_def.triggers)),
            },
        )
    await db.commit()


__all__ = [
    "celery_app",
    "ensure_agents_registered",
    "make_celery_app",
]
