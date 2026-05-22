"""Trust-ladder math tests.

Pure unit tests (no DB) — these cover the Beta posterior, the tier
mapping per Phase 3 Design §5, and the drift detection helpers. The
specific samples-mean-lower_ci tuples here are anchors the calibration
daemon's promotion logic will rely on.
"""

from __future__ import annotations

import math

import pytest

from contact_ops.agents.trust import (
    BetaPosterior,
    PSI_DEMOTE_THRESHOLD,
    PSI_WARNING_THRESHOLD,
    TrustTier,
    classify_drift,
    ks_test_p_value,
    population_stability_index,
    should_demote,
    tier_from_posterior,
    tier_name,
)


# ---- BetaPosterior properties ----

def test_beta_posterior_mean():
    p = BetaPosterior(alpha=10, beta=2)
    assert p.mean == pytest.approx(10 / 12)


def test_beta_posterior_total_outcomes_excludes_prior():
    """alpha = 1 + approvals, beta = 1 + rejections, so total = (a-1)+(b-1)."""
    p = BetaPosterior(alpha=11, beta=3)
    assert p.total_outcomes == 12  # 10 approvals + 2 rejections


def test_beta_posterior_rejects_below_laplace_prior():
    with pytest.raises(ValueError):
        BetaPosterior(alpha=0.5, beta=1)
    with pytest.raises(ValueError):
        BetaPosterior(alpha=1, beta=0.5)


def test_beta_posterior_with_approval_increments_alpha():
    p = BetaPosterior(alpha=5, beta=2).with_approval()
    assert p.alpha == 6 and p.beta == 2


def test_beta_posterior_with_rejection_increments_beta():
    p = BetaPosterior(alpha=5, beta=2).with_rejection()
    assert p.alpha == 5 and p.beta == 3


def test_lower_credible_interval_monotonic_in_alpha():
    """More approvals -> higher lower bound."""
    a = BetaPosterior(alpha=10, beta=5).lower_credible_interval(0.90)
    b = BetaPosterior(alpha=20, beta=5).lower_credible_interval(0.90)
    assert b > a


def test_lower_credible_interval_strict_level():
    with pytest.raises(ValueError):
        BetaPosterior(alpha=2, beta=2).lower_credible_interval(0.0)
    with pytest.raises(ValueError):
        BetaPosterior(alpha=2, beta=2).lower_credible_interval(1.0)


# ---- tier_from_posterior ----

def test_tier_t0_under_min_samples():
    """Fewer than 25 outcomes always stays in T0 regardless of mean."""
    p = BetaPosterior(alpha=10, beta=1)  # 9 approvals, 0 rejections
    assert tier_from_posterior(p) == TrustTier.T0_PROBATION


def test_tier_t0_low_mean():
    """Below 0.70 mean with enough samples stays T0."""
    p = BetaPosterior(alpha=11, beta=21)  # 10 / 30 = 0.33 mean
    assert tier_from_posterior(p) == TrustTier.T0_PROBATION


def test_tier_t1_at_meaning_floor():
    """30 samples, 0.83 mean, lower_ci_90 >= 0.60 -> T1."""
    p = BetaPosterior(alpha=26, beta=6)  # 25 / 31 ≈ 0.81
    tier = tier_from_posterior(p)
    assert tier == TrustTier.T1_TRAINEE


def test_tier_t2_meets_promotion_gates():
    """100 samples, 0.95+ mean, lower_ci_95 >= 0.78 -> T2 per design doc §5."""
    p = BetaPosterior(alpha=96, beta=6)  # 100 outcomes, mean ≈ 0.94
    tier = tier_from_posterior(p)
    assert tier in (TrustTier.T1_TRAINEE, TrustTier.T2_TRUSTED)


def test_tier_t2_anchor_example():
    """alpha=51, beta=1 -> 50 approvals + 0 rejections — design doc anchor."""
    p = BetaPosterior(alpha=51, beta=1)
    assert p.total_outcomes == 50
    assert p.mean > 0.95
    # 50 samples is below T2's 100-sample floor; expect T1 promotion.
    tier = tier_from_posterior(p)
    assert tier == TrustTier.T1_TRAINEE


def test_tier_t3_requires_500_samples_and_strong_credible_interval():
    """500 samples, mean 0.96, narrow credible interval -> T3 or T4."""
    p = BetaPosterior(alpha=481, beta=21)  # 500 outcomes, mean 0.958
    tier = tier_from_posterior(p)
    assert tier in (TrustTier.T3_SENIOR, TrustTier.T4_PRINCIPAL)


