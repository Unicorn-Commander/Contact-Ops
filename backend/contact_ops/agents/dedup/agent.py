"""DedupAgent --- orchestrates the full person deduplication pipeline.

Pipeline steps
--------------
1. Deterministic blocking (SQL-based email / phone / DM / Soundex) +
   Qdrant embedding blocking
2. Splink scoring (MAR handling + TF adjustment + per-source multipliers)
3. Connected-component building + cluster repair (max-size-5 guard)
4. Optional LLM tie-breaker for borderline (0.40 -- 0.65) pairs
5. Evidence pack building
6. Action-event emission via ``propose_action``
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.agents.base import (
    AgentContext,
    AgentResult,
    BaseAgent,
    Reversibility,
)
from contact_ops.agents.dedup.blocking import (
    CandidatePair,
    run_blocking_pipeline,
)
from contact_ops.agents.dedup.cluster_repair import (
    Edge,
    build_components,
    repair_clusters,
)
from contact_ops.agents.dedup.evidence_pack import (
    SideBySideField,
    SourceProvenanceEntry,
    WhatChangesIfMerged,
    build_evidence_pack,
)
from contact_ops.agents.dedup.splink_runner import (
    ScoredPair,
    SplinkResult,
    score_candidates,
)
from contact_ops.agents.dedup.tie_breaker import run_tie_breaker
from contact_ops.agents.registry import AgentClass, AgentDef, register_agent
from contact_ops.agents.trust import TrustTier

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent definition --- registered at module import time
# ---------------------------------------------------------------------------

DEDUP_AGENT_DEF = AgentDef(
    slug="dedup",
    name="Dedup Agent",
    version="0.1.0",
    agent_class=AgentClass.BATCH,
    description="Probabilistic person deduplication using Splink + Qdrant blocking",
    cost_budget_monthly_cents=2000,
    initial_trust_tier=TrustTier.T0_PROBATION,
    triggers=("0 */6 * * *", "manual"),
    declared_capabilities=(
        "person:read",
        "person:write",
        "dedup:propose",
        "dedup:apply",
    ),
)

register_agent(DEDUP_AGENT_DEF)

# ---------------------------------------------------------------------------
# Field names the Splink DataFrame and side-by-side evidence expect
# ---------------------------------------------------------------------------

_SIDE_BY_SIDE_FIELDS: tuple[tuple[str, str], ...] = (
    ("given_name", "first_name"),
    ("family_name", "last_name"),
    ("display_name", "display_name"),
    ("email", "email"),
    ("phone", "phone"),
    ("birthday", "dob"),
    ("address", "address"),
    ("occupation_title", "occupation_title"),
    ("company", "company"),
)

_BAND_THRESHOLDS: list[tuple[float, str]] = [
    (0.95, "auto_merge_eligible"),
    (0.65, "single_review"),
    (0.40, "batch_review"),
]


# ===================================================================
# DedupAgent
# ===================================================================


class DedupAgent(BaseAgent):
    """Person deduplication agent.

    Pipeline
    --------
    1. ``run_blocking_pipeline`` --- deterministic + embedding blocking
    2. ``score_candidates`` --- Splink (DuckDB) or Python fallback
    3. ``build_components`` + ``repair_clusters`` --- max-size-5 guard
    4. Optional LLM tie-breaker for borderline pairs
    5. Evidence pack building
    6. ``propose_action`` for each edge above threshold
    """

    def __init__(self) -> None:
        super().__init__(DEDUP_AGENT_DEF)

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    async def _run(self, ctx: AgentContext) -> AgentResult:
        """Orchestrate the full dedup pipeline for *ctx.tenant_id*."""
        proposals_emitted = 0
        tenant_id = ctx.tenant_id
        db = ctx.db

        # Step 1 -- blocking
        blocking_result = await run_blocking_pipeline(
            tenant_id=tenant_id,
            db_session=db,
            qdrant_client=None,  # TODO: wire from ctx
        )

        if not blocking_result.candidates:
            return AgentResult(
                proposals_emitted=0,
                decision_summary="No candidate pairs found after blocking",
            )

        # Step 2 -- build the wide DataFrame Splink expects
        candidates_df = await _build_candidates_dataframe(
            candidates=blocking_result.candidates,
            tenant_id=tenant_id,
            db=db,
        )

        if candidates_df.empty:
            return AgentResult(
                proposals_emitted=0,
                decision_summary="No candidate data could be loaded",
            )

        # Step 3 -- Splink scoring
        splink_result = await score_candidates(
            candidates_df=candidates_df,
            tenant_id=tenant_id,
            db_session=db,
        )

        # Step 4 -- build edges, components, repair clusters
        edges = [
            Edge(
                person_a_id=sp.person_a_id,
                person_b_id=sp.person_b_id,
                score=sp.match_probability,
                weight_bits=sp.match_weight_bits,
            )
            for sp in splink_result.scored_pairs
            if sp.match_probability >= 0.40
        ]

        clusters = build_components(edges, threshold=0.40)
        repair_result = repair_clusters(clusters, max_size=5)

        final_edges: list[Edge] = []
        for cluster in repair_result.clusters:
            final_edges.extend(cluster.edges)

        # Build a lookup from pair-key → ScoredPair
        scored_map: dict[frozenset[UUID], ScoredPair] = {
            frozenset({sp.person_a_id, sp.person_b_id}): sp
            for sp in splink_result.scored_pairs
        }

        # Step 5 -- process each final edge
        for edge in final_edges:
            pair_key = frozenset({edge.person_a_id, edge.person_b_id})
            pair = scored_map.get(pair_key)
            if pair is None:
                continue

            prob = edge.score
            band = _determine_band(prob)
            if band is None:
                continue

            # Optional tie-breaker for borderline pairs
            tie_breaker_result = None
            if 0.40 <= prob < 0.65:
                tie_breaker_result = await self._run_tie_breaker(
                    edge=edge,
                    tenant_id=tenant_id,
                    ctx=ctx,
                    db=db,
                )
                if tie_breaker_result is not None:
                    adjusted_bits = edge.weight_bits + tie_breaker_result.bit_nudge
                    prob = _bits_to_probability(adjusted_bits)
                    band = _determine_band(prob) or band

            # Build the evidence pack that lands in payload_after
            evidence_pack = await self._build_evidence(
                person_a_id=edge.person_a_id,
                person_b_id=edge.person_b_id,
                match_probability=prob,
                match_weight_bits=edge.weight_bits,
                scored_pair=pair,
                db=db,
            )

            # Find the blocking key(s) that produced this pair
            blocking_keys = [
                p.blocking_key
                for p in blocking_result.candidates
                if {p.person_a_id, p.person_b_id} == pair_key
            ]

            await self.propose_action(
                ctx=ctx,
                event_type="dedup.propose_merge",
                aggregate_type="person",
                aggregate_id=edge.person_a_id,
                payload_before=None,
                payload_after=evidence_pack,
                confidence=prob,
                reversibility=Reversibility.REVERSIBLE,
                evidence={
                    "blocking_keys": blocking_keys,
                    "splink_model_version": splink_result.model_version,
                    "cluster_repair_applied": repair_result.black_holes_detected > 0,
                },
                rationale=(
                    f"Splink scored {prob:.4f} via {band} band; "
                    f"blocking keys: {blocking_keys}"
                ),
            )
            proposals_emitted += 1

        return AgentResult(
            proposals_emitted=proposals_emitted,
            decision_summary=(
                f"Scored {len(splink_result.scored_pairs)} candidate pairs from "
                f"{len(blocking_result.candidates)} blocking candidates; "
                f"emitted {proposals_emitted} proposals"
            ),
        )

    # ------------------------------------------------------------------
    # Tie-breaker wrapper
    # ------------------------------------------------------------------

    async def _run_tie_breaker(
        self,
        *,
        edge: Edge,
        tenant_id: UUID,
        ctx: AgentContext,
        db: AsyncSession,
    ):
        """Run the LLM tie-breaker with cost-guard and error handling.

        Returns ``None`` when the tie-breaker is unavailable or fails
        (the pipeline falls back to the pure Splink score).
        """
        try:
            person_a = await _load_person_summary(edge.person_a_id, db=db)
            person_b = await _load_person_summary(edge.person_b_id, db=db)
            return await run_tie_breaker(
                person_a=person_a,
                person_b=person_b,
                tenant_id=tenant_id,
                cost_guard=ctx.cost_guard,
                db_session=db,
            )
        except Exception:
            logger.warning(
                "tie_breaker_failed",
                person_a_id=str(edge.person_a_id),
                person_b_id=str(edge.person_b_id),
                exc_info=True,
            )
            return None

    # ------------------------------------------------------------------
    # Evidence-pack builder
    # ------------------------------------------------------------------

    async def _build_evidence(
        self,
        *,
        person_a_id: UUID,
        person_b_id: UUID,
        match_probability: float,
        match_weight_bits: float,
        scored_pair: ScoredPair,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Assemble the full evidence pack for a candidate pair."""
        person_a = await _load_person_summary(person_a_id, db=db)
        person_b = await _load_person_summary(person_b_id, db=db)

        side_by_side = _build_side_by_side(
            person_a, person_b, scored_pair.per_field_weights,
        )
        source_provenance = await _load_source_provenance(
            person_a_id, person_b_id, db=db,
        )
        what_changes = WhatChangesIfMerged(
            survivor_id=person_a_id,
            alias_id=person_b_id,
        )

        return build_evidence_pack(
            person_a=person_a,
            person_b=person_b,
            match_probability=match_probability,
            match_weight_bits=match_weight_bits,
            per_field_comparisons=side_by_side,
            source_provenance=source_provenance,
            what_changes=what_changes,
        )


