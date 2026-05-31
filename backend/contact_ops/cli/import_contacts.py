"""CLI for Contact-Ops contact imports."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from typing import NoReturn

from contact_ops.importers.base import Importer, ProvenanceContext, SourceKind
from contact_ops.importers.google_csv import GoogleCSVImporter
from contact_ops.importers.icloud_carddav import ICloudCardDAVImporter
from contact_ops.importers.linkedin_csv import LinkedInCSVImporter
from contact_ops.importers.nextcloud_carddav import NextcloudCardDAVImporter
from contact_ops.importers.vcard import VCardImporter


def main() -> NoReturn:
    raise SystemExit(asyncio.run(_main()))


async def _main() -> int:
    parser = _parser()
    args = parser.parse_args()
    source = args.source
    importer = _importer(source, args)
    records = await importer.records()
    _out(f"Parsed {len(records)} contacts from {importer.source_uri}")
    if args.dry_run or not args.apply:
        for index, record in enumerate(records[: args.preview_limit], start=1):
            _out(
                f"{index}. {record.display_name} "
                f"emails={len(record.emails)} phones={len(record.phones)} tags={record.tags}"
            )
        if len(records) > args.preview_limit:
            _out(f"... {len(records) - args.preview_limit} more")
        return 0

    if not os.environ.get("KC_PUBLISHER_CLIENT_SECRET"):
        _out("KC_PUBLISHER_CLIENT_SECRET is required for apply mode")
        return 2
    tenant_id = await _tenant_id(args.tenant)

    from contact_ops.core.config import get_settings
    from contact_ops.core.database import (
        async_session_maker,
        audit_session_maker,
        bind_session_context,
    )
    from contact_ops.importers.mcp_writer import MCPImporterWriter
    from contact_ops.mcp.registry import MCPContext

    settings = get_settings()
    # Pure CLI path — no human uc_uid in scope. Bind a stable service identity
    # so audit attribution is never null (matches the actor_chain below).
    uc_uid = "service:import-cli"
    async with async_session_maker() as db, audit_session_maker() as audit_db:
        await bind_session_context(db, str(tenant_id), uc_uid, settings)
        await bind_session_context(audit_db, str(tenant_id), uc_uid, settings)
        ctx = MCPContext(
            tenant_id=tenant_id,
            user_id=str(tenant_id),
            actor_chain={"act": "contact-ops-import-cli"},
            human_authority=str(tenant_id),
            db=db,
            audit_db=audit_db,
            request_id=f"import-cli:{uuid.uuid4()}",
            claims={
                "realm_access": {"roles": ["STAFF"]},
                "scope": (
                    "person:read person:write person:bulk email:write phone:write "
                    "address:write identifier:write tag:write org:write"
                ),
            },
        )
        writer = MCPImporterWriter(
            ctx=ctx,
            provenance=ProvenanceContext(
                source_kind=source,
                source_uri=importer.source_uri,
                tenant_id=tenant_id,
            ),
            dry_run=False,
        )
        result = await writer.write_records(records)
        await db.commit()
        await audit_db.commit()
    _out(json.dumps({"stats": result.stats.__dict__, "errors": result.errors}, indent=2))
    return 0 if result.stats.errors == 0 else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="contact-ops import-contacts")
    parser.add_argument(
        "--source",
        required=True,
        choices=["vcard", "google_csv", "linkedin_csv", "nextcloud", "icloud"],
    )
    parser.add_argument("--file", dest="path")
    parser.add_argument("--url")
    parser.add_argument("--tenant", required=True, help="Tenant slug, e.g. aaron-personal")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Parse + show would-create, don't write",
    )
    parser.add_argument("--apply", action="store_true", help="Actually write records")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--resume")
    parser.add_argument("--preview-limit", type=int, default=20)
    return parser


def _importer(source: SourceKind, args: argparse.Namespace) -> Importer:
    if source == "vcard":
        _require_file(args.path)
        return VCardImporter(path=args.path, batch_size=args.batch_size)
    if source == "google_csv":
        _require_file(args.path)
        return GoogleCSVImporter(path=args.path, batch_size=args.batch_size)
    if source == "linkedin_csv":
        _require_file(args.path)
        return LinkedInCSVImporter(path=args.path, batch_size=args.batch_size)
    if source == "nextcloud":
        return NextcloudCardDAVImporter(url=args.url, batch_size=args.batch_size)
    return ICloudCardDAVImporter(url=args.url, batch_size=args.batch_size)


def _require_file(path: str | None) -> None:
    if not path:
        raise SystemExit("--file is required for file-based sources")


async def _tenant_id(slug: str) -> uuid.UUID:
    from sqlalchemy import select

    from contact_ops.core.database import async_session_maker
    from contact_ops.models import Tenant

    async with async_session_maker() as db:
        tenant_id = await db.scalar(select(Tenant.id).where(Tenant.slug == slug))
        if tenant_id is None:
            raise SystemExit(f"unknown tenant slug: {slug}")
        return tenant_id


def _out(message: str) -> None:
    sys.stdout.write(f"{message}\n")


if __name__ == "__main__":
    main()
