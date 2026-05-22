"""Tests for the Garage bucket naming + retention policy module."""

from __future__ import annotations

import pytest

from contact_ops.services.garage_lifecycle import (
    BUCKET_KINDS,
    all_bucket_names,
    bucket_name,
    retention_for_bucket,
)


def test_bucket_kinds_covers_design_doc_set() -> None:
    expected = {
        "photos",
        "voice-samples",
        "business-cards",
        "vcard-archive",
        "evidence-snapshots",
    }
    assert set(BUCKET_KINDS) == expected


def test_bucket_name_lowercases_and_normalizes_slug() -> None:
    assert bucket_name(kind="photos", tenant_slug="MagicUnicorn") == "contact-ops-photos-magicunicorn"
    # Underscores converted to hyphens
    assert (
        bucket_name(kind="voice-samples", tenant_slug="aaron_personal")
        == "contact-ops-voice-samples-aaron-personal"
    )
    # Empty slug falls back to default
    assert bucket_name(kind="photos", tenant_slug="").endswith("-default")


def test_bucket_name_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        bucket_name(kind="unknown-kind", tenant_slug="x")


def test_all_bucket_names_lists_full_set_for_tenant() -> None:
    names = all_bucket_names(tenant_slug="aaron")
    assert len(names) == len(BUCKET_KINDS)
    assert all(n.startswith("contact-ops-") for n in names)
    assert all(n.endswith("-aaron") for n in names)


def test_retention_for_vcard_archive_is_one_week() -> None:
    policy = retention_for_bucket(kind="vcard-archive", hipaa_mode=False)
    assert policy.expire_after_days == 7
    policy_hipaa = retention_for_bucket(kind="vcard-archive", hipaa_mode=True)
    assert policy_hipaa.expire_after_days == 7


def test_retention_for_evidence_is_90_days_non_hipaa_indefinite_hipaa() -> None:
    non_hipaa = retention_for_bucket(kind="evidence-snapshots", hipaa_mode=False)
    assert non_hipaa.expire_after_days == 90
    hipaa = retention_for_bucket(kind="evidence-snapshots", hipaa_mode=True)
    assert hipaa.expire_after_days is None


def test_retention_for_photos_is_indefinite_in_both_modes() -> None:
    for hipaa in (False, True):
        policy = retention_for_bucket(kind="photos", hipaa_mode=hipaa)
        assert policy.expire_after_days is None


def test_retention_for_business_cards_follows_evidence_rule() -> None:
    non_hipaa = retention_for_bucket(kind="business-cards", hipaa_mode=False)
    assert non_hipaa.expire_after_days == 90
    hipaa = retention_for_bucket(kind="business-cards", hipaa_mode=True)
    assert hipaa.expire_after_days is None
