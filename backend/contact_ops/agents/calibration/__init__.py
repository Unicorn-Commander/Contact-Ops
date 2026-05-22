"""Phase 3.4 Calibration Daemon — keeps agent_trust up to date.

Daily Celery beat task that walks ``action_event`` since the last
calibration run, updates Beta(α, β) posteriors per
(agent × tenant × visibility), detects drift via PSI + KS-test,
and emits tier-change proposals (promotion) or auto-applied demotions.

Modules:

* ``posteriors`` — walk + UPSERT on agent_trust
* ``drift`` — PSI + KS + warning-streak accounting
* ``tier_changes`` — promote/demote decisions + ``calibration.tier_*``
  action_event emission
* ``daemon`` — orchestrator (``run_calibration_pass``)
"""

from contact_ops.agents.calibration.daemon import (
    CALIBRATION_DAEMON_DEF,
    CalibrationDaemon,
    CalibrationPassResult,
    run_calibration_pass,
)
from contact_ops.agents.calibration.drift import (
    DriftResult,
    evaluate_drift,
    persist_warning_streak,
    write_drift_state,
)
from contact_ops.agents.calibration.posteriors import (
    TrustUpdate,
    update_posteriors_since,
)
from contact_ops.agents.calibration.tier_changes import (
    TierChange,
    apply_promotion,
    evaluate_and_apply_tier_changes,
)

__all__ = [
    "CALIBRATION_DAEMON_DEF",
    "CalibrationDaemon",
    "CalibrationPassResult",
    "DriftResult",
    "TierChange",
    "TrustUpdate",
    "apply_promotion",
    "evaluate_and_apply_tier_changes",
    "evaluate_drift",
    "persist_warning_streak",
    "run_calibration_pass",
    "update_posteriors_since",
    "write_drift_state",
]
