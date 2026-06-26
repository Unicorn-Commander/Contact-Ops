"""MCP admin tools for the Phase 3 agent fleet.

Nine tools, all ADMIN-role gated:

* ``list_agents``          - registry contents per tenant
* ``get_agent_trust``      - Beta(alpha, beta) + tier per (agent x tenant x visibility)
* ``promote_agent_tier``   - shift the stored tier up one
* ``demote_agent_tier``    - shift the stored tier down one
* ``drain_dlq``            - replay DLQ entries by id
* ``pause_agent``          - open the per-agent circuit breaker
* ``resume_agent``         - close the per-agent (or fleet) breaker
* ``get_agent_governance`` - read the workspace agents-paused master switch
* ``set_agents_paused``    - workspace kill-switch: pause/resume the whole fleet

These mirror the ``contact_ops.agents.cli`` admin commands so SREs can drive
the fleet from either a tunnel + Python shell or a JWT-authenticated MCP
session against the production API.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import text

from contact_ops.agents.circuit_breaker import CircuitBreaker
from contact_ops.agents.dlq import DeadLetterQueue
from contact_ops.agents.registry import list_agents
from contact_ops.agents.trust import (
    BetaPosterior,
    TrustTier,
    tier_from_posterior,
    tier_name,
)
from contact_ops.core.config import get_settings
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools.common import ToolOutput, register

VISIBILITIES = Literal["private", "team", "org", "shared"]


# ---- list_agents ----

class ListAgentsInput(BaseModel):
    include_inactive: bool = False


class AgentSummary(BaseModel):
    slug: str
    name: str
    version: str
    agent_class: str
    description: str
    cost_budget_monthly_cents: int
    initial_trust_tier: int
    initial_trust_tier_label: str
    triggers: list[str] = Field(default_factory=list)
    declared_capabilities: list[str] = Field(default_factory=list)


class ListAgentsOutput(ToolOutput):
    agents: list[AgentSummary]
    count: int


async def _handle_list_agents(ctx: MCPContext, payload: ListAgentsInput) -> ListAgentsOutput:
    rows = list_agents()
    summaries = [
        AgentSummary(
            slug=a.slug,
            name=a.name,
            version=a.version,
            agent_class=a.agent_class.value,
            description=a.description,
            cost_budget_monthly_cents=a.cost_budget_monthly_cents,
            initial_trust_tier=int(a.initial_trust_tier),
            initial_trust_tier_label=tier_name(a.initial_trust_tier),
            triggers=list(a.triggers),
            declared_capabilities=list(a.declared_capabilities),
        )
        for a in rows
    ]
    return ListAgentsOutput(agents=summaries, count=len(summaries))


# ---- get_agent_trust ----

class GetAgentTrustInput(BaseModel):
    agent_slug: str = Field(min_length=1, max_length=64)
    visibility: VISIBILITIES = "private"


class AgentTrustSummary(BaseModel):
    agent_slug: str
    visibility: str
    tenant_id: uuid.UUID
    alpha: float
    beta: float
    mean: float
    lower_ci_95: float
    posterior_tier: int
    posterior_tier_label: str
    stored_tier: int
    stored_tier_label: str
    drift_status: str
    samples_total: int
    samples_7d: int
    samples_30d: int


class GetAgentTrustOutput(ToolOutput):
    trust: AgentTrustSummary | None
    found: bool


async def _handle_get_agent_trust(
    ctx: MCPContext, payload: GetAgentTrustInput
) -> GetAgentTrustOutput:
    result = await ctx.db.execute(
        text(
            """
            SELECT alpha, beta, current_tier, drift_status,
                   samples_total, samples_7d, samples_30d
            FROM agent_trust
            WHERE agent_slug = :slug
              AND tenant_id = CAST(:tenant AS uuid)
              AND visibility = :visibility
            """
        ),
        {
            "slug": payload.agent_slug,
            "tenant": str(ctx.tenant_id),
            "visibility": payload.visibility,
        },
    )
    row = result.mappings().first()
    if row is None:
        return GetAgentTrustOutput(trust=None, found=False)

    alpha = float(row["alpha"])
    beta = float(row["beta"])
    posterior = BetaPosterior(alpha=alpha, beta=beta)
    posterior_tier = tier_from_posterior(posterior)
    stored_tier = TrustTier(int(row["current_tier"]))

    summary = AgentTrustSummary(
        agent_slug=payload.agent_slug,
        visibility=payload.visibility,
        tenant_id=ctx.tenant_id,
        alpha=alpha,
        beta=beta,
        mean=posterior.mean,
        lower_ci_95=posterior.lower_credible_interval(0.95),
        posterior_tier=int(posterior_tier),
        posterior_tier_label=tier_name(posterior_tier),
        stored_tier=int(stored_tier),
        stored_tier_label=tier_name(stored_tier),
        drift_status=str(row["drift_status"]),
        samples_total=int(row["samples_total"]),
        samples_7d=int(row["samples_7d"]),
        samples_30d=int(row["samples_30d"]),
    )
    return GetAgentTrustOutput(trust=summary, found=True)


# ---- promote_agent_tier / demote_agent_tier ----

class TierShiftInput(BaseModel):
    agent_slug: str = Field(min_length=1, max_length=64)
    visibility: VISIBILITIES = "private"
    reason: str = Field(min_length=1, max_length=1024)


class TierShiftOutput(ToolOutput):
    agent_slug: str
    visibility: str
    previous_tier: int
    previous_tier_label: str
    new_tier: int
    new_tier_label: str
    changed: bool


async def _shift_tier(
    ctx: MCPContext, payload: TierShiftInput, *, delta: int
) -> TierShiftOutput:
    result = await ctx.db.execute(
        text(
            """
            SELECT current_tier
            FROM agent_trust
            WHERE agent_slug = :slug
              AND tenant_id = CAST(:tenant AS uuid)
              AND visibility = :visibility
            FOR UPDATE
            """
        ),
        {
            "slug": payload.agent_slug,
            "tenant": str(ctx.tenant_id),
            "visibility": payload.visibility,
        },
    )
    row = result.mappings().first()
    if row is None:
        # Bootstrap a Beta(1,1) prior at the agent's initial tier.
        from contact_ops.agents.registry import get_agent

        agent = get_agent(payload.agent_slug)
        if agent is None:
            return TierShiftOutput(
                agent_slug=payload.agent_slug,
                visibility=payload.visibility,
                previous_tier=0,
                previous_tier_label=tier_name(TrustTier.T0_PROBATION),
                new_tier=0,
                new_tier_label=tier_name(TrustTier.T0_PROBATION),
                changed=False,
            )
        await ctx.db.execute(
            text(
                """
                INSERT INTO agent_trust (
                    agent_slug, tenant_id, visibility, current_tier
                ) VALUES (
                    :slug, CAST(:tenant AS uuid), :visibility, :tier
                )
                ON CONFLICT (agent_slug, tenant_id, visibility) DO NOTHING
                """
            ),
            {
                "slug": payload.agent_slug,
                "tenant": str(ctx.tenant_id),
                "visibility": payload.visibility,
                "tier": int(agent.initial_trust_tier),
            },
        )
        current_tier = int(agent.initial_trust_tier)
    else:
        current_tier = int(row["current_tier"])

    new_tier = max(
        int(TrustTier.T0_PROBATION),
        min(int(TrustTier.T4_PRINCIPAL), current_tier + delta),
    )
    changed = new_tier != current_tier
    if changed:
        await ctx.db.execute(
            text(
                """
                UPDATE agent_trust
                SET current_tier = :tier,
                    last_demotion_at =
                        CASE WHEN :tier < current_tier THEN now() ELSE last_demotion_at END,
                    last_updated = now()
                WHERE agent_slug = :slug
                  AND tenant_id = CAST(:tenant AS uuid)
                  AND visibility = :visibility
                """
            ),
            {
                "slug": payload.agent_slug,
                "tenant": str(ctx.tenant_id),
                "visibility": payload.visibility,
                "tier": new_tier,
            },
        )
        await ctx.audit_db.execute(
            text(
                """
                INSERT INTO prov_activities (
                    tenant_id, activity_type, agent_id, agent_version,
                    started_at, ended_at, reasoning, confidence
                ) VALUES (
                    CAST(:tenant AS uuid), :activity, :slug, 'admin-action',
                    now(), now(), :reason, 1.0
                )
                """
            ),
            {
                "tenant": str(ctx.tenant_id),
                "activity": "calibration.tier_promote" if delta > 0 else "calibration.tier_demote",
                "slug": payload.agent_slug,
                "reason": payload.reason,
            },
        )

    return TierShiftOutput(
        agent_slug=payload.agent_slug,
        visibility=payload.visibility,
        previous_tier=current_tier,
        previous_tier_label=tier_name(TrustTier(current_tier)),
        new_tier=new_tier,
        new_tier_label=tier_name(TrustTier(new_tier)),
        changed=changed,
    )


async def _handle_promote(ctx: MCPContext, payload: TierShiftInput) -> TierShiftOutput:
    return await _shift_tier(ctx, payload, delta=+1)


async def _handle_demote(ctx: MCPContext, payload: TierShiftInput) -> TierShiftOutput:
    return await _shift_tier(ctx, payload, delta=-1)


# ---- drain_dlq ----

class DrainDLQInput(BaseModel):
    dlq_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    mark_unreplayable_on_fail: bool = False


class DrainDLQOutput(ToolOutput):
    requested: int
    replayed: int
    still_failing: int
    skipped_unreplayable: int
    errors: list[str] = Field(default_factory=list)


async def _handle_drain_dlq(ctx: MCPContext, payload: DrainDLQInput) -> DrainDLQOutput:
    dlq = DeadLetterQueue(db=ctx.db)

    async def _noop_replay(_entry) -> None:  # type: ignore[no-untyped-def]
        # Phase 3.0 ships the substrate only; per-agent replay handlers land
        # with their agents in 3.1+. For now ``drain_dlq`` just re-stamps
        # the retry counter and reports.
        raise RuntimeError(
            "no per-agent replay handler registered for Phase 3.0; "
            "use the agent's own re-trigger MCP tool"
        )

    result = await dlq.replay(dlq_ids=payload.dlq_ids, replay_fn=_noop_replay)
    return DrainDLQOutput(
        requested=len(payload.dlq_ids),
        replayed=result.replayed,
        still_failing=result.still_failing,
        skipped_unreplayable=result.skipped_unreplayable,
        errors=result.errors,
    )


# ---- pause_agent / resume_agent ----

class BreakerInput(BaseModel):
    agent_slug: str | None = Field(default=None, max_length=64)
    """``None`` means fleet-wide."""

    reason: str = Field(min_length=1, max_length=1024)


class BreakerOutput(ToolOutput):
    scope: str
    open: bool
    reason: str | None


def _open_redis() -> Redis:
    """Open an async Redis client from settings.

    ``Redis.from_url`` is typed as ``Any`` in redis-py 6.x, so we cast at
    the boundary to keep mypy --strict clean.
    """
    settings = get_settings()
    redis: Redis = Redis.from_url(settings.REDIS_URL)
    return redis


async def _handle_pause(ctx: MCPContext, payload: BreakerInput) -> BreakerOutput:
    if payload.agent_slug is None:
        # Pausing the entire fleet is a high-blast-radius operation; we
        # require a typed-phrase ack out-of-band (the Inbox UI's Tier 4
        # gate) — for the MCP surface, we surface an explicit error to
        # force the caller to use ``resume_agent`` with agent_slug=None
        # only after the fleet was tripped automatically.
        return BreakerOutput(
            scope="fleet",
            open=False,
            reason=(
                "fleet-wide pause via MCP requires the Inbox UI's typed-phrase "
                "gate; use the auto-trip path or call from the CLI"
            ),
        )
    redis = _open_redis()
    try:
        breaker = CircuitBreaker(redis=redis, db=ctx.db)
        state = await breaker.pause(
            agent_slug=payload.agent_slug,
            actor_user_id=uuid.UUID(ctx.user_id),
            reason=payload.reason,
        )
        return BreakerOutput(scope=state.scope, open=state.open, reason=state.reason)
    finally:
        await redis.aclose()


async def _handle_resume(ctx: MCPContext, payload: BreakerInput) -> BreakerOutput:
    redis = _open_redis()
    try:
        breaker = CircuitBreaker(redis=redis, db=ctx.db)
        state = await breaker.resume(
            agent_slug=payload.agent_slug,
            actor_user_id=uuid.UUID(ctx.user_id),
            reason=payload.reason,
        )
        return BreakerOutput(scope=state.scope, open=state.open, reason=state.reason)
    finally:
        await redis.aclose()


# ---- set_agents_paused / get_agent_governance (workspace master switch) ----

class GetAgentGovernanceInput(BaseModel):
    pass


class SetAgentsPausedInput(BaseModel):
    paused: bool
    reason: str = Field(default="", max_length=1024)


class AgentGovernanceOutput(ToolOutput):
    agents_paused: bool
    agents_paused_reason: str | None
    agents_paused_at: datetime | None
    agents_paused_by: uuid.UUID | None


async def _read_agent_governance(ctx: MCPContext) -> AgentGovernanceOutput:
    result = await ctx.db.execute(
        text(
            """
            SELECT agents_paused, agents_paused_reason,
                   agents_paused_at, agents_paused_by
            FROM tenants
            WHERE id = CAST(:tenant AS uuid)
            """
        ),
        {"tenant": str(ctx.tenant_id)},
    )
    row = result.mappings().first()
    if row is None:
        return AgentGovernanceOutput(
            agents_paused=False,
            agents_paused_reason=None,
            agents_paused_at=None,
            agents_paused_by=None,
        )
    return AgentGovernanceOutput(
        agents_paused=bool(row["agents_paused"]),
        agents_paused_reason=row["agents_paused_reason"],
        agents_paused_at=row["agents_paused_at"],
        agents_paused_by=row["agents_paused_by"],
    )


async def _handle_get_agent_governance(
    ctx: MCPContext, payload: GetAgentGovernanceInput
) -> AgentGovernanceOutput:
    return await _read_agent_governance(ctx)


async def _handle_set_agents_paused(
    ctx: MCPContext, payload: SetAgentsPausedInput
) -> AgentGovernanceOutput:
    reason = payload.reason.strip() or None
    actor = uuid.UUID(ctx.user_id)
    # The tenants_modify RLS policy (FOR ALL TO contact_ops_app, migration 0015)
    # scopes this UPDATE to the caller's own tenant; reason/at/by are cleared on
    # resume so a not-paused row never carries stale pause metadata.
    await ctx.db.execute(
        text(
            """
            UPDATE tenants
            SET agents_paused = :paused,
                agents_paused_reason = CASE WHEN :paused THEN :reason ELSE NULL END,
                agents_paused_at = CASE WHEN :paused THEN now() ELSE NULL END,
                agents_paused_by = CASE WHEN :paused THEN CAST(:actor AS uuid) ELSE NULL END,
                updated_at = now()
            WHERE id = CAST(:tenant AS uuid)
            """
        ),
        {
            "paused": payload.paused,
            "reason": reason,
            "actor": str(actor),
            "tenant": str(ctx.tenant_id),
        },
    )
    # Audit the master-switch flip on the same provenance trail as tier changes.
    await ctx.audit_db.execute(
        text(
            """
            INSERT INTO prov_activities (
                tenant_id, activity_type, agent_id, agent_version,
                started_at, ended_at, reasoning, confidence
            ) VALUES (
                CAST(:tenant AS uuid), :activity, 'workspace', 'admin-action',
                now(), now(), :reason, 1.0
            )
            """
        ),
        {
            "tenant": str(ctx.tenant_id),
            "activity": (
                "fleet.workspace_pause" if payload.paused else "fleet.workspace_resume"
            ),
            "reason": reason or ("paused" if payload.paused else "resumed"),
        },
    )
    return await _read_agent_governance(ctx)


# ---- registration ----

def register_agent_admin_tools() -> None:
    """Register all 7 Phase 3.0 admin MCP tools.

    All tools require the ADMIN role and ``contactops:agents.admin``
    scope. They are idempotent — repeat invocations re-stamp state
    or return the same answer.
    """

    register(
        name="list_agents",
        description="List all agents registered with the in-process fleet runtime.",
        input_model=ListAgentsInput,
        output_model=ListAgentsOutput,
        handler=_handle_list_agents,
        required_role="ADMIN",
        required_scopes=("contactops:agents.admin",),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="repeatable",
    )
    register(
        name="get_agent_trust",
        description=(
            "Return Beta(α, β), posterior-implied tier, stored tier, and drift "
            "status for an (agent × tenant × visibility) row."
        ),
        input_model=GetAgentTrustInput,
        output_model=GetAgentTrustOutput,
        handler=_handle_get_agent_trust,
        required_role="ADMIN",
        required_scopes=("contactops:agents.admin",),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="repeatable",
    )
    register(
        name="promote_agent_tier",
        description="Move the stored trust tier up by 1 (manual override).",
        input_model=TierShiftInput,
        output_model=TierShiftOutput,
        handler=_handle_promote,
        required_role="ADMIN",
        required_scopes=("contactops:agents.admin",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        idempotency="not-idempotent",
    )
    register(
        name="demote_agent_tier",
        description="Move the stored trust tier down by 1 (manual override).",
        input_model=TierShiftInput,
        output_model=TierShiftOutput,
        handler=_handle_demote,
        required_role="ADMIN",
        required_scopes=("contactops:agents.admin",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        idempotency="not-idempotent",
    )
    register(
        name="drain_dlq",
        description="Replay DLQ entries by id. Replay handlers ship per-agent in Phase 3.1+.",
        input_model=DrainDLQInput,
        output_model=DrainDLQOutput,
        handler=_handle_drain_dlq,
        required_role="ADMIN",
        required_scopes=("contactops:agents.admin",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        idempotency="not-idempotent",
    )
    register(
        name="pause_agent",
        description="Open the per-agent circuit breaker (refuses to run until resumed).",
        input_model=BreakerInput,
        output_model=BreakerOutput,
        handler=_handle_pause,
        required_role="ADMIN",
        required_scopes=("contactops:agents.admin",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="idempotent",
    )
    register(
        name="resume_agent",
        description="Close the per-agent (or fleet-wide) circuit breaker.",
        input_model=BreakerInput,
        output_model=BreakerOutput,
        handler=_handle_resume,
        required_role="ADMIN",
        required_scopes=("contactops:agents.admin",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="idempotent",
    )
    register(
        name="get_agent_governance",
        description=(
            "Read the workspace agent master switch: whether the fleet is paused "
            "for this tenant, and the reason / at / by when it is."
        ),
        input_model=GetAgentGovernanceInput,
        output_model=AgentGovernanceOutput,
        handler=_handle_get_agent_governance,
        required_role="ADMIN",
        required_scopes=("contactops:agents.admin",),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="repeatable",
    )
    register(
        name="set_agents_paused",
        description=(
            "Workspace kill-switch: pause or resume the ENTIRE agent fleet for "
            "this tenant. When paused, every agent refuses to run until resumed."
        ),
        input_model=SetAgentsPausedInput,
        output_model=AgentGovernanceOutput,
        handler=_handle_set_agents_paused,
        required_role="ADMIN",
        required_scopes=("contactops:agents.admin",),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        idempotency="idempotent",
    )


register_agent_admin_tools()
