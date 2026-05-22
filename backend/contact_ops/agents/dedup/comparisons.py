from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import jellyfish

from contact_ops.agents.dedup.normalizers import (
    apply_nickname_map,
    normalize_address,
    normalize_email,
    normalize_phone_e164,
)


@dataclass(frozen=True)
class FactRef:
    fact_id: UUID
    source_kind: str
    source_confidence: float = 1.0


@dataclass(frozen=True)
class ComparisonOutcome:
    level_name: str  # 'strong' | 'fuzzy' | 'nickname' | 'exact' | 'day_off' | 'partial' | 'disagree' | 'null_exclude'
    raw_bits: float
    contributing_facts: tuple[FactRef, ...] = ()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _normalize_name(s: str) -> str:
    return s.strip().lower()


# ---------------------------------------------------------------------------
# First Name
# ---------------------------------------------------------------------------

def compare_first_name(
    a: str | None, b: str | None,
    *,
    a_facts: tuple[FactRef, ...] = (),
    b_facts: tuple[FactRef, ...] = (),
) -> ComparisonOutcome:
    if a is None or b is None:
        return ComparisonOutcome("null_exclude", 0.0)

    a_norm = _normalize_name(a)
    b_norm = _normalize_name(b)

    # Nickname map
    nicknames_a = apply_nickname_map(a_norm)
    nicknames_b = apply_nickname_map(b_norm)
    if b_norm in nicknames_a or a_norm in nicknames_b:
        return ComparisonOutcome("nickname", 5.0, a_facts + b_facts)

    # Jaro-Winkler
    sim = jellyfish.jaro_winkler_similarity(a_norm, b_norm)
    if sim >= 0.95:
        return ComparisonOutcome("strong", 7.0, a_facts + b_facts)
    if sim >= 0.85:
        return ComparisonOutcome("fuzzy", 3.0, a_facts + b_facts)

    # Phonetic (Double Metaphone)
    prim_a, sec_a = jellyfish.double_metaphone(a_norm)
    prim_b, sec_b = jellyfish.double_metaphone(b_norm)
    if (prim_a and prim_b and prim_a == prim_b) or (sec_a and sec_b and sec_a == sec_b):
        return ComparisonOutcome("fuzzy", 3.0, a_facts + b_facts)

    return ComparisonOutcome("disagree", -4.0, a_facts + b_facts)


# ---------------------------------------------------------------------------
# Last Name
# ---------------------------------------------------------------------------

def compare_last_name(
    a: str | None, b: str | None,
    *,
    tf_a: float = 1.0, tf_b: float = 1.0,
    a_facts: tuple[FactRef, ...] = (),
    b_facts: tuple[FactRef, ...] = (),
) -> ComparisonOutcome:
    if a is None or b is None:
        return ComparisonOutcome("null_exclude", 0.0)

    a_norm = _normalize_name(a)
    b_norm = _normalize_name(b)

    # Jaro-Winkler strong match (TF-adjusted)
    sim = jellyfish.jaro_winkler_similarity(a_norm, b_norm)
    if sim >= 0.95:
        tf = min(tf_a, tf_b)
        bits = 9.0 * tf
        return ComparisonOutcome("strong", bits, a_facts + b_facts)

    # Damerau-Levenshtein + Double Metaphone fuzzy match
    dl_dist = jellyfish.damerau_levenshtein_distance(a_norm, b_norm)
    prim_a, sec_a = jellyfish.double_metaphone(a_norm)
    prim_b, sec_b = jellyfish.double_metaphone(b_norm)
    dm_match = (prim_a and prim_b and prim_a == prim_b) or (sec_a and sec_b and sec_a == sec_b)
    if dl_dist <= 2 and dm_match:
        return ComparisonOutcome("fuzzy", 3.0, a_facts + b_facts)

    return ComparisonOutcome("disagree", -4.0, a_facts + b_facts)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def compare_email(
    a: str | None, b: str | None,
    *,
    a_facts: tuple[FactRef, ...] = (),
    b_facts: tuple[FactRef, ...] = (),
) -> ComparisonOutcome:
    if a is None or b is None:
        return ComparisonOutcome("null_exclude", 0.0)

    a_norm = normalize_email(a)
    b_norm = normalize_email(b)
    if a_norm == b_norm:
        return ComparisonOutcome("exact", 20.0, a_facts + b_facts)
    return ComparisonOutcome("disagree", -2.0, a_facts + b_facts)


# ---------------------------------------------------------------------------
# Phone
# ---------------------------------------------------------------------------

def compare_phone(
    a: str | None, b: str | None,
    *,
    a_facts: tuple[FactRef, ...] = (),
    b_facts: tuple[FactRef, ...] = (),
) -> ComparisonOutcome:
    if a is None or b is None:
        return ComparisonOutcome("null_exclude", 0.0)

    a_norm = normalize_phone_e164(a)
    b_norm = normalize_phone_e164(b)
    if a_norm == b_norm:
        return ComparisonOutcome("exact", 18.0, a_facts + b_facts)
    return ComparisonOutcome("disagree", -1.0, a_facts + b_facts)


# ---------------------------------------------------------------------------
# Date of Birth
# ---------------------------------------------------------------------------

