"""Fixture-based unit tests for the Crisis-Ops backfill mapping.

Proves the confidentiality contract with no database: only opted-in
people/organizations cross over, only their identity fields, only identity
relationships among linked entities, every edge propose-only, and everything
else (other entity types, case-specific data, allegation edges) excluded.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest

from scripts.migrations.phase_5 import backfill_crisis_ops as backfill
from scripts.migrations.phase_5.backfill_crisis_ops import (
    BackfillPlan,
    CrisisOpsEntity,
    CrisisOpsRelationship,
    build_plan,
    extract_identity,
    is_identity_relationship,
    load_fixture,
    summarize_plan,
)

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000005a1")
CASE = "rocky-frb911"


def _entities() -> list[CrisisOpsEntity]:
    return [
        # Opted-in person with identity + a pile of case-specific data that must NOT leak.
        CrisisOpsEntity(
            case_slug=CASE,
            entity_key="rocky",
            entity_type="people",
            name="Rocky Doe",
            data={
                "email": "REMOVED",
                "phone": "REMOVED",
                "allegation": "misappropriation of FRB911 donations",
                "arrest_date": "2024-03-02",
                "aaron_first_hand_knowledge": "saw the bank statements",
                "financials": {"amount_misused": 412000},
            },
        ),
        # Opted-in spouse (person), linked via explicit opt-in key.
        CrisisOpsEntity(
            case_slug=CASE,
            entity_key="jane",
            entity_type="people",
            name="Jane Doe",
            data={"email": "REMOVED"},
        ),
        # Opted-in via in-row flag rather than the opt-in set.
        CrisisOpsEntity(
            case_slug=CASE,
            entity_key="defense-counsel",
            entity_type="people",
            name="Allie Menegakis",
            data={"email": "REMOVED", "contact_ops_optin": True},
        ),
        # Opted-in organization.
        CrisisOpsEntity(
            case_slug=CASE,
            entity_key="harbor-law",
            entity_type="organizations",
            name="Harbor Law Group",
            data={"domain": "clekislaw.test", "legal_name": "Harbor Law, LLC"},
        ),
        # People entity NOT opted in -> stays local.
        CrisisOpsEntity(
            case_slug=CASE,
            entity_key="confidential-source",
            entity_type="people",
            name="Confidential Source",
            data={"email": "REMOVED"},
        ),
        # Non-contact entity types -> never link.
        CrisisOpsEntity(case_slug=CASE, entity_key="ev1", entity_type="evidence", name="Stmt"),
        CrisisOpsEntity(case_slug=CASE, entity_key="ev2", entity_type="events", name="Mtg"),
        CrisisOpsEntity(case_slug=CASE, entity_key="ev3", entity_type="legal", name="Statute"),
        CrisisOpsEntity(case_slug=CASE, entity_key="ev4", entity_type="communication", name="C"),
        CrisisOpsEntity(case_slug=CASE, entity_key="ev5", entity_type="locations", name="Beach"),
    ]


def _relationships() -> list[CrisisOpsRelationship]:
    return [
        # Identity edges among linked entities -> proposed.
        CrisisOpsRelationship(CASE, "rocky", "jane", "SPOUSE_OF"),
        CrisisOpsRelationship(CASE, "defense-counsel", "harbor-law", "WORKS_FOR"),
        CrisisOpsRelationship(CASE, "defense-counsel", "rocky", "DEFENSE_COUNSEL_IN"),
        # Allegation/investigation edges -> never leave the case.
        CrisisOpsRelationship(CASE, "rocky", "jane", "CONSPIRED_WITH"),
        CrisisOpsRelationship(CASE, "rocky", "ev1", "PROVES"),
        CrisisOpsRelationship(CASE, "rocky", "confidential-source", "RETALIATED_AGAINST"),
        # Identity edge but an endpoint is not linked (confidential-source) -> excluded.
        CrisisOpsRelationship(CASE, "rocky", "confidential-source", "SPOUSE_OF"),
    ]


def _plan() -> BackfillPlan:
    opt_in = frozenset({f"{CASE}:rocky", f"{CASE}:jane", f"{CASE}:harbor-law"})
    return build_plan(_entities(), _relationships(), tenant_id=TENANT, opt_in=opt_in)


def test_only_opted_in_people_and_orgs_are_linked() -> None:
    plan = _plan()
    linked = {link.entity_key for link in plan.links}
    assert linked == {"rocky", "jane", "defense-counsel", "harbor-law"}
    assert all(link.target_kind in {"person", "org"} for link in plan.links)
    assert {link.entity_key for link in plan.links if link.target_kind == "org"} == {"harbor-law"}


def test_non_contact_types_and_unopted_excluded() -> None:
    plan = _plan()
    assert plan.excluded_entities["not_opted_in"] == 1  # confidential-source
    for etype in ("evidence", "events", "legal", "communication", "locations"):
        assert plan.excluded_entities[f"non_contact_type:{etype}"] == 1


def test_identity_extraction_drops_case_specific_keys() -> None:
    rocky = next(link for link in _plan().links if link.entity_key == "rocky")
    assert set(rocky.identity.keys()) == {"email", "phone"}
    assert "allegation" not in rocky.identity
    assert "arrest_date" not in rocky.identity
    assert "aaron_first_hand_knowledge" not in rocky.identity
    assert "financials" not in rocky.identity


def test_extract_identity_unit() -> None:
    identity = extract_identity(
        {
            "email": "REMOVED",
            "mobile": "+1-000",
            "domain": "y.test",
            "allegation": "nope",
            "arrest_warrant": "nope",
            "aaron_note": "nope",
            "amount_misused": 1,
            "random_case_key": "nope",
        }
    )
    assert identity == {"email": "REMOVED", "mobile": "+1-000", "domain": "y.test"}


def test_proposed_edges_are_identity_only_and_propose_only() -> None:
    plan = _plan()
    proposed = {
        (e.source_key, e.target_key, e.crisis_relationship_type) for e in plan.proposed_edges
    }
    assert ("rocky", "jane", "SPOUSE_OF") in proposed
    assert ("defense-counsel", "harbor-law", "WORKS_FOR") in proposed
    assert ("defense-counsel", "rocky", "DEFENSE_COUNSEL_IN") in proposed
    assert all(edge.propose_only for edge in plan.proposed_edges)
    # No allegation/investigation edge ever appears.
    leaked = {e.crisis_relationship_type for e in plan.proposed_edges}
    assert leaked.isdisjoint({"CONSPIRED_WITH", "PROVES", "RETALIATED_AGAINST", "VALIDATED"})


def test_allegation_and_unlinked_edges_excluded() -> None:
    plan = _plan()
    assert plan.excluded_relationships["forbidden_edge:CONSPIRED_WITH"] == 1
    assert plan.excluded_relationships["forbidden_edge:PROVES"] == 1
    assert plan.excluded_relationships["forbidden_edge:RETALIATED_AGAINST"] == 1
    # SPOUSE_OF rocky->confidential-source: identity type, but endpoint not linked.
    assert plan.excluded_relationships["endpoint_not_linked"] == 1


def test_summary_invariants() -> None:
    summary = summarize_plan(_plan())
    assert summary["source"] == "crisis-ops"
    assert summary["links_total"] == 4
    assert summary["links_people"] == 3
    assert summary["links_orgs"] == 1
    assert summary["proposed_edges_total"] == 3
    assert summary["proposed_edges_all_propose_only"] is True
    assert all(edge["applied"] is False for edge in summary["proposed_edges"])
    for link in summary["links"]:
        assert link["link_state"] == "proposed"


def test_counsel_relation_detection() -> None:
    assert is_identity_relationship("DEFENSE_COUNSEL_IN")
    assert is_identity_relationship("PLAINTIFF_COUNSEL_FOR")
    assert is_identity_relationship("SPOUSE_OF")
    assert not is_identity_relationship("CONSPIRED_WITH")
    assert not is_identity_relationship("DISCRIMINATED_AGAINST")


def test_default_local_except_in_row_optin_flag() -> None:
    # With an empty opt-in set, only the entity carrying the in-row
    # ``contact_ops_optin`` flag links; everyone else stays local by default.
    plan = build_plan(_entities(), _relationships(), tenant_id=TENANT, opt_in=frozenset())
    assert {link.entity_key for link in plan.links} == {"defense-counsel"}
    # No edge survives because its endpoints (rocky/jane/harbor-law) aren't linked.
    assert plan.proposed_edges == []


def test_cli_dry_run_against_fixture_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = {
        "entities": [
            {
                "case_slug": CASE,
                "entity_key": "rocky",
                "entity_type": "people",
                "name": "Rocky Doe",
                "data": {"email": "REMOVED", "allegation": "x"},
            },
            {"case_slug": CASE, "entity_key": "ev1", "entity_type": "evidence", "name": "stmt"},
        ],
        "relationships": [
            {
                "case_slug": CASE,
                "source_key": "rocky",
                "target_key": "ev1",
                "relationship_type": "PROVES",
            },
        ],
    }
    fixture_path = tmp_path / "crisis_fixture.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    # Round-trips through the public loader.
    entities, relationships = load_fixture(str(fixture_path))
    assert len(entities) == 2
    assert len(relationships) == 1

    # The CLI dry-run path: no DB args supplied and no --apply; must succeed and
    # print a plan. The fixture branch never opens a database connection.
    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill_crisis_ops",
            "--fixture",
            str(fixture_path),
            "--tenant-id",
            str(TENANT),
            "--opt-in",
            f"{CASE}:rocky",
        ],
    )
    exit_code = asyncio.run(backfill._main())
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "dry-run"
    assert out["links_total"] == 1
    assert out["links"][0]["identity_keys"] == ["email"]
    assert out["excluded_entities"] == {"non_contact_type:evidence": 1}
