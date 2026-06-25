"""Runtime wiring: both infra Celery tasks register + both beat entries exist.

Guards the two things make_celery_app() must do for the LIGHT operational tasks:
force-import each module so its ``@shared_task`` binds to the app, and add a
beat-schedule entry at the right cadence (inbox flipper every 5 min; retention
sweep daily at 04:00 UTC).
"""

from __future__ import annotations

from celery.schedules import crontab

from contact_ops.agents.runtime import make_celery_app

INBOX_TASK = "contact_ops.agents.tasks.run_inbox_snooze_flipper"
RETENTION_TASK = "contact_ops.agents.tasks.run_retention_sweep"


def test_both_infra_tasks_registered():
    app = make_celery_app()
    # Accessing app.tasks finalizes the app, binding the force-imported
    # shared_tasks. Both must be present for the worker to run them.
    assert INBOX_TASK in app.tasks
    assert RETENTION_TASK in app.tasks


def test_both_beat_entries_present_with_expected_cadence():
    app = make_celery_app()
    sched = app.conf.beat_schedule

    assert "inbox-snooze-flipper" in sched
    assert "retention-sweep" in sched

    inbox = sched["inbox-snooze-flipper"]
    retention = sched["retention-sweep"]

    assert inbox["task"] == INBOX_TASK
    assert retention["task"] == RETENTION_TASK

    # inbox snooze flipper: every 5 minutes.
    assert isinstance(inbox["schedule"], crontab)
    assert inbox["schedule"].minute == set(range(0, 60, 5))

    # retention sweep: daily at 04:00 UTC (low-traffic hour, daily per the
    # task's "daily Celery beat task" intent).
    assert isinstance(retention["schedule"], crontab)
    assert retention["schedule"].minute == {0}
    assert retention["schedule"].hour == {4}