def compare_dob(
    a: dict | None, b: dict | None,
    *,
    a_facts: tuple[FactRef, ...] = (),
    b_facts: tuple[FactRef, ...] = (),
) -> ComparisonOutcome:
    if a is None or b is None:
        return ComparisonOutcome("null_exclude", 0.0)

    a_year = a.get("year")
    a_month = a.get("month")
    a_day = a.get("day")
    b_year = b.get("year")
    b_month = b.get("month")
    b_day = b.get("day")

    # Exact
    if (a_year is not None and a_month is not None and a_day is not None
            and a_year == b_year and a_month == b_month and a_day == b_day):
        return ComparisonOutcome("exact", 18.0, a_facts + b_facts)

    # Day-off: month+day match, year differs
    if (a_month is not None and a_day is not None
            and b_month is not None and b_day is not None
            and a_month == b_month and a_day == b_day
            and a_year is not None and b_year is not None
            and a_year != b_year):
        return ComparisonOutcome("day_off", 12.0, a_facts + b_facts)

    # Partial: month+day match (any year) OR year match (any month/day)
    month_day_match = (a_month is not None and a_day is not None
                       and b_month is not None and b_day is not None
                       and a_month == b_month and a_day == b_day)
    year_match = (a_year is not None and b_year is not None and a_year == b_year)
    if month_day_match or year_match:
        return ComparisonOutcome("partial", 6.0, a_facts + b_facts)

    return ComparisonOutcome("disagree", -10.0, a_facts + b_facts)


# ---------------------------------------------------------------------------
# Address
# ---------------------------------------------------------------------------

def compare_address(
    a: str | None, b: str | None,
    *,
    a_facts: tuple[FactRef, ...] = (),
    b_facts: tuple[FactRef, ...] = (),
) -> ComparisonOutcome:
    if a is None or b is None:
        return ComparisonOutcome("null_exclude", 0.0)

    a_norm = normalize_address(a)
    b_norm = normalize_address(b)

    sim = jellyfish.jaro_winkler_similarity(a_norm, b_norm)
    if sim >= 0.9:
        return ComparisonOutcome("strong", 14.0, a_facts + b_facts)
    if sim >= 0.7:
        return ComparisonOutcome("partial", 5.0, a_facts + b_facts)
    return ComparisonOutcome("disagree", -2.0, a_facts + b_facts)


# ---------------------------------------------------------------------------
# Name Embedding
# ---------------------------------------------------------------------------

def compare_name_embedding(
    a: list[float] | None, b: list[float] | None,
) -> ComparisonOutcome:
    if a is None or b is None:
        return ComparisonOutcome("null_exclude", 0.0)
    if not a or not b:
        return ComparisonOutcome("null_exclude", 0.0)

    sim = _cosine_similarity(a, b)
    if sim >= 0.95:
        return ComparisonOutcome("strong", 6.0)
    if sim >= 0.85:
        return ComparisonOutcome("fuzzy", 2.0)
    if sim < 0.7:
        return ComparisonOutcome("disagree", -2.0)
    return ComparisonOutcome("fuzzy", 0.0)


# ---------------------------------------------------------------------------
# Face Embedding
# ---------------------------------------------------------------------------

def compare_face_embedding(
    a: list[float] | None, b: list[float] | None,
) -> ComparisonOutcome:
    if a is None or b is None:
        return ComparisonOutcome("null_exclude", 0.0)
    if not a or not b:
        return ComparisonOutcome("null_exclude", 0.0)

    sim = _cosine_similarity(a, b)
    if sim >= 0.75:
        return ComparisonOutcome("strong", 8.0)
    if sim >= 0.6:
        return ComparisonOutcome("fuzzy", 2.0)
    return ComparisonOutcome("disagree", -4.0)


# ---------------------------------------------------------------------------
# Voice Fingerprint
# ---------------------------------------------------------------------------

def compare_voice_fingerprint(
    a: list[float] | None, b: list[float] | None,
) -> ComparisonOutcome:
    if a is None or b is None:
        return ComparisonOutcome("null_exclude", 0.0)
    if not a or not b:
        return ComparisonOutcome("null_exclude", 0.0)

    sim = _cosine_similarity(a, b)
    if sim >= 0.80:
        return ComparisonOutcome("strong", 6.0)
    if sim >= 0.65:
        return ComparisonOutcome("fuzzy", 2.0)
    return ComparisonOutcome("disagree", -2.0)


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------

def compare_company(
    a: str | None, b: str | None,
    *,
    a_facts: tuple[FactRef, ...] = (),
    b_facts: tuple[FactRef, ...] = (),
) -> ComparisonOutcome:
    if a is None or b is None:
        return ComparisonOutcome("null_exclude", 0.0)

    a_norm = _normalize_name(a)
    b_norm = _normalize_name(b)

    if a_norm == b_norm:
        return ComparisonOutcome("exact", 4.0, a_facts + b_facts)

    sim = jellyfish.jaro_winkler_similarity(a_norm, b_norm)
    if sim > 0.85:
        return ComparisonOutcome("fuzzy", 1.0, a_facts + b_facts)

    return ComparisonOutcome("disagree", -1.0, a_facts + b_facts)


# ---------------------------------------------------------------------------
# Government ID
# ---------------------------------------------------------------------------

def compare_government_id(
    a: str | None, b: str | None,
) -> ComparisonOutcome:
    if a is None or b is None:
        return ComparisonOutcome("null_exclude", 0.0)

    a_norm = a.strip().lower()
    b_norm = b.strip().lower()
    if a_norm == b_norm:
        return ComparisonOutcome("exact", 25.0)
    return ComparisonOutcome("disagree", -15.0)
