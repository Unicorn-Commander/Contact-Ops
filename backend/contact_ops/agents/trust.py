"""Bayesian Beta trust ladder (Phase 3 Design §5).

Five tiers (T0..T4) per (agent × tenant × visibility). Trust modeled as
Beta(alpha, beta) where alpha = approvals + 1 and beta = rejections + 1
(Laplace prior, so n=0 gives Beta(1,1) = uniform). Promotion gates evaluate
mean *and* lower bound of a credible interval so an agent does not get
promoted on a 5-sample hot streak.

Drift detection: PSI (Population Stability Index) and KS-test on rolling
7-day vs 30-day approval-rate distributions. Either triggers demotion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum

from scipy import stats


class TrustTier(IntEnum):
    """Trust tier 0..4 per Phase 3 Design §5.

    Naming aligns with the design doc:
      T0 Probation (cold start, never auto-apply)
      T1 Trainee (working but never auto-apply)
      T2 Trusted (auto-apply with high confidence + reversible)
      T3 Senior (auto-apply with moderate confidence + reversible)
      T4 Principal (auto-apply all reversible)
    """

    T0_PROBATION = 0
    T1_TRAINEE = 1
    T2_TRUSTED = 2
    T3_SENIOR = 3
    T4_PRINCIPAL = 4


@dataclass(frozen=True)
class BetaPosterior:
    """Posterior Beta(alpha, beta) parameters.

    ``alpha = approvals + 1`` and ``beta = rejections + 1``. This means
    ``total_outcomes`` is ``alpha + beta - 2``, not ``alpha + beta``.
    """

    alpha: float
    beta: float

    def __post_init__(self) -> None:
        if self.alpha < 1.0:
            raise ValueError(f"alpha must be >= 1.0 (Laplace prior); got {self.alpha}")
        if self.beta < 1.0:
            raise ValueError(f"beta must be >= 1.0 (Laplace prior); got {self.beta}")

    @property
    def mean(self) -> float:
        """E[p] = alpha / (alpha + beta)."""
        return self.alpha / (self.alpha + self.beta)

    @property
    def total_outcomes(self) -> int:
        """Approvals + rejections (excludes the Laplace prior)."""
        return int(round(self.alpha + self.beta - 2))

    @property
    def variance(self) -> float:
        """Var[p] for the Beta posterior."""
        a, b = self.alpha, self.beta
        return (a * b) / ((a + b) ** 2 * (a + b + 1))

    def lower_credible_interval(self, level: float = 0.90) -> float:
        """Lower bound of the central credible interval at ``level``."""
        if not 0.0 < level < 1.0:
            raise ValueError(f"level must be in (0, 1); got {level}")
        tail = (1.0 - level) / 2.0
        return float(stats.beta.ppf(tail, self.alpha, self.beta))

    def upper_credible_interval(self, level: float = 0.90) -> float:
        """Upper bound of the central credible interval at ``level``."""
        if not 0.0 < level < 1.0:
            raise ValueError(f"level must be in (0, 1); got {level}")
        tail = (1.0 - level) / 2.0
        return float(stats.beta.ppf(1.0 - tail, self.alpha, self.beta))

    def with_approval(self) -> BetaPosterior:
        return BetaPosterior(alpha=self.alpha + 1.0, beta=self.beta)

    def with_rejection(self) -> BetaPosterior:
        return BetaPosterior(alpha=self.alpha, beta=self.beta + 1.0)


# Promotion gates per Phase 3 Design §5. ``samples`` here is
# ``total_outcomes`` (approvals + rejections, excluding Laplace prior).
#
# T0 -> T1: samples >= 25 AND mean >= 0.70 AND lower_ci_90 >= 0.60
# T1 -> T2: samples >= 100 AND mean >= 0.85 AND lower_ci_95 >= 0.78
# T2 -> T3: samples >= 500 AND mean >= 0.95 AND lower_ci_95 >= 0.90
# T3 -> T4: terminal; design says T4 is mean >= 0.95 with samples >= 500
#           and ``stable`` drift state. We require the same gate as T3 with
#           an additional 30-day stability window. The Calibration Daemon
#           applies the drift check; ``tier_from_posterior`` reports the
#           posterior tier alone.

T0_MIN_SAMPLES = 25
T0_MEAN_FLOOR = 0.70
T0_LOWER_CI_90 = 0.60

T1_MIN_SAMPLES = 100
T1_MEAN_FLOOR = 0.85
T1_LOWER_CI_95 = 0.78

T2_MIN_SAMPLES = 500
T2_MEAN_FLOOR = 0.95
T2_LOWER_CI_95 = 0.90


def tier_from_posterior(p: BetaPosterior) -> TrustTier:
    """Map a Beta posterior to a trust tier per Phase 3 Design §5.

    Promotion gates require both the mean AND the lower bound of the
    appropriate credible interval. Demotion is handled separately by the
    Calibration Daemon when PSI/KS drift triggers fire; this function
    reports the *posterior-implied* tier ignoring drift.

    The function is deterministic and pure: same input always returns the
    same tier. The Calibration Daemon stores its tier on ``agent_trust``
    so demotions sticky-pin until the next daily recompute.
    """
    n = p.total_outcomes
    mean = p.mean

    if n < T0_MIN_SAMPLES:
        return TrustTier.T0_PROBATION

    if n >= T2_MIN_SAMPLES and mean >= T2_MEAN_FLOOR:
        if p.lower_credible_interval(0.95) >= T2_LOWER_CI_95:
            return TrustTier.T4_PRINCIPAL
        return TrustTier.T3_SENIOR

    if n >= T1_MIN_SAMPLES and mean >= T1_MEAN_FLOOR:
        if p.lower_credible_interval(0.95) >= T1_LOWER_CI_95:
            return TrustTier.T2_TRUSTED
        return TrustTier.T1_TRAINEE

    if mean >= T0_MEAN_FLOOR and p.lower_credible_interval(0.90) >= T0_LOWER_CI_90:
        return TrustTier.T1_TRAINEE

    return TrustTier.T0_PROBATION


# ---- Drift detection ----

# PSI thresholds per design doc §5:
#   PSI < 0.1   stable
#   0.1 <= PSI < 0.2   warning (3rd consecutive day -> demote)
#   PSI >= 0.2  demote immediately
PSI_WARNING_THRESHOLD = 0.10
PSI_DEMOTE_THRESHOLD = 0.20


def population_stability_index(
    expected: list[float],
    actual: list[float],
    *,
    bins: int = 10,
) -> float:
    """Population Stability Index between two distributions.

    PSI = Σi (actual_i - expected_i) * ln(actual_i / expected_i)

    Computed over ``bins`` equal-width buckets in [0, 1]. Empty buckets are
    smoothed with 1e-4 to avoid log(0). Designed for proportion data
    (approval rates), not arbitrary values.
    """
    if not expected or not actual:
        raise ValueError("expected and actual must be non-empty")
    if any(not 0.0 <= v <= 1.0 for v in expected + actual):
        raise ValueError("PSI inputs must be in [0, 1]")

    def _hist(values: list[float]) -> list[float]:
        counts = [0] * bins
        for v in values:
            idx = min(int(v * bins), bins - 1)
            counts[idx] += 1
        total = len(values)
        return [max(c / total, 1e-4) for c in counts]

    e_hist = _hist(expected)
    a_hist = _hist(actual)

    psi = 0.0
    for e, a in zip(e_hist, a_hist, strict=True):
        psi += (a - e) * math.log(a / e)
    return psi


def classify_drift(psi: float) -> str:
    """Map PSI to drift status: 'stable' | 'warning' | 'drift'."""
    if psi >= PSI_DEMOTE_THRESHOLD:
        return "drift"
    if psi >= PSI_WARNING_THRESHOLD:
        return "warning"
    return "stable"


def ks_test_p_value(
    sample_a: list[float],
    sample_b: list[float],
) -> float:
    """Two-sample Kolmogorov-Smirnov p-value.

    Used as a secondary drift signal: p < 0.05 means the rolling 7-day
    approval-rate distribution is statistically distinguishable from the
    30-day baseline.
    """
    if not sample_a or not sample_b:
        raise ValueError("both samples must be non-empty")
    result = stats.ks_2samp(sample_a, sample_b)
    return float(result.pvalue)


def should_demote(
    *,
    current_tier: TrustTier,
    rolling_7d_mean: float,
    rolling_30d_mean: float,
    psi: float,
    consecutive_warning_days: int,
) -> bool:
    """Apply the three demotion triggers from Phase 3 Design §5.

    1. PSI >= 0.2 over the 7d-vs-30d window.
    2. Rolling 7d mean drops > 15pp below the 30d baseline.
    3. Three consecutive daily PSI >= 0.1 readings.

    T0 cannot be demoted further; returns False unconditionally.
    """
    if current_tier == TrustTier.T0_PROBATION:
        return False
    if psi >= PSI_DEMOTE_THRESHOLD:
        return True
    if rolling_30d_mean - rolling_7d_mean > 0.15:
        return True
    if consecutive_warning_days >= 3:
        return True
    return False


def tier_name(tier: TrustTier) -> str:
    """Human-readable tier label, e.g. ``T2 Trusted``."""
    labels = {
        TrustTier.T0_PROBATION: "T0 Probation",
        TrustTier.T1_TRAINEE: "T1 Trainee",
        TrustTier.T2_TRUSTED: "T2 Trusted",
        TrustTier.T3_SENIOR: "T3 Senior",
        TrustTier.T4_PRINCIPAL: "T4 Principal",
    }
    return labels[tier]
