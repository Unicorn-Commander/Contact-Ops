from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pandas as pd
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.agents.dedup.comparisons import (
    FactRef,
    compare_address,
    compare_company,
    compare_dob,
    compare_email,
    compare_face_embedding,
    compare_first_name,
    compare_government_id,
    compare_last_name,
    compare_name_embedding,
    compare_phone,
    compare_voice_fingerprint,
)

try:
    from splink.duckdb.linker import DuckDBLinker

    SPLINK_AVAILABLE = True
except ImportError:
    SPLINK_AVAILABLE = False

logger = structlog.get_logger(__name__)


@dataclass
class ScoredPair:
    person_a_id: UUID
    person_b_id: UUID
    match_probability: float
    match_weight_bits: float
    per_field_weights: dict[str, float]
    blocking_key: str
    source_kind: str


@dataclass
class SplinkResult:
    scored_pairs: list[ScoredPair]
    model_version: str
    em_iterations: int
    em_converged: bool
    trained_on_pairs: int
    settings_json: dict[str, Any]


_FIELD_COMPARISONS: dict[str, Any] = {
    "first_name": compare_first_name,
    "last_name": compare_last_name,
    "email": compare_email,
    "phone": compare_phone,
    "dob": compare_dob,
    "address": compare_address,
    "name_embedding": compare_name_embedding,
    "face_embedding": compare_face_embedding,
    "voice_fingerprint": compare_voice_fingerprint,
    "company": compare_company,
    "government_id": compare_government_id,
}

_EM_BLOCKING_RULES: list[str] = [
    "l.first_name = r.first_name",
    "l.last_name = r.last_name",
    "l.email = r.email",
    "l.phone = r.phone",
]


# ---------------------------------------------------------------------------
# Settings builder – Splink dict format per §9.3 + §9.5
# ---------------------------------------------------------------------------


def build_splink_settings() -> dict[str, Any]:
    """Build the Splink settings dict for the Dedup Agent.

    Uses splink.comparison_library (cl) and comparison_level_library (cll)
    conventions expressed as plain dicts for serialisation stability.

    Settings skeleton per §9.3 + §9.5:
    - link_type: ``dedupe_only``
    - unique_id_column_name: ``person_id``
    - comparisons list with each field's comparison levels
    - blocking_rules_to_generate_predictions as backup
    - retain_intermediate_calculation_columns: True
    - retain_matching_columns: True
    - term_frequency_adjustments on last_name and full_name comparisons
    """
    return {
        "link_type": "dedupe_only",
        "unique_id_column_name": "person_id",
        "comparisons": [
            _fn_comparison(),
            _ln_comparison(),
            _email_comparison(),
            _phone_comparison(),
            _dob_comparison(),
            _address_comparison(),
            _name_embedding_comparison(),
            _face_embedding_comparison(),
            _voice_fingerprint_comparison(),
            _company_comparison(),
            _government_id_comparison(),
        ],
        "blocking_rules_to_generate_predictions": [
            "l.first_name = r.first_name",
            "l.last_name = r.last_name",
            "l.email = r.email",
            "l.phone = r.phone",
            "l.address = r.address",
        ],
        "retain_intermediate_calculation_columns": True,
        "retain_matching_columns": True,
        "term_frequency_adjustments": [
            {"column_name": "last_name", "adjustment_weight": 1.0},
            {"column_name": "full_name", "adjustment_weight": 0.5},
        ],
    }


def _level_null(column: str) -> dict[str, Any]:
    return {
        "sql_condition": f"{column}_l IS NULL OR {column}_r IS NULL",
        "label_for_charts": "Null",
        "is_null_level": True,
    }


def _level_exact(column: str) -> dict[str, Any]:
    return {
        "sql_condition": f"{column}_l = {column}_r",
        "label_for_charts": "Exact",
    }


def _level_else() -> dict[str, Any]:
    return {"sql_condition": "ELSE", "label_for_charts": "Disagree"}


def _level_jw(column: str, threshold: float, label: str) -> dict[str, Any]:
    return {
        "sql_condition": f"jaro_winkler_similarity({column}_l, {column}_r) >= {threshold}",
        "label_for_charts": label,
    }


def _level_dl(column: str, distance: int, label: str) -> dict[str, Any]:
    return {
        "sql_condition": f"damerau_levenshtein({column}_l, {column}_r) <= {distance}",
        "label_for_charts": label,
    }


