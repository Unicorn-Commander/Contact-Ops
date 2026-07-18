"""Operator CLI for tenant plan tiers.

Usage:

    python -m contact_ops.billing.plan_cli show --tenant <uuid>
    python -m contact_ops.billing.plan_cli set-plan-tier \\
        --tenant <uuid> --tier pro --actor aaron --reason "manual upgrade"

This is the SRE/operator surface for moving a tenant between billing tiers by
hand (the Lago webhook is the automated payment-driven path). Every ``set``
writes ``tenants.plan_tier`` under the admin (BYPASSRLS) DSN and records an
append-only ``action_event`` — see :mod:`contact_ops.billing.plan_admin`.

It is deliberately a CLI, not a tenant-JWT-reachable MCP tool: a plan change is
an operator / billing-substrate decision and must never be initiated by a
request carrying a tenant's own token (which, under the ``tenants_modify`` RLS
policy, could otherwise self-upgrade). Reaching this CLI already means holding
the admin DSN.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from contact_ops.billing.plan_admin import (
    VALID_TIERS,
    PlanTierError,
    _admin_async_url,
    set_tenant_plan_tier,
)
from contact_ops.core.config import get_settings

logger = structlog.get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m contact_ops.billing.plan_cli",
        description="Contact-Ops tenant plan-tier operator CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_show = sub.add_parser("show", help="Show a tenant's current plan tier")
    p_show.add_argument("--tenant", required=True)

    p_set = sub.add_parser(
        "set-plan-tier", help="Set a tenant's plan tier (audited, admin DSN)"
    )
    p_set.add_argument("--tenant", required=True)
    p_set.add_argument("--tier", required=True, choices=sorted(VALID_TIERS))
    p_set.add_argument(
        "--actor",
        required=True,
        help="operator identity recorded on the audit event (e.g. your username)",
    )
    p_set.add_argument(
        "--reason", required=True, help="why this change is being made (audited)"
    )

    args = parser.parse_args(argv)
    dispatch = {"show": _cmd_show, "set-plan-tier": _cmd_set}
    return int(asyncio.run(dispatch[args.command](args)))


async def _cmd_show(args: argparse.Namespace) -> int:
    settings = get_settings()
    admin_url = _admin_async_url(settings)
    if not admin_url:
        print("ERROR: MIGRATION_DATABASE_URL unset — cannot read tenant plan tier.")
        return 2
    try:
        tenant_id = UUID(args.tenant)
    except ValueError:
        print(f"ERROR: --tenant is not a valid uuid: {args.tenant}")
        return 2

    engine = create_async_engine(admin_url)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with maker() as session:
            tier = await session.scalar(
                text("SELECT plan_tier FROM tenants WHERE id = CAST(:t AS uuid)"),
                {"t": str(tenant_id)},
            )
    finally:
        await engine.dispose()

    if tier is None:
        print(f"(no tenant {tenant_id})")
        return 1
    print(f"{tenant_id}: {tier}")
    return 0


async def _cmd_set(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        tenant_id = UUID(args.tenant)
    except ValueError:
        print(f"ERROR: --tenant is not a valid uuid: {args.tenant}")
        return 2

    try:
        change = await set_tenant_plan_tier(
            tenant_id=tenant_id,
            new_tier=args.tier,
            settings=settings,
            actor=args.actor,
            reason=args.reason,
            source="operator_cli",
        )
    except PlanTierError as exc:
        print(f"ERROR: {exc}")
        return 2

    if not change.changed:
        print(f"{tenant_id}: already on {change.after} (no change, no event)")
        return 0
    print(
        f"{tenant_id}: {change.before} -> {change.after} "
        f"(event {change.event_id})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
