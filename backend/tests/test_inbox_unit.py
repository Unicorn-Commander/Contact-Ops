"""Unit tests for Phase 3.3a inbox MCP tools (no DB required).

Covers:
* All 9 tool registrations + RBAC + annotations
* Pure helper functions in inbox_query.py (cursor codec, bucket math,
  cluster id derivation, classification, fields_changed, derive_compliance)
* Pure helper functions in inbox_mutations.py (typed phrase validation,
  tier computation)
* Pydantic schema field-validation
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from contact_ops.mcp.registry import get_tool
from contact_ops.mcp.tools import register_all_tools
from contact_ops.schemas.inbox import (
    ApproveProposalInput,
    BulkApproveInput,
    ListPendingProposalsInput,
    SnoozeProposalInput,
)
from contact_ops.services.inbox_mutations import (
    _bulk_count_of,
    _compute_tier,
    _expected_phrase,
    _phrases_match,
)
from contact_ops.services.inbox_query import (
    _bucket_for,
    _bulk_count,
    _classify_action,
    _cluster_id_for,
    _decode_cursor,
    _derive_compliance,
    _encode_cursor,
    _fields_changed,
)

# Ensure the 9 inbox tools are loaded into the registry once.
register_all_tools()


# ---- registration / RBAC ----


INBOX_TOOLS = (
    "list_pending_proposals",
    "approve_proposal",
    "reject_proposal",
    "snooze_proposal",
    "bulk_approve",
    "bulk_reject",
    "get_proposal_evidence",
    "revert_auto_applied",
    "resolve_conflict_keep_both",
)


def test_nine_inbox_tools_registered():
    for name in INBOX_TOOLS:
        assert get_tool(name) is not None, f"{name} not registered"


def test_inbox_tools_require_staff_role():
    for name in INBOX_TOOLS:
        tool = get_tool(name)
        assert tool is not None
        assert tool.required_role == "STAFF", f"{name} should be STAFF-gated"
        assert "contactops:inbox.review" in tool.required_scopes


def test_list_pending_proposals_readonly():
    tool = get_tool("list_pending_proposals")
    assert tool is not None
    assert tool.annotations["readOnlyHint"] is True
    assert tool.annotations["destructiveHint"] is False


def test_get_proposal_evidence_readonly():
    tool = get_tool("get_proposal_evidence")
    assert tool is not None
    assert tool.annotations["readOnlyHint"] is True


def test_revert_auto_applied_marked_destructive():
    tool = get_tool("revert_auto_applied")
    assert tool is not None
    assert tool.annotations["destructiveHint"] is True


# ---- inbox_query helpers ----


def test_cursor_encode_decode_roundtrip():
    proposed_at = datetime(2026, 5, 22, 14, 30, 0, tzinfo=UTC)
    event_id = uuid.uuid4()
    cursor = _encode_cursor(proposed_at=proposed_at, event_id=event_id)
    out_t, out_e = _decode_cursor(cursor)
    assert out_t == proposed_at
    assert out_e == event_id


def test_decode_cursor_rejects_garbage():
    with pytest.raises(ValueError):
        _decode_cursor("not-base64!!!")


def test_bucket_for_truncates_to_midnight_utc():
    dt = datetime(2026, 5, 22, 17, 33, 45, tzinfo=UTC)
    bucket = _bucket_for(dt)
    assert bucket.hour == 0
    assert bucket.minute == 0
    assert bucket.second == 0


def test_bucket_for_naive_datetime_assumed_utc():
    naive = datetime(2026, 5, 22, 17, 0, 0)
    bucket = _bucket_for(naive)
    assert bucket.tzinfo is not None
    assert bucket.hour == 0


def test_cluster_id_is_deterministic():
    tenant = uuid.UUID("11111111-1111-1111-1111-111111111111")
    agg = uuid.UUID("22222222-2222-2222-2222-222222222222")
    bucket = datetime(2026, 5, 22, 0, 0, 0, tzinfo=UTC)
    a = _cluster_id_for(tenant_id=tenant, aggregate_id=agg, bucket=bucket)
    b = _cluster_id_for(tenant_id=tenant, aggregate_id=agg, bucket=bucket)
    assert a == b


def test_cluster_id_differs_across_tenants():
    agg = uuid.uuid4()
    bucket = datetime(2026, 5, 22, 0, 0, 0, tzinfo=UTC)
    a = _cluster_id_for(tenant_id=uuid.uuid4(), aggregate_id=agg, bucket=bucket)
    b = _cluster_id_for(tenant_id=uuid.uuid4(), aggregate_id=agg, bucket=bucket)
    assert a != b


def test_classify_action_dedup():
    out = _classify_action("dedup.propose_merge")
    assert out["is_dedup"] is True
    assert out["is_delete"] is False


def test_classify_action_relationship():
    out = _classify_action("relationship.propose_edge")
    assert out["is_edge"] is True


def test_classify_action_delete():
    for event_type in (
        "contact.delete", "tag.remove", "edge.remove",
        "person.delete", "organization.delete",
    ):
        out = _classify_action(event_type)
        assert out["is_delete"] is True, f"{event_type} should be classified as delete"


def test_fields_changed_symmetric_diff():
    before = {"display_name": "A", "title": "X"}
    after = {"display_name": "B", "title": "X", "email": "REMOVED"}
    # display_name differs, email added; title unchanged
    assert _fields_changed(before, after) == 2


def test_fields_changed_with_none_before():
    after = {"display_name": "B", "email": "REMOVED"}
    assert _fields_changed(None, after) == 2


def test_bulk_count_from_target_ids():
    payload = {"target_ids": [str(uuid.uuid4()) for _ in range(7)], "field": "tag"}
    assert _bulk_count(payload) == 7


def test_bulk_count_defaults_to_one():
    payload = {"display_name": "X"}
    assert _bulk_count(payload) == 1


def test_derive_compliance_hipaa_tenant_raises_min_exposure():
    out = _derive_compliance(tenant_hipaa_mode=True, tags=[])
    assert out.hipaa is True
    assert out.exposure == "low"  # default-bumped from "none" when HIPAA


def test_derive_compliance_legal_tag_high():
    out = _derive_compliance(tenant_hipaa_mode=False, tags=["legal"])
    assert out.exposure == "high"
    assert out.hipaa is False


def test_derive_compliance_financial_tag_medium():
    out = _derive_compliance(tenant_hipaa_mode=False, tags=["financial"])
    assert out.exposure == "medium"


def test_derive_compliance_no_tags():
    out = _derive_compliance(tenant_hipaa_mode=False, tags=[])
    assert out.exposure == "none"
    assert out.hipaa is False


# ---- inbox_mutations helpers ----


def test_phrases_match_case_insensitive():
    assert _phrases_match("Approve Hipaa", "approve hipaa") is True


def test_phrases_match_whitespace_trim():
    assert _phrases_match("  approve hipaa  ", "approve hipaa") is True


def test_phrases_match_strict_inner_whitespace_is_not_collapsed():
    # The spec is "case-insensitive + trim", not "collapse inner whitespace".
    # Inner double-space must NOT match a single-space expected.
    # Casefold compares after the trim, so "approve  hipaa" != "approve hipaa"
    # passes case-fold but the internal whitespace is preserved.
    # Actually: per the implementation, .strip().casefold() preserves
    # internal whitespace exactly. So this is a NO-MATCH.
    assert _phrases_match("approve  hipaa", "approve hipaa") is False


def test_phrases_match_none_expected_always_passes():
    assert _phrases_match(None, None) is True
    assert _phrases_match("anything", None) is True


def test_phrases_match_none_supplied_fails_when_required():
    assert _phrases_match(None, "approve hipaa") is False


def test_expected_phrase_delete_uses_entity_name():
    phrase = _expected_phrase(
        is_hipaa=False, is_delete=True, is_cross_tenant=False,
        bulk_count=1, entity_display_name="John Rector",
        source_tenant_slug=None, target_tenant_slug=None,
    )
    assert phrase == "John Rector"


def test_expected_phrase_hipaa_static():
    phrase = _expected_phrase(
        is_hipaa=True, is_delete=False, is_cross_tenant=False,
        bulk_count=1, entity_display_name=None,
        source_tenant_slug=None, target_tenant_slug=None,
    )
    assert phrase == "approve hipaa"


def test_expected_phrase_bulk_over_ten():
    phrase = _expected_phrase(
        is_hipaa=False, is_delete=False, is_cross_tenant=False,
        bulk_count=17, entity_display_name=None,
        source_tenant_slug=None, target_tenant_slug=None,
    )
    assert phrase == "approve 17 items"


def test_expected_phrase_cross_tenant_slugs():
    phrase = _expected_phrase(
        is_hipaa=False, is_delete=False, is_cross_tenant=True,
        bulk_count=1, entity_display_name=None,
        source_tenant_slug="aaron-personal",
        target_tenant_slug="magic-unicorn-llc",
    )
    assert phrase == "aaron-personal to magic-unicorn-llc"


def test_expected_phrase_none_when_no_gate():
    assert _expected_phrase(
        is_hipaa=False, is_delete=False, is_cross_tenant=False,
        bulk_count=1, entity_display_name=None,
        source_tenant_slug=None, target_tenant_slug=None,
    ) is None


# ---- _compute_tier (server-side tier ladder) ----


def _row(
    event_type: str = "tag.add",
    confidence: float = 0.85,
    tenant_hipaa_mode: bool = False,
    target_tenant_id: uuid.UUID | None = None,
    reversibility: str = "reversible",
    payload_after: dict | None = None,
    payload_before: dict | None = None,
) -> dict:
    return {
        "event_type": event_type,
        "confidence": confidence,
        "tenant_hipaa_mode": tenant_hipaa_mode,
        "target_tenant_id": str(target_tenant_id) if target_tenant_id else None,
        "reversibility_class": reversibility,
        "payload": payload_after or {},
        "decision_payload": {
            "payload_after": payload_after or {},
            "payload_before": payload_before,
        },
    }


def test_compute_tier_hipaa_forces_t4():
    assert _compute_tier(_row(tenant_hipaa_mode=True)) == 4


def test_compute_tier_delete_forces_t4():
    assert _compute_tier(_row(event_type="contact.delete")) == 4


def test_compute_tier_cross_tenant_forces_t4():
    assert _compute_tier(_row(target_tenant_id=uuid.uuid4())) == 4


def test_compute_tier_bulk_over_ten_forces_t4():
    big_targets = {"target_ids": [str(uuid.uuid4()) for _ in range(15)]}
    assert _compute_tier(_row(payload_after=big_targets)) == 4


def test_compute_tier_dedup_forces_t3():
    assert _compute_tier(_row(event_type="dedup.propose_merge", confidence=0.96)) == 3


def test_compute_tier_three_field_change_forces_t3():
    after = {"f1": 1, "f2": 2, "f3": 3, "f4": 4}
    assert _compute_tier(_row(payload_after=after, confidence=0.96)) == 3


def test_compute_tier_low_confidence_t2():
    assert _compute_tier(_row(confidence=0.70)) == 2


def test_compute_tier_irreversible_t2():
    assert _compute_tier(_row(confidence=0.99, reversibility="irreversible")) == 2


def test_compute_tier_high_confidence_default_t1():
    assert _compute_tier(_row(confidence=0.99)) == 1


def test_bulk_count_of_helper():
    row = _row(payload_after={"target_ids": [str(uuid.uuid4()) for _ in range(5)]})
    assert _bulk_count_of(row) == 5
    assert _bulk_count_of(_row()) == 1


# ---- schema field validation ----


def test_list_input_rejects_extra_fields():
    with pytest.raises(ValueError):
        ListPendingProposalsInput(unexpected_arg=True)  # type: ignore[call-arg]


def test_approve_input_requires_proposal_id_and_tier():
    with pytest.raises(ValueError):
        ApproveProposalInput(tier_assigned=1)  # type: ignore[call-arg]


def test_approve_input_accepts_field_choices():
    payload = ApproveProposalInput(
        proposal_id=uuid.uuid4(),
        tier_assigned=3,
        field_choices={"title": "proposed", "email": "master"},
    )
    assert payload.field_choices == {"title": "proposed", "email": "master"}


def test_bulk_approve_input_rejects_empty_list():
    with pytest.raises(ValueError):
        BulkApproveInput(proposal_ids=[])


def test_bulk_approve_input_rejects_over_100():
    with pytest.raises(ValueError):
        BulkApproveInput(proposal_ids=[uuid.uuid4() for _ in range(101)])


def test_snooze_input_required_fields():
    payload = SnoozeProposalInput(
        proposal_id=uuid.uuid4(),
        snooze_until=datetime.now(UTC) + timedelta(hours=2),
    )
    assert payload.snooze_reason == "custom"
