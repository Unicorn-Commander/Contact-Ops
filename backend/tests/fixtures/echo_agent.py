"""Echo agent — a minimal BaseAgent subclass used by tests + docs.

The contract demonstration: a real Phase 3 agent inherits from
``BaseAgent``, declares its ``AgentDef``, implements ``_run``, and gets
everything else (cost guard, OTel span, trust-ladder + auto-apply
gating, idempotency) for free. This fixture is ~40 lines.
"""

from __future__ import annotations

import uuid

from contact_ops.agents import (
    AgentClass,
    AgentContext,
    AgentDef,
    AgentResult,
    BaseAgent,
    Reversibility,
    TrustTier,
)
from contact_ops.agents.registry import _clear_registry_for_tests, register_agent


def make_echo_agent_def(
    *,
    slug: str = "echo",
    initial_trust_tier: TrustTier = TrustTier.T0_PROBATION,
) -> AgentDef:
    return AgentDef(
        slug=slug,
        name="Echo Agent",
        version="0.0.1",
        agent_class=AgentClass.BATCH,
        description="Test fixture: emits one reversible action_event per run.",
        cost_budget_monthly_cents=100,
        initial_trust_tier=initial_trust_tier,
        triggers=("manual",),
        declared_capabilities=("echo.propose",),
    )


class EchoAgent(BaseAgent):
    """Emits one ``echo.propose`` action_event per ``_run``.

    Used by ``test_agent_base.py`` to exercise the full propose -> store ->
    optional-auto-apply path without needing any LLM mock.
    """

    async def _run(self, ctx: AgentContext) -> AgentResult:
        aggregate_id = uuid.UUID(
            ctx.event_payload.get("aggregate_id", str(uuid.uuid4()))
        )
        confidence = float(ctx.event_payload.get("confidence", 0.5))
        reversibility = Reversibility(
            ctx.event_payload.get("reversibility", Reversibility.REVERSIBLE.value)
        )
        proposed = await self.propose_action(
            ctx=ctx,
            event_type="echo.propose",
            aggregate_type="person",
            aggregate_id=aggregate_id,
            payload_before=None,
            payload_after={"echoed": ctx.event_payload.get("message", "ping")},
            confidence=confidence,
            reversibility=reversibility,
            evidence={"source": "echo-agent"},
            rationale="echo agent emitted a test proposal",
        )
        return AgentResult(
            proposals_emitted=1,
            proposals_auto_applied=1 if proposed.auto_applied else 0,
            decision_summary=f"echo proposed event_id={proposed.event_id}",
            extra={"event_id": str(proposed.event_id), "status": proposed.status},
        )


def register_echo_agent(
    *,
    slug: str = "echo",
    initial_trust_tier: TrustTier = TrustTier.T0_PROBATION,
) -> EchoAgent:
    """Register and return a fresh EchoAgent for tests.

    The registry is process-global so tests that register conflicting slugs
    must call ``_clear_registry_for_tests()`` first.
    """
    agent_def = make_echo_agent_def(
        slug=slug, initial_trust_tier=initial_trust_tier
    )
    register_agent(agent_def)
    return EchoAgent(agent_def)


__all__ = [
    "EchoAgent",
    "make_echo_agent_def",
    "register_echo_agent",
    "_clear_registry_for_tests",
]
