"""``BaseAgent``: the substrate every Phase 3 agent inherits from.

A subclass implements ``_run(ctx)`` and registers an ``AgentDef`` at module
import time. The base class handles OpenTelemetry tracing, idempotency,
cost guards, trust-ladder updates, action_event emission, error routing
to DLQ, and circuit breaker integration.

The contract the inbox UI relies on:

* Every agent run is wrapped in one OTel span (with GenAI semantic
  conventions when an LLM is called).
* Every external mutation goes through ``propose_action``, which writes
  to ``action_event`` and auto-applies *only* when (tier, confidence,
  reversibility) all align with the trust-ladder policy.
* Every irreversible action surfaces as ``status='proposed'`` regardless
  of tier — auto-apply is gated on ``Reversibility.REVERSIBLE`` /
  ``REVERSIBLE_24H``.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.agents.circuit_breaker import CircuitBreaker
from contact_ops.agents.cost_guard import CostGuard
from contact_ops.agents.errors import (
    AgentError,
    CircuitBreakerOpenError,
    CostBudgetExceededError,
    IdempotencyKeyReusedError,
    RetryableError,
    WorkspaceAgentsPausedError,
)
from contact_ops.agents.observability.metrics import (
    AGENT_LATENCY_SECONDS,
    AGENT_SUCCESS_TOTAL,
)
from contact_ops.agents.observability.otel import (
    GenAIAttributes,
    agent_span,
)
from contact_ops.agents.registry import AgentClass, AgentDef, Reversibility
from contact_ops.agents.trust import TrustTier, tier_from_posterior

logger = structlog.get_logger(__name__)


class Visibility(str, Enum):
    """Agent scoping visibility (mirrors ``feedback_agent_scoping_pattern``)."""

    PRIVATE = "private"
    TEAM = "team"
    ORG = "org"
    SHARED = "shared"


@dataclass
class AgentContext:
    """Per-invocation context. Constructed by the runtime, handed to ``_run``."""

    db: AsyncSession
    audit_db: AsyncSession
    tenant_id: UUID
    visibility: Visibility
    triggered_by: str
    event_payload: dict[str, Any]
    cost_guard: CostGuard
    circuit_breaker: CircuitBreaker
    run_id: UUID = field(default_factory=uuid4)
    started_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class AgentResult:
    """Returned by ``_run``; consumed by the runtime for telemetry."""

    proposals_emitted: int = 0
    proposals_auto_applied: int = 0
    decision_summary: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProposedAction:
    """Outcome of a single ``propose_action`` call (returned for tests + audit)."""

    event_id: UUID
    status: str  # 'proposed' | 'applied' | 'replayed'
    idempotency_key: str
    auto_applied: bool
    tier_at_creation: TrustTier
    decision_payload: dict[str, Any]


class BaseAgent(ABC):
    """Common runtime wrapper. Subclasses implement ``_run``."""

    def __init__(self, agent_def: AgentDef) -> None:
        self._agent_def = agent_def

    @property
    def agent_def(self) -> AgentDef:
        return self._agent_def

    @abstractmethod
    async def _run(self, ctx: AgentContext) -> AgentResult:
        """Subclass implementation. See ``docs/AGENTS.md`` for the worked example."""

    async def execute(
        self,
        *,
        ctx: AgentContext,
    ) -> AgentResult:
        """Wrap ``_run`` with span + cost guard pre-check + breaker check."""
        # Circuit breaker: refuse to run if open. The breaker also stops
        # cost burn on already-known-bad agents.
        await ctx.circuit_breaker.check(agent_slug=self.agent_def.slug)

        # Workspace master switch: an admin can pause the entire fleet for a
        # tenant via tenants.agents_paused. Checked right after the breaker so a
        # paused workspace incurs no span, no cost, no _run. Distinct from the
        # per-agent / auto-fleet breaker above: this is a deliberate human stop.
        await self._assert_workspace_not_paused(ctx)

        outcome_label = "errored"
        span_cm = agent_span(
            agent_slug=self.agent_def.slug,
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            agent_version=self.agent_def.version,
        )
        with span_cm as span:
            span.set_attribute("contactops.agent.class", self.agent_def.agent_class.value)
            span.set_attribute("contactops.agent.visibility", ctx.visibility.value)
            span.set_attribute("contactops.agent.triggered_by", ctx.triggered_by)
            t0 = datetime.now(UTC).timestamp()
            try:
                result = await self._run(ctx)
            except CostBudgetExceededError as exc:
                outcome_label = "errored"
                span.set_attribute(GenAIAttributes.RESPONSE_FINISH_REASONS, "budget")
                span.record_exception(exc)
                raise
            except CircuitBreakerOpenError:
                outcome_label = "errored"
                raise
            except RetryableError:
                outcome_label = "errored"
                raise
            except AgentError as exc:
                outcome_label = "errored"
                span.record_exception(exc)
                raise
            else:
                outcome_label = "succeeded"
                span.set_attribute("contactops.agent.proposals_emitted", result.proposals_emitted)
                span.set_attribute(
                    "contactops.agent.proposals_auto_applied",
                    result.proposals_auto_applied,
                )
                return result
            finally:
                duration = datetime.now(UTC).timestamp() - t0
                tier = await self._current_tier(ctx)
                AGENT_LATENCY_SECONDS.labels(
                    agent=self.agent_def.slug,
                    tenant=str(ctx.tenant_id),
                    tier=str(int(tier)),
                ).observe(duration)
                AGENT_SUCCESS_TOTAL.labels(
                    agent=self.agent_def.slug,
                    tenant=str(ctx.tenant_id),
                    outcome=outcome_label,
                ).inc()

    async def propose_action(
        self,
        *,
        ctx: AgentContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: UUID,
        payload_before: dict[str, Any] | None,
        payload_after: dict[str, Any],
        confidence: float,
        reversibility: Reversibility,
        evidence: dict[str, Any],
        rationale: str,
        allow_replay: bool = True,
        target_tenant_id: UUID | None = None,
    ) -> ProposedAction:
        """Emit an ``action_event``. Auto-apply when policy permits.

        Idempotency: the key is
        ``sha256(agent_slug || tenant_id || aggregate_id || event_type || content_hash)``.
        Replays return the existing row's decision_payload rather than
        re-running the LLM call. Set ``allow_replay=False`` to raise
        ``IdempotencyKeyReusedError`` instead.

        Auto-apply policy:
            * ``Reversibility.IRREVERSIBLE`` -> never auto-apply.
            * Otherwise see ``_should_auto_apply``.

        Phase 3.3a additions: after the proposal is persisted, runs a
        conflict-detection query against other ``status='proposed'`` rows
        on the same aggregate within the last hour. Any overlap on
        ``payload_after`` top-level keys inserts a ``proposal_conflict``
        row, and the DB trigger ``proposal_conflict_clear_snooze_trg``
        clears any active snooze on both involved proposals.

        ``target_tenant_id`` is for cross-tenant agents (CardDAV
        reconciliation, Data Intel bridge). Populated, it drives the
        ``cross_tenant`` derived flag the inbox UI reads.
        """
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1]; got {confidence}")

        idempotency_key = self._idempotency_key(
            agent_slug=self.agent_def.slug,
            tenant_id=ctx.tenant_id,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload_after=payload_after,
        )

        existing = await self._lookup_existing(
            db=ctx.audit_db,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if not allow_replay:
                raise IdempotencyKeyReusedError(
                    idempotency_key=idempotency_key,
                    existing_event_id=str(existing["event_id"]),
                )
            return ProposedAction(
                event_id=UUID(str(existing["event_id"])),
                status="replayed",
                idempotency_key=idempotency_key,
                auto_applied=existing["status"] == "applied",
                tier_at_creation=TrustTier(int(existing.get("tier_at_creation") or 0)),
                decision_payload=existing.get("decision_payload") or {},
            )

        tier = await self._current_tier(ctx)
        auto_apply = self._should_auto_apply(
            tier=tier,
            confidence=confidence,
            reversibility=reversibility,
        )
        status = "applied" if auto_apply else "proposed"

        # Construct the decision_payload that the inbox + replay will use.
        decision_payload = {
            "reversibility": reversibility.value,
            "tier_at_creation": int(tier),
            "auto_apply_eligible": auto_apply,
            "payload_after": payload_after,
            "payload_before": payload_before,
        }

        content_hash = hashlib.sha256(
            json.dumps(
                {"payload": payload_after, "event_type": event_type},
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).digest()

        actor_chain = {
            "sub": self.agent_def.slug,
            "agent_version": self.agent_def.version,
            "act": {"sub": "system"},
        }

        result = await ctx.audit_db.execute(
            text(
                """
                INSERT INTO action_event (
                    event_type, event_version,
                    tenant_id, target_tenant_id, aggregate_type, aggregate_id,
                    payload, actor, actor_type,
                    confidence, evidence, rationale,
                    status, content_hash,
                    idempotency_key, decision_payload, reversibility_class,
                    agent_version, trust_tier_at_creation, triggered_by
                ) VALUES (
                    :event_type, 1,
                    CAST(:tenant_id AS uuid),
                    CAST(:target_tenant_id AS uuid),
                    CAST(:aggregate_type AS entity_kind),
                    CAST(:aggregate_id AS uuid),
                    CAST(:payload AS jsonb), CAST(:actor AS jsonb), 'agent'::actor_type,
                    :confidence, CAST(:evidence AS jsonb), :rationale,
                    CAST(:status AS event_status), :content_hash,
                    :idempotency_key, CAST(:decision_payload AS jsonb), :reversibility,
                    :agent_version, :tier, :triggered_by
                )
                RETURNING event_id
                """
            ),
            {
                "event_type": event_type,
                "tenant_id": str(ctx.tenant_id),
                "target_tenant_id": str(target_tenant_id) if target_tenant_id else None,
                "aggregate_type": aggregate_type,
                "aggregate_id": str(aggregate_id),
                "payload": json.dumps(payload_after, sort_keys=True, default=str),
                "actor": json.dumps(actor_chain, sort_keys=True),
                "confidence": confidence,
                "evidence": json.dumps(evidence, sort_keys=True, default=str),
                "rationale": rationale[:8192],
                "status": status,
                "content_hash": content_hash,
                "idempotency_key": idempotency_key,
                "decision_payload": json.dumps(
                    decision_payload, sort_keys=True, default=str
                ),
                "reversibility": reversibility.value,
                "agent_version": self.agent_def.version,
                "tier": int(tier),
                "triggered_by": ctx.triggered_by[:255],
            },
        )
        event_id = UUID(str(result.scalar_one()))

        if auto_apply:
            await ctx.audit_db.execute(
                text(
                    """
                    UPDATE action_event
                    SET applied_at = now()
                    WHERE event_id = CAST(:id AS uuid)
                    """
                ),
                {"id": str(event_id)},
            )
            # Auto-apply must perform the proposal's real-world effect, not just
            # mark it applied. For a dedup merge at T2+ this actually merges the
            # two person records via merge_people; without it a "trusted" agent
            # would silently no-op. Same sessions + transaction as the insert, so
            # a merge failure aborts the whole propose_action.
            from contact_ops.services.downstream_apply import (
                apply_downstream_effect,
            )

            await apply_downstream_effect(
                event_type=event_type,
                app_db=ctx.db,
                audit_db=ctx.audit_db,
                tenant_id=ctx.tenant_id,
                payload=payload_after,
                confidence=confidence,
                actor_chain=actor_chain,
                human_authority="agent",
                request_id=f"agent-autoapply:{self.agent_def.slug}",
            )
        else:
            # Phase 3.3a (C6): only proposed (non-auto-applied) rows can
            # conflict against another inbox item. Auto-applied rows are
            # already resolved and don't surface a ConflictBanner.
            await self._detect_and_record_conflict(
                ctx=ctx,
                new_event_id=event_id,
                aggregate_id=aggregate_id,
                payload_after=payload_after,
            )

        await ctx.audit_db.commit()

        return ProposedAction(
            event_id=event_id,
            status=status,
            idempotency_key=idempotency_key,
            auto_applied=auto_apply,
            tier_at_creation=tier,
            decision_payload=decision_payload,
        )

    # ---- internals ----

    async def _detect_and_record_conflict(
        self,
        *,
        ctx: AgentContext,
        new_event_id: UUID,
        aggregate_id: UUID,
        payload_after: dict[str, Any],
    ) -> None:
        """C6: find a conflicting proposal in last 1h and insert proposal_conflict.

        "Conflict" = same aggregate + status='proposed' + shares any
        top-level ``payload_after`` JSON key. The first overlapping key
        becomes ``proposal_conflict.field_name`` for the UI to highlight.
        Skipped for empty payloads. Caps lookback at 20 rows; cascade of
        many-vs-one would be enriched in a future calibration sweep.
        """
        candidate_keys = list((payload_after or {}).keys())
        if not candidate_keys:
            return

        result = await ctx.audit_db.execute(
            text(
                """
                SELECT event_id, decision_payload
                FROM action_event
                WHERE aggregate_id = CAST(:agg AS uuid)
                  AND tenant_id = CAST(:tenant AS uuid)
                  AND event_id != CAST(:self AS uuid)
                  AND status = 'proposed'
                  AND proposed_at > now() - INTERVAL '1 hour'
                ORDER BY proposed_at DESC
                LIMIT 20
                """
            ),
            {
                "agg": str(aggregate_id),
                "tenant": str(ctx.tenant_id),
                "self": str(new_event_id),
            },
        )
        new_keys = set(candidate_keys)
        for row in result.mappings():
            other_payload_after: dict[str, Any] = {}
            decision_payload = row["decision_payload"] or {}
            if isinstance(decision_payload, dict):
                pa = decision_payload.get("payload_after")
                if isinstance(pa, dict):
                    other_payload_after = pa
            overlap = sorted(new_keys & set(other_payload_after.keys()))
            if not overlap:
                continue
            await ctx.audit_db.execute(
                text(
                    """
                    INSERT INTO proposal_conflict (
                        tenant_id, primary_proposal_id, conflicting_proposal_id,
                        conflict_type, entity_id, field_name
                    ) VALUES (
                        CAST(:tenant AS uuid),
                        CAST(:primary AS uuid), CAST(:conflicting AS uuid),
                        'contradicting_field_value',
                        CAST(:entity AS uuid), :field
                    )
                    """
                ),
                {
                    "tenant": str(ctx.tenant_id),
                    "primary": str(new_event_id),
                    "conflicting": str(row["event_id"]),
                    "entity": str(aggregate_id),
                    "field": overlap[0],
                },
            )
            return

    def _should_auto_apply(
        self,
        *,
        tier: TrustTier,
        confidence: float,
        reversibility: Reversibility,
    ) -> bool:
        """Auto-apply policy per Phase 3 Design §5.

        Irreversible never auto-applies. Soft-delete never auto-applies in
        Phase 3 (Phase 5 may relax this once 90-day restore is hardened).
        """
        if reversibility in (Reversibility.IRREVERSIBLE, Reversibility.SOFT_DELETE):
            return False
        if tier == TrustTier.T0_PROBATION:
            return False
        if tier == TrustTier.T1_TRAINEE:
            return False
        if tier == TrustTier.T2_TRUSTED:
            return confidence >= 0.95
        if tier == TrustTier.T3_SENIOR:
            return confidence >= 0.85
        if tier == TrustTier.T4_PRINCIPAL:
            return True
        return False

    async def _current_tier(self, ctx: AgentContext) -> TrustTier:
        """Read the current Beta(alpha, beta) tier or fall back to initial.

        The trust-ladder math lives in ``trust.py``; this method just
        fetches the row and applies ``tier_from_posterior``.
        """
        result = await ctx.audit_db.execute(
            text(
                """
                SELECT alpha, beta, current_tier
                FROM agent_trust
                WHERE agent_slug = :slug
                  AND tenant_id = CAST(:tenant AS uuid)
                  AND visibility = :visibility
                """
            ),
            {
                "slug": self.agent_def.slug,
                "tenant": str(ctx.tenant_id),
                "visibility": ctx.visibility.value,
            },
        )
        row = result.mappings().first()
        if row is None:
            return self.agent_def.initial_trust_tier

        # Honor sticky demotion: if the Calibration Daemon has demoted the
        # agent below what the posterior implies, use the demoted value.
        posterior_tier = tier_from_posterior(
            _make_posterior(float(row["alpha"]), float(row["beta"]))
        )
        stored_tier = TrustTier(int(row["current_tier"]))
        return TrustTier(min(int(posterior_tier), int(stored_tier)))

    async def _assert_workspace_not_paused(self, ctx: AgentContext) -> None:
        """Raise if an admin has paused the whole fleet for this tenant.

        Reads ``tenants.agents_paused`` through the RLS-bound app session (the
        row is the caller's own tenant via the tenants_select / tenants_modify
        policies). A missing row is treated as not-paused (fail-open) rather than
        wedging the fleet on an anomalous read; the deliberate stop is the True
        flag, not the absence of a row.
        """
        result = await ctx.db.execute(
            text(
                """
                SELECT agents_paused, agents_paused_reason
                FROM tenants
                WHERE id = CAST(:tenant AS uuid)
                """
            ),
            {"tenant": str(ctx.tenant_id)},
        )
        row = result.mappings().first()
        if row is not None and bool(row["agents_paused"]):
            raise WorkspaceAgentsPausedError(
                tenant_id=str(ctx.tenant_id),
                reason=row["agents_paused_reason"],
            )

    async def _lookup_existing(
        self,
        *,
        db: AsyncSession,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        result = await db.execute(
            text(
                """
                SELECT event_id, status, trust_tier_at_creation AS tier_at_creation,
                       decision_payload
                FROM action_event
                WHERE idempotency_key = :key
                LIMIT 1
                """
            ),
            {"key": idempotency_key},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    @staticmethod
    def _idempotency_key(
        *,
        agent_slug: str,
        tenant_id: UUID,
        aggregate_id: UUID,
        event_type: str,
        payload_after: dict[str, Any],
    ) -> str:
        content_hash = hashlib.sha256(
            json.dumps(payload_after, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        material = f"{agent_slug}|{tenant_id}|{aggregate_id}|{event_type}|{content_hash}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _make_posterior(alpha: float, beta: float):  # type: ignore[no-untyped-def]
    """Import-cycle dodge for ``BetaPosterior``."""
    from contact_ops.agents.trust import BetaPosterior

    return BetaPosterior(alpha=alpha, beta=beta)


__all__ = [
    "AgentClass",
    "AgentContext",
    "AgentResult",
    "BaseAgent",
    "ProposedAction",
    "Visibility",
]
