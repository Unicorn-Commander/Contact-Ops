"""Runtime wiring: both infra Celery tasks register + both beat entries exist.

Guards the two things make_celery_app() must do for the LIGHT operational tasks:
force-import each module so its ``@shared_task`` binds to the app, and add a
beat-schedule entry at the right cadence (inbox flipper every 5 min; retention
sweep daily at 04:00 UTC).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from celery.schedules import crontab

from contact_ops.agents.runtime import make_celery_app

INBOX_TASK = "contact_ops.agents.tasks.run_inbox_snooze_flipper"
RETENTION_TASK = "contact_ops.agents.tasks.run_retention_sweep"


def test_both_infra_tasks_registered() -> None:
    app = make_celery_app()
    # Accessing app.tasks finalizes the app, binding the force-imported
    # shared_tasks. Both must be present for the worker to run them.
    assert INBOX_TASK in app.tasks
    assert RETENTION_TASK in app.tasks


def test_both_beat_entries_present_with_expected_cadence() -> None:
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


def test_worker_import_validation_error_hides_settings_inputs(tmp_path: Path) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    unrelated_marker = "worker-postmark-marker-must-never-render"
    database_marker = "worker-database-marker-must-never-render"
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and import statement.
        [sys.executable, "-c", "import contact_ops.agents.runtime"],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(backend_root),
            "DATABASE_URL": (
                f"postgresql+asyncpg://runtime:{database_marker}@database.invalid/contact_ops"
            ),
            "ENV": "prod",
            "STANDALONE_MODE": "true",
            "POSTMARK_SERVER_TOKEN": unrelated_marker,
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    combined_output = completed.stdout + completed.stderr

    assert completed.returncode != 0
    assert "STANDALONE_MODE=true is not permitted when ENV=prod" in completed.stderr
    assert unrelated_marker not in combined_output
    assert database_marker not in combined_output
    assert "input_value=" not in combined_output
    assert "input_type=dict" not in combined_output