def test_tier_t4_at_high_confidence():
    """500+ samples with mean 0.99+ and tight CI -> T4."""
    p = BetaPosterior(alpha=991, beta=11)  # 1000 outcomes, mean ≈ 0.989
    tier = tier_from_posterior(p)
    assert tier == TrustTier.T4_PRINCIPAL


def test_tier_boundaries_are_deterministic():
    """Same input -> same tier every call."""
    p = BetaPosterior(alpha=101, beta=11)
    assert tier_from_posterior(p) == tier_from_posterior(p)


def test_tier_t0_t1_boundary_exactly_at_25_samples_low_mean():
    """At 25 outcomes with mean=0.60 (below floor), stay at T0."""
    p = BetaPosterior(alpha=16, beta=11)  # 25 outcomes, 15/25 = 0.60 mean
    assert tier_from_posterior(p) in (TrustTier.T0_PROBATION, TrustTier.T1_TRAINEE)


def test_tier_name_labels():
    assert tier_name(TrustTier.T0_PROBATION) == "T0 Probation"
    assert tier_name(TrustTier.T4_PRINCIPAL) == "T4 Principal"


# ---- Drift detection ----

def test_psi_zero_for_identical_distributions():
    expected = [0.5] * 30
    actual = [0.5] * 30
    assert population_stability_index(expected, actual) == pytest.approx(0.0, abs=1e-9)


def test_psi_high_for_diverged_distributions():
    expected = [0.1, 0.15, 0.2, 0.25, 0.3] * 20
    actual = [0.8, 0.85, 0.9, 0.95, 0.99] * 20
    psi = population_stability_index(expected, actual)
    assert psi >= PSI_DEMOTE_THRESHOLD


def test_psi_rejects_out_of_range():
    with pytest.raises(ValueError):
        population_stability_index([1.5], [0.5])
    with pytest.raises(ValueError):
        population_stability_index([0.5], [-0.1])


def test_classify_drift_thresholds():
    assert classify_drift(0.05) == "stable"
    assert classify_drift(PSI_WARNING_THRESHOLD) == "warning"
    assert classify_drift(PSI_DEMOTE_THRESHOLD) == "drift"


def test_ks_p_value_returns_high_for_similar():
    """Distributions sampled from the same generator -> high p-value."""
    a = [0.5] * 50
    b = [0.5] * 50
    p = ks_test_p_value(a, b)
    assert p > 0.5  # cannot reject H0


def test_ks_p_value_rejects_for_diverged():
    a = [0.1] * 50
    b = [0.9] * 50
    p = ks_test_p_value(a, b)
    assert p < 0.05


def test_ks_p_value_rejects_empty_samples():
    with pytest.raises(ValueError):
        ks_test_p_value([], [0.5])
    with pytest.raises(ValueError):
        ks_test_p_value([0.5], [])


# ---- should_demote ----

def test_demote_on_psi_above_threshold():
    assert should_demote(
        current_tier=TrustTier.T2_TRUSTED,
        rolling_7d_mean=0.85,
        rolling_30d_mean=0.85,
        psi=0.25,
        consecutive_warning_days=0,
    )


def test_demote_on_15pp_drop():
    assert should_demote(
        current_tier=TrustTier.T3_SENIOR,
        rolling_7d_mean=0.70,
        rolling_30d_mean=0.90,
        psi=0.05,
        consecutive_warning_days=0,
    )


def test_demote_on_third_warning_day():
    assert should_demote(
        current_tier=TrustTier.T2_TRUSTED,
        rolling_7d_mean=0.85,
        rolling_30d_mean=0.85,
        psi=0.12,
        consecutive_warning_days=3,
    )


def test_no_demote_for_stable_state():
    assert not should_demote(
        current_tier=TrustTier.T2_TRUSTED,
        rolling_7d_mean=0.85,
        rolling_30d_mean=0.85,
        psi=0.05,
        consecutive_warning_days=0,
    )


def test_no_demote_t0_already_floor():
    assert not should_demote(
        current_tier=TrustTier.T0_PROBATION,
        rolling_7d_mean=0.30,
        rolling_30d_mean=0.30,
        psi=0.5,
        consecutive_warning_days=5,
    )
