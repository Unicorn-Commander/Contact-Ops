"""Auto-merge guardrail: a match lacking an exact unique identifier (email or
phone) can never reach the auto_merge_eligible band, however high the model
score, so name-only collisions are surfaced for human review rather than
silently merged once dedup is trusted to auto-apply (T2+).

This pins the safety property that stops two distinct people who share a name
("A. Rojas" vs "Mateo Cruz") from being auto-conflated.
"""

from __future__ import annotations

from contact_ops.agents.dedup.agent import (
    _AUTO_MERGE_FLOOR,
    _REVIEW_CONFIDENCE_CAP,
    _determine_band,
    _has_identifier_corroboration,
)


def test_identifier_corroboration_detection():
    assert _has_identifier_corroboration(["email_exact"]) is True
    assert _has_identifier_corroboration(["phone_exact"]) is True
    assert _has_identifier_corroboration(["dmetaphone_lastname_first_initial", "email_exact"]) is True
    assert _has_identifier_corroboration(["dmetaphone_lastname_first_initial"]) is False
    assert _has_identifier_corroboration(["email_domain_soundex_lastname"]) is False
    assert _has_identifier_corroboration([]) is False


def test_review_cap_routes_out_of_auto_merge_band():
    assert _REVIEW_CONFIDENCE_CAP < _AUTO_MERGE_FLOOR
    assert _determine_band(_REVIEW_CONFIDENCE_CAP) != "auto_merge_eligible"
    assert _determine_band(_REVIEW_CONFIDENCE_CAP) == "single_review"


def _decision(prob: float, blocking_keys: list[str]) -> tuple[float, str | None]:
    """Mirror the inline guardrail decision in DedupAgent._run."""
    band = _determine_band(prob)
    corroborated = _has_identifier_corroboration(blocking_keys)
    decision_confidence = prob
    decision_band = band
    if prob >= _AUTO_MERGE_FLOOR and not corroborated:
        decision_confidence = _REVIEW_CONFIDENCE_CAP
        decision_band = _determine_band(decision_confidence) or band
    return decision_confidence, decision_band


def test_name_only_perfect_score_is_capped_to_review():
    dc, db = _decision(1.0, ["dmetaphone_lastname_first_initial"])
    assert dc < _AUTO_MERGE_FLOOR
    assert db != "auto_merge_eligible"


def test_identifier_backed_high_score_stays_auto_merge():
    dc, db = _decision(1.0, ["email_exact"])
    assert dc == 1.0
    assert db == "auto_merge_eligible"

    dc2, db2 = _decision(0.96, ["phone_exact"])
    assert dc2 == 0.96
    assert db2 == "auto_merge_eligible"


def test_company_or_dob_corroboration_alone_is_not_enough():
    # name + matching company/dob (but no exact email/phone) still routes to
    # review -- those are not unique identifiers.
    dc, db = _decision(0.99, ["dmetaphone_lastname_first_initial"])
    assert dc == _REVIEW_CONFIDENCE_CAP
    assert db == "single_review"


def test_below_floor_unaffected_by_guardrail():
    dc, db = _decision(0.70, ["dmetaphone_lastname_first_initial"])
    assert dc == 0.70
    assert db == "single_review"
