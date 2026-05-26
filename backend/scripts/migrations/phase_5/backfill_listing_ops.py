"""Backfill Listing-Ops users into Contact-Ops.

Dry-run is the default verification path for Phase 5.1. Live mode uses the
Contact-Ops MCP surface for person/identifier writes, then records the
source-system mapping in ``listing_ops_link`` before any consumer read cutover.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from contact_ops.federation.consumer_sdk import ContactOpsConsumerClient


@dataclass(frozen=True, slots=True)
class ListingOpsUser:
    id: uuid.UUID
    sso_subject: str
    email: str
    display_name: str
    role: str
    is_active: bool


def _asyncpg_url(url: str) -> str:
    if url.startswith("postgresql+"):
        return url
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _load_users(source_db: str) -> list[ListingOpsUser]:
    engine = create_async_engine(_asyncpg_url(source_db), pool_pre_ping=True)
    try:
        async_session = async_sessionmaker(engine, expire_on_commit=False)
        async with async_session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, sso_subject, email, display_name, role, is_active
                    FROM users
                    ORDER BY created_at, id
                    """
                )
            )
            return [
                ListingOpsUser(
                    id=uuid.UUID(str(row["id"])),
                    sso_subject=str(row["sso_subject"]),
                    email=str(row["email"]),
                    display_name=str(row["display_name"]),
                    role=str(row["role"]),
                    is_active=bool(row["is_active"]),
                )
                for row in result.mappings().all()
            ]
    finally:
        await engine.dispose()


def summarize_users(users: list[ListingOpsUser]) -> dict[str, Any]:
    by_role: dict[str, int] = {}
    active = 0
    for user in users:
        by_role[user.role] = by_role.get(user.role, 0) + 1
        if user.is_active:
            active += 1
    return {
        "source": "listing-ops",
        "users_total": len(users),
        "users_active": active,
        "roles": dict(sorted(by_role.items())),
        "users": [
            {
                "id": str(user.id),
                "email": user.email,
                "display_name": user.display_name,
                "role": user.role,
                "is_active": user.is_active,
                "identifier": f"listing-ops:user:{user.id}",
            }
            for user in users
        ],
    }


async def _record_link(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_user_id: uuid.UUID,
    person_id: uuid.UUID,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO listing_ops_link (
                listing_ops_user_id,
                tenant_id,
                contact_ops_person_id,
                source_identifier,
                last_synced_at,
                sync_state
            )
            VALUES (
                :source_user_id,
                :tenant_id,
                :person_id,
                :source_identifier,
                now(),
                'active'
            )
            ON CONFLICT (listing_ops_user_id) DO UPDATE
            SET contact_ops_person_id = EXCLUDED.contact_ops_person_id,
                source_identifier = EXCLUDED.source_identifier,
                last_synced_at = now(),
                sync_state = 'active'
            """
        ),
        {
            "source_user_id": source_user_id,
            "tenant_id": tenant_id,
            "person_id": person_id,
            "source_identifier": f"listing-ops:user:{source_user_id}",
        },
    )


async def _apply(
    users: list[ListingOpsUser],
    *,
    contact_ops_db: str,
    tenant_id: uuid.UUID,
    subject_token: str,
    token_endpoint: str,
    client_id: str,
    client_secret: str,
    mcp_url: str,
) -> dict[str, Any]:
    engine = create_async_engine(_asyncpg_url(contact_ops_db), pool_pre_ping=True)
    client = ContactOpsConsumerClient(
        token_endpoint=token_endpoint,
        client_id=client_id,
        client_secret=client_secret,
        mcp_url=mcp_url,
        scope="contactops:contacts.read contactops:contacts.write",
    )
    created = 0
    linked = 0
    try:
        async_session = async_sessionmaker(engine, expire_on_commit=False)
        async with async_session() as session:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            for user in users:
                namespace = "listing-ops:user"
                value = str(user.id)
                existing = await client.find_person_by_identifier(
                    subject_token=subject_token,
                    namespace=namespace,
                    value=value,
                )
                if existing is None:
                    result = await client.call_tool(
                        subject_token=subject_token,
                        tool="create_person",
                        arguments={
                            "display_name": user.display_name,
                            "emails": [{"address": user.email, "type": "work", "is_primary": True}],
                            "initial_tags": ["listing-ops", f"listing-ops:{user.role}"],
                            "idempotency_key": str(
                                uuid.uuid5(uuid.NAMESPACE_URL, f"listing-ops:{user.id}")
                            ),
                            "confidence": 1.0,
                        },
                    )
                    person_id = uuid.UUID(str(result["person_id"]))
                    created += 1
                    await client.call_tool(
                        subject_token=subject_token,
                        tool="add_identifier",
                        arguments={
                            "subject_kind": "person",
                            "subject_id": str(person_id),
                            "namespace": namespace,
                            "value": value,
                            "verified": True,
                            "confidence": 1.0,
                        },
                    )
                else:
                    person_id = existing.person_id
                await _record_link(
                    session,
                    tenant_id=tenant_id,
                    source_user_id=user.id,
                    person_id=person_id,
                )
                linked += 1
            await session.commit()
    finally:
        await client.close()
        await engine.dispose()
    return {"created": created, "linked": linked}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-db", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--contact-ops-db", default=os.getenv("CONTACT_OPS_DATABASE_URL"))
    parser.add_argument("--tenant-id", default=os.getenv("CONTACT_OPS_TENANT_ID"))
    parser.add_argument("--subject-token", default=os.getenv("CONTACT_OPS_SUBJECT_TOKEN"))
    parser.add_argument(
        "--token-endpoint",
        default=os.getenv("CONTACT_OPS_TOKEN_ENDPOINT"),
    )
    parser.add_argument(
        "--client-id",
        default=os.getenv("CONTACT_OPS_CONSUMER_CLIENT_ID", "listing-ops-contactops-consumer"),
    )
    parser.add_argument("--client-secret", default=os.getenv("CONTACT_OPS_CONSUMER_CLIENT_SECRET"))
    parser.add_argument(
        "--mcp-url",
        default=os.getenv("CONTACT_OPS_MCP_URL", "https://mcp.contacts.magicunicorn.dev"),
    )
    return parser


async def _main() -> int:
    args = _parser().parse_args()
    users = await _load_users(str(args.source_db))
    summary = summarize_users(users)
    if args.dry_run:
        sys.stdout.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        return 0
    required = {
        "contact_ops_db": args.contact_ops_db,
        "tenant_id": args.tenant_id,
        "subject_token": args.subject_token,
        "token_endpoint": args.token_endpoint,
        "client_secret": args.client_secret,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        sys.stderr.write(f"missing required live-mode options: {', '.join(missing)}\n")
        return 2
    result = await _apply(
        users,
        contact_ops_db=str(args.contact_ops_db),
        tenant_id=uuid.UUID(str(args.tenant_id)),
        subject_token=str(args.subject_token),
        token_endpoint=str(args.token_endpoint),
        client_id=str(args.client_id),
        client_secret=str(args.client_secret),
        mcp_url=str(args.mcp_url),
    )
    summary["apply"] = result
    sys.stdout.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