# ===================================================================
# Module-level helpers
# ===================================================================


async def _build_candidates_dataframe(
    *,
    candidates: list[CandidatePair],
    tenant_id: UUID,
    db: AsyncSession,
) -> pd.DataFrame:
    """Build the wide DataFrame that ``score_candidates`` expects.

    For every unique person referenced in *candidates* we load their
    core row + primary email + primary phone, then assemble one
    DataFrame row per candidate with ``_l`` / ``_r`` suffixed columns.
    """
    # Collect unique person IDs
    all_ids: set[UUID] = set()
    for c in candidates:
        all_ids.add(c.person_a_id)
        all_ids.add(c.person_b_id)

    if not all_ids:
        return pd.DataFrame()

    id_list_literal = "{" + ",".join(str(pid) for pid in all_ids) + "}"

    # Load persons
    person_rows = await db.execute(
        text("""
            SELECT
                p.id,
                p.given_name,
                p.family_name,
                p.display_name,
                p.birthday,
                EXTRACT(YEAR FROM p.birthday)::int  AS dob_year,
                EXTRACT(MONTH FROM p.birthday)::int  AS dob_month,
                EXTRACT(DAY FROM p.birthday)::int    AS dob_day,
                p.birthday::text                     AS dob,
                p.name_embedding,
                p.face_embedding,
                p.voice_fingerprint,
                p.company,
                p.government_id,
                p.address,
                p.occupation_title,
                p.nickname,
                p.headline
            FROM persons p
            WHERE p.id = ANY(:ids::uuid[])
        """),
        {"ids": id_list_literal},
    )
    person_map: dict[UUID, dict[str, Any]] = {}
    for row in person_rows.mappings():
        pid = UUID(str(row["id"]))
        rec = dict(row)
        rec.pop("id")
        # Cast embedding columns to lists
        for emb_col in ("name_embedding", "face_embedding", "voice_fingerprint"):
            val = rec.get(emb_col)
            if val is not None:
                rec[emb_col] = list(val)
        person_map[pid] = rec

    # Load primary emails
    email_rows = await db.execute(
        text("""
            SELECT DISTINCT ON (person_id)
                person_id,
                address
            FROM emails
            WHERE person_id = ANY(:ids::uuid[])
              AND is_primary = true
            ORDER BY person_id, id
        """),
        {"ids": id_list_literal},
    )
    email_map: dict[UUID, str | None] = {}
    for row in email_rows:
        email_map[UUID(str(row["person_id"]))] = row["address"]
    for pid in all_ids:
        email_map.setdefault(pid, None)

    # Load primary phones
    phone_rows = await db.execute(
        text("""
            SELECT DISTINCT ON (person_id)
                person_id,
                e164
            FROM phones
            WHERE person_id = ANY(:ids::uuid[])
              AND is_primary = true
            ORDER BY person_id, id
        """),
        {"ids": id_list_literal},
    )
    phone_map: dict[UUID, str | None] = {}
    for row in phone_rows:
        phone_map[UUID(str(row["person_id"]))] = row["e164"]
    for pid in all_ids:
        phone_map.setdefault(pid, None)

    # Build rows
    rows: list[dict[str, Any]] = []
    for c in candidates:
        pa = person_map.get(c.person_a_id)
        pb = person_map.get(c.person_b_id)
        if pa is None or pb is None:
            continue

        row: dict[str, Any] = {
            "person_id_l": str(c.person_a_id),
            "person_id_r": str(c.person_b_id),
            "blocking_key": c.blocking_key,
            "source_kind": "dedup",
        }

        _add_field_pair(row, pa, pb, "given_name", "first_name")
        _add_field_pair(row, pa, pb, "family_name", "last_name")
        _add_lookup_pair(row, c.person_a_id, c.person_b_id, email_map, "email")
        _add_lookup_pair(row, c.person_a_id, c.person_b_id, phone_map, "phone")
        _add_field_pair(row, pa, pb, "dob", "dob")
        _add_field_pair(row, pa, pb, "dob_month", "dob_month")
        _add_field_pair(row, pa, pb, "dob_day", "dob_day")
        _add_field_pair(row, pa, pb, "dob_year", "dob_year")
        _add_field_pair(row, pa, pb, "address", "address")
        _add_field_pair(row, pa, pb, "name_embedding", "name_embedding")
        _add_field_pair(row, pa, pb, "face_embedding", "face_embedding")
        _add_field_pair(row, pa, pb, "voice_fingerprint", "voice_fingerprint")
        _add_field_pair(row, pa, pb, "company", "company")
        _add_field_pair(row, pa, pb, "government_id", "government_id")

        rows.append(row)

    return pd.DataFrame(rows)


