from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from contact_ops.agents.dedup.evidence_pack import (
    ClusterContext,
    SideBySideField,
    SourceProvenanceEntry,
    TieBreakerInfo,
    WhatChangesIfMerged,
    build_evidence_pack,
)


class TestBuildEvidencePack:
    def test_build_evidence_pack_shape(self) -> None:
        a_id = uuid4()
        b_id = uuid4()
        result = build_evidence_pack(
            person_a={"id": str(a_id), "name": "Alice"},
            person_b={"id": str(b_id), "name": "Bob"},
            match_probability=0.92,
            match_weight_bits=12.5,
            per_field_comparisons=[],
            source_provenance=[],
            what_changes=WhatChangesIfMerged(
                survivor_id=a_id,
                alias_id=b_id,
                fields_added_to_survivor=["email"],
                fields_replaced_on_survivor=[],
                facts_promoted_count=1,
                edges_remapped_count=0,
            ),
        )
        assert "version" in result
        assert "candidate" in result
        assert "side_by_side" in result
        assert "source_provenance" in result
        assert "what_changes_if_merged" in result
        assert "tie_breaker" in result
        assert "cluster_context" in result
        assert "unmerge_note" in result

    def test_side_by_side_fields(self) -> None:
        a_id = uuid4()
        b_id = uuid4()
        fields = [
            SideBySideField(
                field="email",
                a_value="REMOVED",
                b_value="REMOVED",
                a_source="csv_import",
                b_source="carddav",
                a_confidence=0.9,
                b_confidence=1.0,
                comparison_level="disagree",
                contribution_bits=-2.0,
            ),
        ]
        result = build_evidence_pack(
            person_a={"id": str(a_id)},
            person_b={"id": str(b_id)},
            match_probability=0.5,
            match_weight_bits=0.0,
            per_field_comparisons=fields,
            source_provenance=[],
            what_changes=WhatChangesIfMerged(
                survivor_id=a_id,
                alias_id=b_id,
            ),
        )
        sbs = result["side_by_side"]
        assert len(sbs) == 1
        entry = sbs[0]
        assert entry["field"] == "email"
        assert entry["a_value"] == "REMOVED"
        assert entry["b_value"] == "REMOVED"
        assert entry["comparison_level"] == "disagree"
        assert entry["contribution_bits"] == -2.0

    def test_source_provenance_included(self) -> None:
        a_id = uuid4()
        b_id = uuid4()
        fact_id = uuid4()
        now = datetime.now(timezone.utc)
        provenance = [
            SourceProvenanceEntry(
                fact_id=fact_id,
                source_kind="carddav",
                source_id="src_1",
                imported_at=now,
                source_confidence_multiplier=0.95,
            ),
        ]
        result = build_evidence_pack(
            person_a={"id": str(a_id)},
            person_b={"id": str(b_id)},
            match_probability=0.5,
            match_weight_bits=0.0,
            per_field_comparisons=[],
            source_provenance=provenance,
            what_changes=WhatChangesIfMerged(
                survivor_id=a_id,
                alias_id=b_id,
            ),
        )
        prov = result["source_provenance"]
        assert len(prov) == 1
        entry = prov[0]
        assert entry["fact_id"] == str(fact_id)
        assert entry["source_kind"] == "carddav"
        assert entry["source_id"] == "src_1"
        assert entry["imported_at"] == now.isoformat()

    def test_tie_breaker_included(self) -> None:
        a_id = uuid4()
        b_id = uuid4()
        tie = TieBreakerInfo(
            model="gpt-4",
            verdict="SAME_PERSON",
            reason="Both records share email and phone",
            raw_response='{"verdict": "SAME_PERSON"}',
        )
        result = build_evidence_pack(
            person_a={"id": str(a_id)},
            person_b={"id": str(b_id)},
            match_probability=0.6,
            match_weight_bits=3.0,
            per_field_comparisons=[],
            source_provenance=[],
            what_changes=WhatChangesIfMerged(
                survivor_id=a_id,
                alias_id=b_id,
            ),
            tie_breaker_result=tie,
        )
        tb = result["tie_breaker"]
        assert tb["model"] == "gpt-4"
        assert tb["verdict"] == "SAME_PERSON"
        assert tb["reason"] == "Both records share email and phone"

    def test_cluster_context_included(self) -> None:
        a_id = uuid4()
        b_id = uuid4()
        other = uuid4()
        ctx = ClusterContext(
            cluster_size=3,
            other_pair_ids=[other],
            cluster_repair_applied=True,
        )
        result = build_evidence_pack(
            person_a={"id": str(a_id)},
            person_b={"id": str(b_id)},
            match_probability=0.6,
            match_weight_bits=3.0,
            per_field_comparisons=[],
            source_provenance=[],
            what_changes=WhatChangesIfMerged(
                survivor_id=a_id,
                alias_id=b_id,
            ),
            cluster_context=ctx,
        )
        cc = result["cluster_context"]
        assert cc["cluster_size"] == 3
        assert str(other) in cc["other_pair_ids"]
        assert cc["cluster_repair_applied"] is True

    def test_unmerge_note_present(self) -> None:
        a_id = uuid4()
        b_id = uuid4()
        result = build_evidence_pack(
            person_a={"id": str(a_id)},
            person_b={"id": str(b_id)},
            match_probability=0.5,
            match_weight_bits=0.0,
            per_field_comparisons=[],
            source_provenance=[],
            what_changes=WhatChangesIfMerged(
                survivor_id=a_id,
                alias_id=b_id,
            ),
        )
        assert "unmerge_note" in result
        assert result["unmerge_note"]

    def test_uuid_conversion(self) -> None:
        a_id = uuid4()
        b_id = uuid4()
        result = build_evidence_pack(
            person_a={"id": str(a_id)},
            person_b={"id": str(b_id)},
            match_probability=0.5,
            match_weight_bits=0.0,
            per_field_comparisons=[],
            source_provenance=[],
            what_changes=WhatChangesIfMerged(
                survivor_id=a_id,
                alias_id=b_id,
            ),
        )
        assert isinstance(result["candidate"]["person_a_id"], str)
        assert isinstance(result["candidate"]["person_b_id"], str)
        assert isinstance(result["what_changes_if_merged"]["survivor_id"], str)
        assert isinstance(result["what_changes_if_merged"]["alias_id"], str)