def _level_cosine(column: str, threshold: float, label: str) -> dict[str, Any]:
    return {
        "sql_condition": f"list_cosine_similarity({column}_l, {column}_r) >= {threshold}",
        "label_for_charts": label,
    }


def _fn_comparison() -> dict[str, Any]:
    return {
        "output_column_name": "first_name",
        "comparison_levels": [
            _level_null("first_name"),
            _level_exact("first_name"),
            _level_jw("first_name", 0.95, "Strong"),
            _level_jw("first_name", 0.85, "Fuzzy"),
            _level_else(),
        ],
    }


def _ln_comparison() -> dict[str, Any]:
    return {
        "output_column_name": "last_name",
        "comparison_levels": [
            _level_null("last_name"),
            {
                "sql_condition": "last_name_l = last_name_r",
                "label_for_charts": "Strong",
                "tf_adjustment_column": "last_name",
                "tf_adjustment_weight": 1.0,
            },
            _level_dl("last_name", 2, "Fuzzy"),
            _level_else(),
        ],
    }


def _email_comparison() -> dict[str, Any]:
    return {
        "output_column_name": "email",
        "comparison_levels": [
            _level_null("email"),
            _level_exact("email"),
            _level_else(),
        ],
    }


def _phone_comparison() -> dict[str, Any]:
    return {
        "output_column_name": "phone",
        "comparison_levels": [
            _level_null("phone"),
            _level_exact("phone"),
            _level_else(),
        ],
    }


def _dob_comparison() -> dict[str, Any]:
    return {
        "output_column_name": "dob",
        "comparison_levels": [
            _level_null("dob"),
            _level_exact("dob"),
            {
                "sql_condition": (
                    "dob_month_l = dob_month_r AND dob_day_l = dob_day_r "
                    "AND dob_year_l IS NOT NULL AND dob_year_r IS NOT NULL "
                    "AND dob_year_l != dob_year_r"
                ),
                "label_for_charts": "Day off",
            },
            {
                "sql_condition": (
                    "(dob_month_l = dob_month_r AND dob_day_l = dob_day_r) "
                    "OR (dob_year_l = dob_year_r)"
                ),
                "label_for_charts": "Partial",
            },
            _level_else(),
        ],
    }


def _address_comparison() -> dict[str, Any]:
    return {
        "output_column_name": "address",
        "comparison_levels": [
            _level_null("address"),
            _level_jw("address", 0.9, "Strong"),
            _level_jw("address", 0.7, "Partial"),
            _level_else(),
        ],
    }


def _name_embedding_comparison() -> dict[str, Any]:
    return {
        "output_column_name": "name_embedding",
        "comparison_levels": [
            _level_null("name_embedding"),
            _level_cosine("name_embedding", 0.95, "Strong"),
            _level_cosine("name_embedding", 0.85, "Fuzzy"),
            _level_cosine("name_embedding", 0.70, "Weak"),
            {
                "sql_condition": (
                    "list_cosine_similarity(name_embedding_l, name_embedding_r) < 0.70"
                ),
                "label_for_charts": "Disagree",
            },
        ],
    }


def _face_embedding_comparison() -> dict[str, Any]:
    return {
        "output_column_name": "face_embedding",
        "comparison_levels": [
            _level_null("face_embedding"),
            _level_cosine("face_embedding", 0.75, "Strong"),
            _level_cosine("face_embedding", 0.60, "Fuzzy"),
            {
                "sql_condition": (
                    "list_cosine_similarity(face_embedding_l, face_embedding_r) < 0.60"
                ),
                "label_for_charts": "Disagree",
            },
        ],
    }


def _voice_fingerprint_comparison() -> dict[str, Any]:
    return {
        "output_column_name": "voice_fingerprint",
        "comparison_levels": [
            _level_null("voice_fingerprint"),
            _level_cosine("voice_fingerprint", 0.80, "Strong"),
            _level_cosine("voice_fingerprint", 0.65, "Fuzzy"),
            {
                "sql_condition": (
                    "list_cosine_similarity(voice_fingerprint_l, voice_fingerprint_r)"
                    " < 0.65"
                ),
                "label_for_charts": "Disagree",
            },
        ],
    }


