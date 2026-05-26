from __future__ import annotations

import uuid

from scripts.migrations.phase_5.backfill_listing_ops import ListingOpsUser, summarize_users


def test_listing_ops_dry_run_summary_counts_roles() -> None:
    users = [
        ListingOpsUser(
            id=uuid.UUID("00000000-0000-0000-0000-000000000401"),
            sso_subject="sub-aaron",
            email="REMOVED",
            display_name="Aaron Stransky",
            role="owner",
            is_active=True,
        ),
        ListingOpsUser(
            id=uuid.UUID("00000000-0000-0000-0000-000000000402"),
            sso_subject="sub-david",
            email="REMOVED",
            display_name="Daniel Tran",
            role="contributor",
            is_active=True,
        ),
    ]

    summary = summarize_users(users)

    assert summary["source"] == "listing-ops"
    assert summary["users_total"] == 2
    assert summary["users_active"] == 2
    assert summary["roles"] == {"contributor": 1, "owner": 1}
    assert summary["users"][1]["identifier"] == (
        "listing-ops:user:00000000-0000-0000-0000-000000000402"
    )

