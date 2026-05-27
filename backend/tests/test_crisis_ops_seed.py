"""Unit tests for the Crisis-Ops tenant seed plan (no database)."""

from __future__ import annotations

import asyncio
import json

import pytest

from scripts import seed_crisis_ops_tenants as seed

ADMIN_PERSON = "00000000-0000-0000-0000-0000000000a1"
OWNER_USER = "00000000-0000-0000-0000-0000000000b1"


def test_seed_plan_creates_both_case_tenants() -> None:
    plan = seed.build_seed_plan(admin_person_id=ADMIN_PERSON, owner_user_id=OWNER_USER)
    slugs = {tenant["slug"] for tenant in plan["tenants"]}
    assert slugs == {"rocky-frb911", "shafen-loans"}
    for tenant in plan["tenants"]:
        assert tenant["kind"] == "white_label_customer"
        assert tenant["owner_user_id"] == OWNER_USER
        assert tenant["qdrant_namespace"] == f"contact_ops__{tenant['slug']}"
        assert tenant["garage_bucket_prefix"] == f"contact-ops-{tenant['slug']}"


def test_seed_plan_attaches_admin_membership_to_both() -> None:
    plan = seed.build_seed_plan(admin_person_id=ADMIN_PERSON, owner_user_id=OWNER_USER)
    assert {m["tenant_slug"] for m in plan["memberships"]} == {"rocky-frb911", "shafen-loans"}
    for membership in plan["memberships"]:
        assert membership["person_id"] == ADMIN_PERSON
        assert membership["role_intent"] == "ADMIN"
        assert membership["custom_attrs"]["federation_role"] == "ADMIN"
        assert "admin" in membership["tags"]


def test_seed_plan_excludes_client_accounts() -> None:
    # Rocky/Shafen CLIENT accounts are a separate gated step — never in this plan.
    plan = seed.build_seed_plan(admin_person_id=ADMIN_PERSON, owner_user_id=OWNER_USER)
    blob = json.dumps(plan).lower()
    assert "client" not in {m["role_intent"].lower() for m in plan["memberships"]}
    assert "separate gated step" in plan["note"]
    assert "rocky-frb911" in blob and "shafen-loans" in blob


def test_seed_cli_dry_run_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "seed_crisis_ops_tenants",
            "--admin-person-id",
            ADMIN_PERSON,
            "--owner-user-id",
            OWNER_USER,
        ],
    )
    exit_code = asyncio.run(seed._main())
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "dry-run"
    assert len(out["tenants"]) == 2
    assert len(out["memberships"]) == 2