def _company_comparison() -> dict[str, Any]:
    return {
        "output_column_name": "company",
        "comparison_levels": [
            _level_null("company"),
            _level_exact("company"),
            _level_jw("company", 0.85, "Fuzzy"),
            _level_else(),
        ],
    }


def _government_id_comparison() -> dict[str, Any]:
    return {
        "output_column_name": "government_id",
        "comparison_levels": [
            _level_null("government_id"),
            _level_exact("government_id"),
            _level_else(),
        ],
    }


# ---------------------------------------------------------------------------
# Source calibration lookup – per-source confidence multiplier
# ---------------------------------------------------------------------------

_WEIGHT_CACHE: dict[tuple[UUID, str], float] = {}


async def _get_multiplier(
    tenant_id: UUID,
    source_kind: str,
    db_session: AsyncSession,
) -> float:
    cache_key = (tenant_id, source_kind)
    cached = _WEIGHT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    result = await db_session.execute(
        text(
            "SELECT confidence_multiplier "
            "FROM source_calibration "
            "WHERE tenant_id = :tenant_id AND source_kind = :source_kind"
        ),
        {"tenant_id": tenant_id, "source_kind": source_kind},
    )
    row = result.scalar_one_or_none()
    multiplier = float(row) if row is not None else 0.85
    _WEIGHT_CACHE[cache_key] = multiplier
    return multiplier


async def effective_weight(
    raw_bits: float,
    source_kind: str,
    *,
    tenant_id: UUID,
    db_session: AsyncSession,
) -> float:
    """Apply per-source confidence multiplier from ``source_calibration`` table.

    Returns ``raw_bits * multiplier`` where the multiplier defaults to 0.85
    when no calibration row exists for the given (tenant, source_kind) pair.

    Results are cached in ``_WEIGHT_CACHE`` for the lifetime of the current
    top-level ``score_candidates`` call (cleared on entry).
    """
    multiplier = await _get_multiplier(tenant_id, source_kind, db_session)
    return raw_bits * multiplier


# ---------------------------------------------------------------------------
# Main scoring entrypoint
# ---------------------------------------------------------------------------


async def score_candidates(
    candidates_df: pd.DataFrame,
    *,
    tenant_id: UUID,
    db_session: AsyncSession,
) -> SplinkResult:
    """Score candidate pairs using Splink (DuckDB backend) or Python fallback.

    Parameters
    ----------
    candidates_df:
        DataFrame with ``_l`` / ``_r`` suffixed columns for each comparison
        field plus ``person_id_l``, ``person_id_r``, ``source_kind``, and
        ``blocking_key``.
    tenant_id:
        Tenant scope for source calibration lookup.
    db_session:
        SQLAlchemy async session for database queries.

    Returns
    -------
    SplinkResult with scored pairs and training metadata.
    """
    _WEIGHT_CACHE.clear()

    if SPLINK_AVAILABLE:
        try:
            return await _score_via_splink(candidates_df, tenant_id, db_session)
        except Exception:
            logger.warning("splink_scoring_failed_falling_back", exc_info=True)

    return await _score_via_fallback(candidates_df, tenant_id, db_session)


# ---------------------------------------------------------------------------
# Splink (DuckDB) scoring path
# ---------------------------------------------------------------------------


async def _score_via_splink(
    candidates_df: pd.DataFrame,
    tenant_id: UUID,
    db_session: AsyncSession,
) -> SplinkResult:
    """Score via Splink DuckDBLinker with EM training."""
    settings = build_splink_settings()
    linker = DuckDBLinker(candidates_df, settings)

    em_iters = 0
    for br in _EM_BLOCKING_RULES:
        linker.estimate_parameters_using_expectation_maximisation(
            blocking_rule=br,
            fix_u=True,
        )
        em_iters += 1

    df_predictions = linker.predict(threshold_match_probability=0.0)
    records = df_predictions.as_pandas_dataframe()

    scored_pairs: list[ScoredPair] = []
    for _, row in records.iterrows():
        p_a = UUID(str(row["person_id_l"]))
        p_b = UUID(str(row["person_id_r"]))

        weight_raw = float(row.get("match_weight", 0.0))

        per_field_raw: dict[str, float] = {}
        for col in records.columns:
            if col.startswith("bf_"):
                field = col.removeprefix("bf_")
                per_field_raw[field] = float(row.get(col, 0))

        blocking_key = str(row.get("blocking_key", ""))
        source_kind = str(row.get("source_kind", "unknown"))

        multiplier = await _get_multiplier(tenant_id, source_kind, db_session)
        weight_adj = weight_raw * multiplier
        prob_adj = _bits_to_probability(weight_adj)
        per_field_adj = {k: v * multiplier for k, v in per_field_raw.items()}

        scored_pairs.append(
            ScoredPair(
                person_a_id=p_a,
                person_b_id=p_b,
                match_probability=prob_adj,
                match_weight_bits=weight_adj,
                per_field_weights=per_field_adj,
                blocking_key=blocking_key,
                source_kind=source_kind,
            )
        )

    return SplinkResult(
        scored_pairs=scored_pairs,
        model_version=_model_version(),
        em_iterations=em_iters,
        em_converged=True,
        trained_on_pairs=len(candidates_df),
        settings_json=settings,
    )


