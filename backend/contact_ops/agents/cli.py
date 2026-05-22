"""Admin CLI for the agent fleet.

Usage:

    python -m contact_ops.agents.cli list
    python -m contact_ops.agents.cli trust --agent dedup --tenant <uuid> --visibility private
    python -m contact_ops.agents.cli promote --agent dedup --tenant <uuid> --visibility private
    python -m contact_ops.agents.cli demote --agent dedup --tenant <uuid> --visibility private
    python -m contact_ops.agents.cli dlq --limit 50
    python -m contact_ops.agents.cli replay <dlq-id> [<dlq-id> ...]
    python -m contact_ops.agents.cli pause --agent dedup --reason "investigating drift"
    python -m contact_ops.agents.cli resume --agent dedup --reason "fixed"

This is the ops surface the SREs reach for. The 7 MCP admin tools
(``mcp/tools/agent_admin.py``) expose the same operations to authorized
ADMIN-role JWT holders.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from contact_ops.agents.circuit_breaker import CircuitBreaker
from contact_ops.agents.dlq import DeadLetterQueue, ErrorClass
from contact_ops.agents.registry import list_agents
from contact_ops.agents.trust import (
    BetaPosterior,
    TrustTier,
    tier_from_posterior,
    tier_name,
)
from contact_ops.core.config import get_settings

logger = structlog.get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m contact_ops.agents.cli",
        description="Contact-Ops agent fleet admin CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List registered agents")

    p_trust = sub.add_parser("trust", help="Show current trust + Beta posterior")
    p_trust.add_argument("--agent", required=True)
    p_trust.add_argument("--tenant", required=True)
    p_trust.add_argument("--visibility", default="private")

    p_promote = sub.add_parser("promote", help="Promote agent to next tier")
    p_promote.add_argument("--agent", required=True)
    p_promote.add_argument("--tenant", required=True)
    p_promote.add_argument("--visibility", default="private")

    p_demote = sub.add_parser("demote", help="Demote agent by one tier")
    p_demote.add_argument("--agent", required=True)
    p_demote.add_argument("--tenant", required=True)
    p_demote.add_argument("--visibility", default="private")

    p_dlq = sub.add_parser("dlq", help="List DLQ entries")
    p_dlq.add_argument("--limit", type=int, default=50)
    p_dlq.add_argument("--error-class", default=None)
    p_dlq.add_argument("--tenant", default=None)

    p_replay = sub.add_parser("replay", help="Bulk-replay DLQ entries")
    p_replay.add_argument("dlq_ids", nargs="+")

    p_pause = sub.add_parser("pause", help="Pause agent (open per-agent breaker)")
    p_pause.add_argument("--agent", required=True)
    p_pause.add_argument("--reason", required=True)

    p_resume = sub.add_parser("resume", help="Resume agent (close per-agent breaker)")
    p_resume.add_argument("--agent", required=True)
    p_resume.add_argument("--reason", required=True)

    p_simulate = sub.add_parser(
        "simulate-trust",
        help="Show the tier mapping for synthetic Beta(alpha, beta)",
    )
    p_simulate.add_argument("--alpha", type=float, required=True)
    p_simulate.add_argument("--beta", type=float, required=True)

    args = parser.parse_args(argv)
    dispatch: dict[
        str,
        Callable[[argparse.Namespace], Awaitable[int] | int],
    ] = {
        "list": _cmd_list,
        "trust": _cmd_trust,
        "promote": _cmd_promote,
        "demote": _cmd_demote,
        "dlq": _cmd_dlq,
        "replay": _cmd_replay,
        "pause": _cmd_pause,
        "resume": _cmd_resume,
        "simulate-trust": _cmd_simulate,
    }
    result = dispatch[args.command](args)
    if isinstance(result, int):
        return result
    # ``result`` is a coroutine returned by an async dispatch entry.
    coro: Any = result
    return int(asyncio.run(coro))


def _cmd_list(_: argparse.Namespace) -> int:
    rows = list_agents()
    if not rows:
        print("(no agents registered in this process)")
        return 0
    print(f"{'SLUG':<24} {'VERSION':<10} {'CLASS':<10} {'TIER':<14} BUDGET")
    for a in rows:
        print(
            f"{a.slug:<24} {a.version:<10} {a.agent_class.value:<10} "
            f"{tier_name(a.initial_trust_tier):<14} "
            f"${a.cost_budget_monthly_cents/100:.2f}/mo"
        )
    return 0


def _cmd_simulate(args: argparse.Namespace) -> int:
    p = BetaPosterior(alpha=args.alpha, beta=args.beta)
    tier = tier_from_posterior(p)
    print(f"Beta(alpha={p.alpha}, beta={p.beta})")
    print(f"  mean: {p.mean:.4f}")
    print(f"  total_outcomes: {p.total_outcomes}")
    print(f"  lower_ci_90: {p.lower_credible_interval(0.90):.4f}")
    print(f"  lower_ci_95: {p.lower_credible_interval(0.95):.4f}")
    print(f"  -> {tier_name(tier)}")
    return 0


async def _cmd_trust(args: argparse.Namespace) -> int:
    db = await _open_db()
    try:
        async with db() as session:
            from sqlalchemy import text

            result = await session.execute(
                text(
                    """
                    SELECT alpha, beta, current_tier, drift_status,
                           samples_total, samples_7d, samples_30d
                    FROM agent_trust
                    WHERE agent_slug = :slug
                      AND tenant_id = CAST(:tenant AS uuid)
                      AND visibility = :v
                    """
                ),
                {"slug": args.agent, "tenant": args.tenant, "v": args.visibility},
            )
            row = result.mappings().first()
            if row is None:
                print(f"(no trust row for {args.agent} / {args.tenant} / {args.visibility})")
                return 1
            posterior = BetaPosterior(
                alpha=float(row["alpha"]), beta=float(row["beta"])
            )
            implied = tier_from_posterior(posterior)
            print(f"agent: {args.agent}  tenant: {args.tenant}  visibility: {args.visibility}")
            print(f"  Beta(alpha={posterior.alpha}, beta={posterior.beta})")
            print(f"  mean: {posterior.mean:.4f}")
            print(f"  lower_ci_95: {posterior.lower_credible_interval(0.95):.4f}")
            print(f"  posterior-implied tier: {tier_name(implied)}")
            print(f"  stored tier: {tier_name(TrustTier(int(row['current_tier'])))}")
            print(f"  drift: {row['drift_status']}")
            print(
                f"  samples: total={row['samples_total']}, "
                f"7d={row['samples_7d']}, 30d={row['samples_30d']}"
            )
    finally:
        pass
    return 0


async def _cmd_promote(args: argparse.Namespace) -> int:
    return await _shift_tier(args, delta=+1)


async def _cmd_demote(args: argparse.Namespace) -> int:
    return await _shift_tier(args, delta=-1)


async def _shift_tier(args: argparse.Namespace, delta: int) -> int:
    db = await _open_db()
    from sqlalchemy import text

    async with db() as session:
        result = await session.execute(
            text(
                """
                SELECT current_tier FROM agent_trust
                WHERE agent_slug = :slug
                  AND tenant_id = CAST(:tenant AS uuid)
                  AND visibility = :v
                FOR UPDATE
                """
            ),
            {"slug": args.agent, "tenant": args.tenant, "v": args.visibility},
        )
        row = result.mappings().first()
        if row is None:
            print(
                f"(no trust row to {('promote' if delta > 0 else 'demote')}; "
                f"create the row first via the calibration daemon)"
            )
            return 1
        new_tier = max(
            int(TrustTier.T0_PROBATION),
            min(int(TrustTier.T4_PRINCIPAL), int(row["current_tier"]) + delta),
        )
        await session.execute(
            text(
                """
                UPDATE agent_trust
                SET current_tier = :tier,
                    last_updated = now()
                WHERE agent_slug = :slug
                  AND tenant_id = CAST(:tenant AS uuid)
                  AND visibility = :v
                """
            ),
            {
                "tier": new_tier,
                "slug": args.agent,
                "tenant": args.tenant,
                "v": args.visibility,
            },
        )
        await session.commit()
        print(
            f"{args.agent}/{args.tenant}/{args.visibility}: "
            f"tier {row['current_tier']} -> {new_tier} "
            f"({tier_name(TrustTier(new_tier))})"
        )
    return 0


async def _cmd_dlq(args: argparse.Namespace) -> int:
    db = await _open_db()
    async with db() as session:
        dlq = DeadLetterQueue(db=session)
        entries = await dlq.list_by_error_class(
            tenant_id=UUID(args.tenant) if args.tenant else None,
            error_class=ErrorClass(args.error_class) if args.error_class else None,
            limit=args.limit,
        )
        if not entries:
            print("(no DLQ entries)")
            return 0
        print(f"{'ID':<38} {'AGENT':<20} {'ERROR_CLASS':<22} RETRIES")
        for e in entries:
            print(f"{str(e.id):<38} {e.agent_slug:<20} {e.error_class.value:<22} {e.retry_count}")
    return 0


async def _cmd_replay(args: argparse.Namespace) -> int:
    print(
        "Replay via CLI is not yet wired to the per-agent task dispatch.\n"
        "Use the MCP admin tool `drain_dlq` or call DeadLetterQueue.replay()\n"
        "from a Python shell with a per-agent replay_fn."
    )
    return 1


async def _cmd_pause(args: argparse.Namespace) -> int:
    return await _set_breaker(args, action="pause")


async def _cmd_resume(args: argparse.Namespace) -> int:
    return await _set_breaker(args, action="resume")


async def _set_breaker(args: argparse.Namespace, action: str) -> int:
    db = await _open_db()
    from redis.asyncio import Redis

    settings = get_settings()
    redis = Redis.from_url(settings.REDIS_URL)
    try:
        async with db() as session:
            cb = CircuitBreaker(redis=redis, db=session)
            placeholder_user = UUID("00000000-0000-0000-0000-000000000000")
            if action == "pause":
                state = await cb.pause(
                    agent_slug=args.agent,
                    actor_user_id=placeholder_user,
                    reason=args.reason,
                )
            else:
                state = await cb.resume(
                    agent_slug=args.agent,
                    actor_user_id=placeholder_user,
                    reason=args.reason,
                )
            await session.commit()
            print(f"{state.scope}: open={state.open} reason={state.reason}")
        return 0
    finally:
        await redis.aclose()


async def _open_db() -> Any:
    settings = get_settings()
    engine = create_async_engine(
        settings.AUDIT_DATABASE_URL or settings.DATABASE_URL,
        echo=False,
    )

    def factory() -> AsyncSession:
        return AsyncSession(bind=engine, expire_on_commit=False)

    return factory


if __name__ == "__main__":
    sys.exit(main())