def _add_field_pair(
    row: dict[str, Any],
    pa: dict[str, Any],
    pb: dict[str, Any],
    key: str,
    suffix: str,
) -> None:
    """Add ``{suffix}_l`` and ``{suffix}_r`` to *row* from person dicts."""
    row[f"{suffix}_l"] = pa.get(key)
    row[f"{suffix}_r"] = pb.get(key)


def _add_lookup_pair(
    row: dict[str, Any],
    pid_a: UUID,
    pid_b: UUID,
    lookup_map: dict[UUID, str | None],
    suffix: str,
) -> None:
    """Add ``{suffix}_l`` and ``{suffix}_r`` from a UUID-keyed lookup map."""
    row[f"{suffix}_l"] = lookup_map.get(pid_a)
    row[f"{suffix}_r"] = lookup_map.get(pid_b)


def _determine_band(prob: float) -> str | None:
    """Return the routing band for a match probability, or ``None`` to discard."""
    for threshold, band in _BAND_THRESHOLDS:
        if prob >= threshold:
            return band
    return None


def _bits_to_probability(bits: float) -> float:
    """Convert log-odds bits to a match probability (logistic function)."""
    if bits >= 50:
        return 1.0
    if bits <= -50:
        return 0.0
    odds = pow(2.0, bits)
    return odds / (1.0 + odds)


