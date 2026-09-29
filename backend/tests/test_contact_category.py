"""Category scope, write default, and cross-category guards that do not need Postgres.

Row-level collision and the migration backfill run in test_contact_category_db.py
against the real schema. This file does not mock that schema.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from contact_ops.agents.dedup.agent import _AUTO_MERGE_FLOOR, _apply_automerge_guardrail
from contact_ops.models.enums import ContactCategory
from contact_ops.services.contact_category import (
    READ_PERSONAL_SCOPE,
    CategoryScopeDenied,
    category_for_write,
    is_service_principal,
    resolve_category_filter,
    should_skip_alias_collapse,
    tag_marks_sourced_prospect,
)

_MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0053_contact_category.py"
)
_KEYCLOAK = "https://auth.example/realms/uchub"


@pytest.fixture
def human_issuers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "contact_ops.services.contact_category._human_issuers",
        lambda: {_KEYCLOAK, "standalone://contact-ops"},
    )


def test_human_sessions_see_every_category_unless_they_filter(human_issuers: None) -> None:
    human = {"iss": _KEYCLOAK, "sub": "owner", "scope": "person:read"}
    standalone = {"iss": "standalone://contact-ops", "sub": "owner"}
    internal = {"sub": "owner", "scope": "person:read"}  # tool tests carry no issuer
    for claims in (human, standalone, internal):
        assert is_service_principal(claims) is False
        assert resolve_category_filter(claims, None) is None
        assert resolve_category_filter(claims, "ALL") is None
        assert resolve_category_filter(claims, "PERSONAL") is ContactCategory.PERSONAL
        assert resolve_category_filter(claims, "PROSPECT") is ContactCategory.PROSPECT


def test_service_principals_default_to_prospect_only(human_issuers: None) -> None:
    principals = [
        {"iss": "contact-ops:personal-access-token", "pat_id": "pat-1", "scope": "person:read"},
        {"iss": _KEYCLOAK, "pat_id": "pat-2", "scope": "person:read"},
        {"iss": "https://brigade.example", "brigade_claims": {"sub": "customer-ops"}},
        {"iss": _KEYCLOAK, "sub": "owner", "act": {"sub": "agent-1"}},
        {"iss": _KEYCLOAK, "preferred_username": "service-account-customer-ops"},
        {"iss": "https://unknown.example", "sub": "federation"},
    ]
    for claims in principals:
        assert is_service_principal(claims) is True
        assert resolve_category_filter(claims, None) is ContactCategory.PROSPECT
        assert resolve_category_filter(claims, "ALL") is ContactCategory.PROSPECT
        assert resolve_category_filter(claims, "PROSPECT") is ContactCategory.PROSPECT


def test_actor_chain_and_agent_authority_are_service_principals(human_issuers: None) -> None:
    human_claims = {"iss": _KEYCLOAK, "sub": "owner", "scope": "person:read"}
    assert (
        is_service_principal(human_claims, actor_chain={"sub": "owner", "act": {"sub": "agent"}})
        is True
    )
    assert resolve_category_filter(human_claims, None, human_authority="agent") is (
        ContactCategory.PROSPECT
    )


def test_explicit_personal_requires_scope_for_service_principals(human_issuers: None) -> None:
    service = {
        "iss": "https://brigade.example",
        "brigade_claims": {"act": {"client_id": "customer-ops"}},
        "scope": "contacts:read contacts:write person:read person:write",
    }
    with pytest.raises(CategoryScopeDenied) as denied:
        resolve_category_filter(service, "PERSONAL")
    assert denied.value.scope == READ_PERSONAL_SCOPE

    granted = {**service, "scope": service["scope"] + " " + READ_PERSONAL_SCOPE}
    assert resolve_category_filter(granted, "PERSONAL") is ContactCategory.PERSONAL
    assert READ_PERSONAL_SCOPE not in service["scope"].split()


def test_write_category_defaults_by_principal_and_tag(human_issuers: None) -> None:
    human = {"iss": _KEYCLOAK, "sub": "owner"}
    service = {"brigade_claims": {"sub": "customer-ops"}, "scope": "person:write"}
    assert category_for_write(human, None, []) is ContactCategory.PERSONAL
    assert category_for_write(service, None, []) is ContactCategory.PROSPECT
    assert category_for_write(human, "PROSPECT", []) is ContactCategory.PROSPECT
    assert category_for_write(service, "PROSPECT", []) is ContactCategory.PROSPECT
    assert category_for_write(human, None, ["Sourced Prospect"]) is ContactCategory.PROSPECT
    assert tag_marks_sourced_prospect(["sourced-prospect"]) is True
    with pytest.raises(CategoryScopeDenied):
        category_for_write(service, "PERSONAL", [])
    granted = {**service, "scope": "person:write " + READ_PERSONAL_SCOPE}
    assert category_for_write(granted, "PERSONAL", []) is ContactCategory.PERSONAL


def test_cross_category_pair_cannot_auto_merge_either_direction() -> None:
    personal_vs_prospect = _apply_automerge_guardrail(
        1.0, ["email_exact"], categories_differ=True
    )
    prospect_vs_personal = _apply_automerge_guardrail(
        0.99, ["phone_exact"], categories_differ=True
    )
    same_category = _apply_automerge_guardrail(1.0, ["email_exact"], categories_differ=False)
    assert personal_vs_prospect[0] < _AUTO_MERGE_FLOOR
    assert personal_vs_prospect[1] != "auto_merge_eligible"
    assert prospect_vs_personal[0] < _AUTO_MERGE_FLOOR
    assert prospect_vs_personal[1] != "auto_merge_eligible"
    assert same_category == (1.0, "auto_merge_eligible")


def test_graph_alias_collapse_skips_cross_category_unless_a_human_merged() -> None:
    assert should_skip_alias_collapse(
        op_type="person_merge_applied",
        entity="person",
        human_authorized=False,
        category_a="PERSONAL",
        category_b="PROSPECT",
    )
    assert should_skip_alias_collapse(
        op_type="person_merge_applied",
        entity="person",
        human_authorized=False,
        category_a="PROSPECT",
        category_b="PERSONAL",
    )
    assert not should_skip_alias_collapse(
        op_type="person_merge_applied",
        entity="person",
        human_authorized=True,
        category_a="PERSONAL",
        category_b="PROSPECT",
    )
    assert not should_skip_alias_collapse(
        op_type="person_unmerge",
        entity="person",
        human_authorized=False,
        category_a="PERSONAL",
        category_b="PROSPECT",
    )
    assert not should_skip_alias_collapse(
        op_type="person_merge_applied",
        entity="person",
        human_authorized=False,
        category_a="PROSPECT",
        category_b="PROSPECT",
    )


def test_migration_commits_the_enum_before_using_it_and_backfill_is_reversible() -> None:
    source = _MIGRATION.read_text()
    upgrade = source.split("def downgrade", 1)[0]
    downgrade = source.split("def downgrade", 1)[1]
    add_column = "ADD COLUMN IF NOT EXISTS category"
    assert upgrade.index("autocommit_block") < upgrade.index("CREATE TYPE contact_category")
    assert upgrade.index("CREATE TYPE contact_category") < upgrade.index(add_column)
    assert upgrade.index(add_column) < upgrade.index("op.execute(BACKFILL_PROSPECT_SQL)")
    assert "IS DISTINCT FROM 'PROSPECT'" in source
    assert "sourced-prospect" in source
    assert "canonical_owner_tenant_id, category" in source
    assert downgrade.index("RESTORE_PERSONAL_FOR_SOURCED_PROSPECT_SQL") < downgrade.index(
        "DROP COLUMN"
    )
    assert downgrade.index("DROP COLUMN") < downgrade.index("DROP TYPE")