# ---------------------------------------------------------------------------
# Pure-Python fallback scoring path
# ---------------------------------------------------------------------------


async def _score_via_fallback(
    candidates_df: pd.DataFrame,
    tenant_id: UUID,
    db_session: AsyncSession,
) -> SplinkResult:
    """Score candidate pairs using the pure-Python comparison functions from
    ``comparisons.py``.

    Used when Splink / DuckDB are not available (e.g. CI environments).
    """
    scored_pairs: list[ScoredPair] = []
    columns = set(candidates_df.columns)

    for _, row in candidates_df.iterrows():
        p_a_raw = row.get("person_id_l")
        p_b_raw = row.get("person_id_r")
        if p_a_raw is None or p_b_raw is None:
            continue
        p_a = UUID(str(p_a_raw))
        p_b = UUID(str(p_b_raw))

        source_kind = str(row.get("source_kind", "unknown"))
        blocking_key = str(row.get("blocking_key", ""))

        total_raw_bits = 0.0
        field_raw_bits: dict[str, float] = {}

        for field_name, comp_fn in _FIELD_COMPARISONS.items():
            col_l = f"{field_name}_l"
            col_r = f"{field_name}_r"
            if col_l not in columns or col_r not in columns:
                continue

            val_l = _extract_value(row, col_l)
            val_r = _extract_value(row, col_r)

            a_facts: tuple[FactRef, ...] = ()
            b_facts: tuple[FactRef, ...] = ()

            try:
                outcome = comp_fn(val_l, val_r, a_facts=a_facts, b_facts=b_facts)
            except TypeError:
                outcome = comp_fn(val_l, val_r)

            field_raw_bits[field_name] = outcome.raw_bits
            total_raw_bits += outcome.raw_bits

        multiplier = await _get_multiplier(tenant_id, source_kind, db_session)
        total_adj = total_raw_bits * multiplier
        prob = _bits_to_probability(total_adj)
        per_field_adj = {k: v * multiplier for k, v in field_raw_bits.items()}

        scored_pairs.append(
            ScoredPair(
                person_a_id=p_a,
                person_b_id=p_b,
                match_probability=prob,
                match_weight_bits=total_adj,
                per_field_weights=per_field_adj,
                blocking_key=blocking_key,
                source_kind=source_kind,
            )
        )

    return SplinkResult(
        scored_pairs=scored_pairs,
        model_version="fallback-0.1.0",
        em_iterations=0,
        em_converged=True,
        trained_on_pairs=len(candidates_df),
        settings_json=build_splink_settings(),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bits_to_probability(bits: float) -> float:
    """Convert log2 bits (match weight) to a match probability.

    Uses the logistic function: p = 2^bits / (1 + 2^bits).
    Clamps extreme values to avoid overflow.
    """
    if bits >= 50:
        return 1.0
    if bits <= -50:
        return 0.0
    odds = pow(2.0, bits)
    return odds / (1.0 + odds)


def _model_version() -> str:
    return datetime.now(UTC).strftime("splink-%Y%m%d%H%M%S")


def _extract_value(row: pd.Series, column: str) -> Any:
    val = row.get(column)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, str):
        # Attempt JSON parse for columns like dob (dict) or embeddings (list)
        stripped = val.strip()
        if stripped and stripped[0] in ("{", "[", '"'):
            try:
                return json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                pass
    return val
