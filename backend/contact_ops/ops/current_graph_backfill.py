"""Explicit, count- and owner-checked backfill of one tenant's current graph.

Default dry run. This queues source references, never caller-supplied graph data.
No global mode exists. Actual graph writes use the normal durable consumer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from contact_ops.agents.graph_sync_drain import _admin_async_url
from contact_ops.core.config import get_settings


async def queue_backfill(
    db, *, tenant_id, expected_owner, expected_people, expected_orgs, apply=False
):
    tenant = (
        (
            await db.execute(
                text("SELECT id,owner_user_id,parent_tenant_id FROM tenants WHERE id=:tid"),
                {"tid": tenant_id},
            )
        )
        .mappings()
        .first()
    )
    if (
        tenant is None
        or tenant["owner_user_id"] != expected_owner
        or tenant["parent_tenant_id"] is not None
    ):
        raise ValueError("standalone tenant owner does not match explicit backfill guard")
    people = (
        await db.scalars(
            text(
                "SELECT id FROM persons WHERE canonical_owner_tenant_id=:tid AND "
                "merge_status='canonical' ORDER BY id"
            ),
            {"tid": tenant_id},
        )
    ).all()
    orgs = (
        await db.scalars(
            text(
                "SELECT id FROM organizations WHERE "
                "canonical_owner_tenant_id=:tid AND merge_status='canonical' "
                "ORDER BY id"
            ),
            {"tid": tenant_id},
        )
    ).all()
    if len(people) != expected_people or len(orgs) != expected_orgs:
        raise ValueError("source counts differ from the reviewed tenant snapshot")
    if apply:
        for kind, ids in [("person", people), ("organization", orgs)]:
            for entity_id in ids:
                await db.execute(
                    text("SELECT enqueue_current_graph(:tid,:kind,:eid)"),
                    {"tid": tenant_id, "kind": kind, "eid": entity_id},
                )
    return {
        "tenant_id": str(tenant_id),
        "people": len(people),
        "organizations": len(orgs),
        "apply": apply,
    }


async def run(args):
    if args.apply and args.confirm_tenant != args.tenant_id:
        raise ValueError("apply requires --confirm-tenant matching --tenant-id")
    url = _admin_async_url(get_settings())
    if not url:
        raise ValueError("admin database DSN required")
    engine = create_async_engine(url, isolation_level="REPEATABLE READ")
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with sessions() as db:
            result = await queue_backfill(
                db,
                tenant_id=args.tenant_id,
                expected_owner=args.expected_owner,
                expected_people=args.expected_people,
                expected_orgs=args.expected_orgs,
                apply=args.apply,
            )
            if args.apply:
                await db.commit()
            else:
                await db.rollback()
            print(json.dumps(result))  # noqa: T201 -- explicit CLI count-only result  # noqa: T201 -- explicit CLI count-only result  # noqa: T201 -- explicit CLI count-only result
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=uuid.UUID, required=True)
    parser.add_argument("--expected-owner", type=uuid.UUID, required=True)
    parser.add_argument("--expected-people", type=int, required=True)
    parser.add_argument("--expected-orgs", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-tenant", type=uuid.UUID)
    args = parser.parse_args()
    if (
        args.expected_people < 0
        or args.expected_orgs < 0
        or args.expected_people + args.expected_orgs > 10000
    ):
        parser.error("backfill requires reviewed counts within the 10000-object cap")
    try:
        asyncio.run(run(args))
    except Exception as exc:
        # Credential-bearing database URLs and source rows never reach stdout.
        print(json.dumps({"failed": True, "error_type": type(exc).__name__}))  # noqa: T201 -- explicit CLI count-only result  # noqa: T201 -- explicit CLI count-only result  # noqa: T201 -- explicit CLI count-only result
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
