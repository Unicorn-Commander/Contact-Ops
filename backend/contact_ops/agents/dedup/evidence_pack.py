from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass
class SideBySideField:
    field: str
    a_value: Any
    b_value: Any
    a_source: str | None = None
    b_source: str | None = None
    a_confidence: float = 1.0
    b_confidence: float = 1.0
    comparison_level: str = "null_exclude"
    contribution_bits: float = 0.0
    tf_adjustment: float = 0.0


@dataclass
class SourceProvenanceEntry:
    fact_id: UUID
    source_kind: str
    source_id: str
    imported_at: datetime
    source_confidence_multiplier: float = 1.0


@dataclass
class WhatChangesIfMerged:
    survivor_id: UUID
    alias_id: UUID
    fields_added_to_survivor: list[str] = field(default_factory=list)
    fields_replaced_on_survivor: list[str] = field(default_factory=list)
    facts_promoted_count: int = 0
    edges_remapped_count: int = 0


@dataclass
class TieBreakerInfo:
    model: str
    verdict: str
    reason: str
    raw_response: str


@dataclass
class ClusterContext:
    cluster_size: int
    other_pair_ids: list[UUID]
    cluster_repair_applied: bool = False


@dataclass
class EvidencePack:
    version: int = 1
    candidate: dict[str, Any] = field(default_factory=dict)
    side_by_side: list[dict[str, Any]] = field(default_factory=list)
    splink_chart_data: dict[str, Any] = field(default_factory=dict)
    source_provenance: list[dict[str, Any]] = field(default_factory=list)
    what_changes_if_merged: dict[str, Any] = field(default_factory=dict)
    tie_breaker: dict[str, Any] | None = None
    cluster_context: dict[str, Any] | None = None
    unmerge_note: str = (
        "Approving this merge can be reversed via unmerge() within 90 days. "
        "The alias's original facts are preserved."
    )


def build_evidence_pack(
    person_a: dict[str, Any],
    person_b: dict[str, Any],
    *,
    match_probability: float,
    match_weight_bits: float,
    per_field_comparisons: list[SideBySideField],
    source_provenance: list[SourceProvenanceEntry],
    what_changes: WhatChangesIfMerged,
    tie_breaker_result: TieBreakerInfo | None = None,
    cluster_context: ClusterContext | None = None,
    splink_chart_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the evidence pack dict that lands in action_event.payload_after.

    Returns a dict matching the EvidencePack structure that the inbox UI's
    EvidencePanel and StructuredDiff components will render.
    """
    pack = EvidencePack(
        candidate={
            "person_a_id": str(person_a.get("id")),
            "person_b_id": str(person_b.get("id")),
            "match_probability": match_probability,
            "match_weight_bits": match_weight_bits,
        },
        side_by_side=[_field_to_dict(f) for f in per_field_comparisons],
        splink_chart_data=splink_chart_json or {},
        source_provenance=[_provenance_to_dict(p) for p in source_provenance],
        what_changes_if_merged={
            "survivor_id": str(what_changes.survivor_id),
            "alias_id": str(what_changes.alias_id),
            "fields_added_to_survivor": what_changes.fields_added_to_survivor,
            "fields_replaced_on_survivor": what_changes.fields_replaced_on_survivor,
            "facts_promoted_count": what_changes.facts_promoted_count,
            "edges_remapped_count": what_changes.edges_remapped_count,
        },
        tie_breaker={
            "model": tie_breaker_result.model,
            "verdict": tie_breaker_result.verdict,
            "reason": tie_breaker_result.reason,
            "raw_response": tie_breaker_result.raw_response,
        }
        if tie_breaker_result
        else None,
        cluster_context={
            "cluster_size": cluster_context.cluster_size,
            "other_pair_ids": [str(pid) for pid in cluster_context.other_pair_ids],
            "cluster_repair_applied": cluster_context.cluster_repair_applied,
        }
        if cluster_context
        else None,
    )
    return _dataclass_to_dict(pack)


def _field_to_dict(f: SideBySideField) -> dict[str, Any]:
    return {
        "field": f.field,
        "a_value": _safe_str(f.a_value),
        "b_value": _safe_str(f.b_value),
        "a_source": f.a_source,
        "b_source": f.b_source,
        "a_confidence": f.a_confidence,
        "b_confidence": f.b_confidence,
        "comparison_level": f.comparison_level,
        "contribution_bits": f.contribution_bits,
        "tf_adjustment": f.tf_adjustment,
    }


def _provenance_to_dict(p: SourceProvenanceEntry) -> dict[str, Any]:
    return {
        "fact_id": str(p.fact_id),
        "source_kind": p.source_kind,
        "source_id": p.source_id,
        "imported_at": p.imported_at.isoformat(),
        "source_confidence_multiplier": p.source_confidence_multiplier,
    }


def _safe_str(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return str(v)


def _dataclass_to_dict(obj: Any) -> dict[str, Any]:
    """Recursively convert a dataclass to a plain dict, handling UUIDs and datetimes."""
    if is_dataclass(obj):
        obj = asdict(obj)
    return _convert_values(obj)


def _convert_values(obj: Any) -> Any:
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if is_dataclass(obj):
        return _convert_values(asdict(obj))
    if isinstance(obj, dict):
        return {k: _convert_values(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_convert_values(v) for v in obj]
    return obj
