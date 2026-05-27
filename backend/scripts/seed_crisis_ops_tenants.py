"""Seed the Crisis-Ops case tenants + Aaron's ADMIN membership in Contact-Ops.

Phase 5.2 step 2. Creates the two case tenants — ``rocky-frb911`` (Rocky Doe's
FRB911 case) and ``shafen-loans`` (Shafen's LOANS case) — and a
``person_tenant_membership`` row attaching Aaron to each. Aaron's ADMIN authority
comes from his Keycloak realm role (``realm_access.roles``), not a column on the
membership table; the role intent is recorded in ``custom_attrs`` for clarity.

Rocky's / Shafen's CLIENT accounts (and their ``tenant_id`` Keycloak attribute)
are a separate, later, Aaron-gated step — NOT seeded here.

``--dry-run`` is the DEFAULT and writes nothing. ``--apply`` is gated and
idempotent (``ON CONFLICT DO NOTHING``). See
``~/Documents/Contact-Ops-Phase-5.2-Crisis-Ops-Revised-Design.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

#: Both case tenants are fixer/legal client workspaces.
TENANT_KIND = "white_label_customer"

CRISIS_OPS_TENANTS: list[dict[str, str]] = [
    {"slug": "rocky-frb911", "display_name": "Rocky Doe — FRB911"},
    {"slug": "shafen-loans", "display_name": "Shafen — LOANS"},
]


def _asyncpg_url(url: str) -> str:
    if url.startswith("postgresql+"):
        return url
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def build_seed_plan(*, admin_person_id: str | None, owner_user_id: str | None) -> dict[str, Any]:
    """Pure plan describing exactly what the seed would write (testable, no DB)."""
    tenants = [
        {
            "slug": tenant["slug"],
            "kind": TENANT_KIND,
            "display_name": tenant["display_name"],
            "owner_user_id": owner_user_id,
            "qdrant_namespace": f"contact_ops__{tenant['slug']}",
            "garage_bucket_prefix": f"contact-ops-{tenant['slug']}",
        }
        for tenant in CRISIS_OPS_TENANTS
    ]
    memberships = [
        {
            "tenant_slug": tenant["slug"],
            "person_id": admin_person_id,
            "role_intent": "ADMIN",
            "custom_attrs": {"federation_role": "ADMIN", "source": "crisis-ops"},
            "tags": ["crisis-ops", "admin"],
            "notes": (
                "Aaron — ADMIN across Crisis-Ops case tenants "
                "(authoritative role via Keycloak realm role)."
            ),
        }
        for tenant in CRISIS_OPS_TENANTS
    ]
    return {
        "source": "crisis-ops",
        "tenant_kind": TENANT_KIND,
        "tenants": tenants,
        "memberships": memberships,
        "note": (
            "Rocky/Shafen CLIENT accounts + tenant_id Keycloak attribute "
            "are a separate gated step."
        ),
    }


async def apply_seed(
    plan: dict[str, Any],
    *,
    contact_ops_db: str,
    admin_person_id: uuid.UUID,
    owner_user_id: uuid.UUID,
) -> dict[str, Any]:
    engine = create_async_engine(_asyncpg_url(contact_ops_db), pool_pre_ping=True)
    tenants_created = 0
    memberships_created = 0
    try:
        async_session = async_sessionmaker(engine, expire_on_commit=False)
        async with async_session() as session:
            for tenant in plan["tenants"]:
                result = await session.execute(
                    text(
                        """
                        INSERT INTO tenants (
                            slug, kind, display_name, owner_user_id,
                            qdrant_namespace, garage_bucket_prefix
                        )
                        VALUES (
                            :slug, CAST(:kind AS tenant_kind), :display_name, :owner_user_id,
                            :qdrant_namespace, :garage_bucket_prefix
                        )
                        ON CONFLICT (slug) DO NOTHING
                        RETURNING id
                        """
                    ),
                    {
                        "slug": tenant["slug"],
                        "kind": tenant["kind"],
                        "display_name": tenant["display_name"],
                        "owner_user_id": str(owner_user_id),
                        "qdrant_namespace": tenant["qdrant_namespace"],
                        "garage_bucket_prefix": tenant["garage_bucket_prefix"],
                    },
                )
                if result.first() is not None:
                    tenants_created += 1

                membership = await session.execute(
                    text(
                        """
                        INSERT INTO person_tenant_membership (
                            person_id, tenant_id, notes, tags, custom_attrs, added_by
                        )
                        SELECT
                            :person_id, t.id, :notes,
                            ARRAY['crisis-ops','admin']::text[],
                            CAST(:custom_attrs AS jsonb),
                            :added_by
                        FROM tenants t
                        WHERE t.slug = :slug
                        ON CONFLICT (person_id, tenant_id) DO NOTHING
                        RETURNING person_id
                        """
                    ),
                    {
                        "person_id": str(admin_person_id),
                        "slug": tenant["slug"],
                        "notes": plan["memberships"][0]["notes"],
                        "custom_attrs": json.dumps(
                            {"federation_role": "ADMIN", "source": "crisis-ops"}
                        ),
                        "added_by": str(admin_person_id),
                    },
                )
                if membership.first() is not None:
                    memberships_created += 1
            await session.commit()
    finally:
        await engine.dispose()
    return {"tenants_created": tenants_created, "memberships_created": memberships_created}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-person-id", help="Aaron's Contact-Ops person UUID")
    parser.add_argument("--owner-user-id", help="Aaron's user UUID (tenant owner)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="GATED: perform live writes. Default is dry-run (writes nothing).",
    )
    parser.add_argument("--contact-ops-db")
    return parser


async def _main() -> int:
    args = _parser().parse_args()
    plan = build_seed_plan(
        admin_person_id=args.admin_person_id,
        owner_user_id=args.owner_user_id,
    )

    if not args.apply:
        plan["mode"] = "dry-run"
        sys.stdout.write(json.dumps(plan, indent=2, sort_keys=True) + "\n")
        return 0

    required = {
        "contact_ops_db": args.contact_ops_db,
        "admin_person_id": args.admin_person_id,
        "owner_user_id": args.owner_user_id,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        sys.stderr.write(f"missing required --apply options: {', '.join(missing)}\n")
        return 2

    result = await apply_seed(
        plan,
        contact_ops_db=str(args.contact_ops_db),
        admin_person_id=uuid.UUID(str(args.admin_person_id)),
        owner_user_id=uuid.UUID(str(args.owner_user_id)),
    )
    plan["mode"] = "apply"
    plan["apply"] = result
    sys.stdout.write(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