async def _load_person_summary(
    person_id: UUID,
    *,
    db: AsyncSession,
) -> dict[str, Any]:
    """Load a person's field summary for evidence-pack building."""
    result = await db.execute(
        text("""
            SELECT
                id, display_name, given_name, family_name,
                birthday, occupation_title, nickname, headline,
                company, address, government_id
            FROM persons
            WHERE id = CAST(:pid AS uuid)
        """),
        {"pid": str(person_id)},
    )
    row = result.mappings().first()
    if row is None:
        return {}

    person = dict(row)

    # Primary email
    email_result = await db.execute(
        text("""
            SELECT address FROM emails
            WHERE person_id = CAST(:pid AS uuid) AND is_primary = true
            LIMIT 1
        """),
        {"pid": str(person_id)},
    )
    email_row = email_result.mappings().first()
    if email_row:
        person["email"] = email_row["address"]

    # Primary phone
    phone_result = await db.execute(
        text("""
            SELECT e164 FROM phones
            WHERE person_id = CAST(:pid AS uuid) AND is_primary = true
            LIMIT 1
        """),
        {"pid": str(person_id)},
    )
    phone_row = phone_result.mappings().first()
    if phone_row:
        person["phone"] = phone_row["e164"]

    return person


async def _load_source_provenance(
    person_a_id: UUID,
    person_b_id: UUID,
    *,
    db: AsyncSession,
) -> list[SourceProvenanceEntry]:
    """Load source provenance for facts on both persons."""
    result = await db.execute(
        text("""
            SELECT
                f.id,
                s.source_type,
                s.source_uri,
                s.source_record_id,
                s.source_reliability_base,
                s.retrieved_at
            FROM facts f
            JOIN sources s ON s.id = f.source_id
            WHERE f.subject_id IN (CAST(:a AS uuid), CAST(:b AS uuid))
              AND f.subject_kind = 'person'
        """),
        {"a": str(person_a_id), "b": str(person_b_id)},
    )
    entries: list[SourceProvenanceEntry] = []
    for row in result.mappings():
        entries.append(
            SourceProvenanceEntry(
                fact_id=UUID(str(row["id"])),
                source_kind=str(row["source_type"]),
                source_id=f"{row['source_uri']}:{row['source_record_id'] or ''}",
                imported_at=row["retrieved_at"],
                source_confidence_multiplier=float(
                    row["source_reliability_base"] or 0.7
                ),
            )
        )
    return entries


def _build_side_by_side(
    person_a: dict[str, Any],
    person_b: dict[str, Any],
    per_field_weights: dict[str, float],
) -> list[SideBySideField]:
    """Build per-field comparison list for the evidence pack."""
    result: list[SideBySideField] = []
    for person_key, splink_key in _SIDE_BY_SIDE_FIELDS:
        a_val = person_a.get(person_key)
        b_val = person_b.get(person_key)
        if a_val is None and b_val is None:
            continue

        contribution_bits = per_field_weights.get(splink_key, 0.0)
        comparison_level = "exact" if a_val == b_val else "fuzzy"

        result.append(
            SideBySideField(
                field=person_key,
                a_value=a_val,
                b_value=b_val,
                comparison_level=comparison_level,
                contribution_bits=contribution_bits,
            )
        )
    return result


# ---------------------------------------------------------------------------
# Explicit re-exports
# ---------------------------------------------------------------------------

__all__ = [
    "DedupAgent",
    "DEDUP_AGENT_DEF",
]
