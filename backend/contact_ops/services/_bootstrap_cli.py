"""CLI entry point for ``scripts/garage_bootstrap.sh``.

Reads tenant rows from Contact-Ops Postgres and provisions Garage
buckets for each, applying retention defaults per HIPAA mode. Safe to
re-run — buckets that already exist are detected via the admin API.

Usage::

    python -m contact_ops.services._bootstrap_cli [--tenant=SLUG] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from contact_ops.core.config import Settings, get_settings
from contact_ops.core.database import get_db_context
from contact_ops.models import Tenant
from contact_ops.services.garage_lifecycle import (
    all_bucket_names,
    provision_tenant_storage,
)

logger = structlog.get_logger(__name__)


def _admin_async_url(settings: Settings) -> str | None:
    """The migration/admin DSN, normalised to the asyncpg driver.

    Enumerating ALL tenants is a cross-tenant read; the runtime role is
    NOBYPASSRLS so with no GUC bound it sees ZERO tenants. The admin role
    (MIGRATION_DATABASE_URL) bypasses RLS — use it for the enumeration only.
    """
    raw = settings.MIGRATION_DATABASE_URL
    if not raw:
        return None
    scheme, _, rest = raw.partition("://")
    if not rest:
        return raw
    return f"postgresql+asyncpg://{rest}"


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.GARAGE_ADMIN_ENDPOINT and not args.dry_run:
        print(
            "GARAGE_ADMIN_ENDPOINT must be set (or pass --dry-run)",
            file=sys.stderr,
        )
        return 2

    stmt = select(Tenant.id, Tenant.slug, Tenant.hipaa_mode)
    if args.tenant:
        stmt = stmt.where(Tenant.slug == args.tenant)

    admin_url = _admin_async_url(settings)
    if admin_url:
        eng = create_async_engine(admin_url)
        try:
            async with eng.connect() as conn:
                rows = (await conn.execute(stmt)).all()
        finally:
            await eng.dispose()
    else:
        # No admin DSN — fall back to the runtime role. RLS will hide tenants
        # with no GUC bound, so this typically yields nothing; warn loudly.
        print(
            "WARNING: MIGRATION_DATABASE_URL unset — using the runtime role; "
            "RLS may hide all tenants. Set MIGRATION_DATABASE_URL to enumerate.",
            file=sys.stderr,
        )
        async with get_db_context() as session:
            rows = (await session.execute(stmt)).all()

    if not rows:
        print(f"no tenants matched; nothing to do (filter={args.tenant!r})")
        return 0

    for tenant_id, slug, hipaa_mode in rows:
        names = all_bucket_names(tenant_slug=slug)
        if args.dry_run:
            print(f"would provision for tenant {slug} (hipaa={hipaa_mode}):")
            for n in names:
                print(f"  - {n}")
            continue
        try:
            # provision_tenant_storage applies the dormant guards + grants the
            # shared access key. --force bypasses the GARAGE_AUTO_PROVISION switch
            # for a deliberate operator backfill (the flag gates AUTO-provision at
            # signup, not the manual repair path).
            created = await provision_tenant_storage(
                tenant_slug=slug, hipaa_mode=hipaa_mode, force=args.force
            )
            print(f"tenant={slug} provisioned: {len(created)} buckets")
        except Exception as exc:  # noqa: BLE001 — bootstrap, log + continue
            print(f"tenant={slug} failed: {exc}", file=sys.stderr)
            logger.exception("garage_bootstrap_tenant_failed", tenant=slug)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="garage_bootstrap",
        description="Provision Contact-Ops Garage buckets per tenant.",
    )
    parser.add_argument("--tenant", default=None, help="Provision only this slug")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List buckets that would be created without calling Garage",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Provision even when GARAGE_AUTO_PROVISION is off (manual backfill)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
