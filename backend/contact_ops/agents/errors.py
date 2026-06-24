"""Agent runtime error hierarchy.

Errors are routed by the ``BaseAgent.execute`` wrapper to one of three
destinations:

* ``RetryableError`` — transient (LLM 5xx, downstream timeout, broker hiccup).
  Celery retries with exponential backoff up to ``max_retries``; on exhaustion
  the action lands in ``agent_action_dlq``.
* ``CostBudgetExceededError`` — a layer-1..5 guard fired. The agent stops
  immediately; no action_event is written. Slack/email at 75/90/95/100%
  thresholds is the user-facing surface (see ``cost_guard``).
* ``CircuitBreakerOpenError`` — the per-agent or fleet-wide breaker is open;
  the agent refuses to run until the breaker is reset.
* ``WorkspaceAgentsPausedError`` is raised when an admin flips the tenant-wide
  ``tenants.agents_paused`` master switch; every agent then refuses to run for
  that workspace until it is cleared (before any work or cost, like the breaker).
* ``IdempotencyKeyReusedError`` — caller tried to write a proposal with a key
  that already exists. The replay path returns the prior decision instead of
  re-running the LLM; this exception is raised only when caller is asking for
  ``allow_replay=False`` behavior.

Anything else (``AgentError``) routes straight to DLQ as ``error_class=other``.
"""

from __future__ import annotations


class AgentError(Exception):
    """Base class for all agent-runtime errors."""


class RetryableError(AgentError):
    """Transient failure; Celery will retry with backoff."""


class IdempotencyKeyReusedError(AgentError):
    """A proposal already exists for this idempotency_key.

    The default behavior on key reuse is to return the prior proposal's
    decision_payload (replay-safe). This exception is raised only when the
    caller explicitly opted into ``allow_replay=False``.
    """

    def __init__(self, idempotency_key: str, existing_event_id: str) -> None:
        self.idempotency_key = idempotency_key
        self.existing_event_id = existing_event_id
        super().__init__(
            f"idempotency_key already used: {idempotency_key} -> "
            f"event {existing_event_id}"
        )


class CostBudgetExceededError(AgentError):
    """A cost guard layer denied the call.

    ``layer`` is 1-5 per the design doc §7:
      1. per-request token cap
      2. per-agent turn counter
      3. per-session budget
      4. per-tenant daily/hourly budget
      5. monthly budget (hard-stop at 100%)
    """

    def __init__(
        self,
        layer: int,
        message: str,
        *,
        agent_slug: str,
        tenant_id: str | None = None,
    ) -> None:
        self.layer = layer
        self.agent_slug = agent_slug
        self.tenant_id = tenant_id
        super().__init__(f"cost budget exceeded (layer {layer}): {message}")


class CircuitBreakerOpenError(AgentError):
    """The per-agent or fleet-wide circuit breaker is open."""

    def __init__(self, scope: str, reason: str) -> None:
        # scope ∈ {"agent:<slug>", "fleet"}
        self.scope = scope
        self.reason = reason
        super().__init__(f"circuit breaker open ({scope}): {reason}")


class WorkspaceAgentsPausedError(AgentError):
    """An admin paused the whole agent fleet for a tenant (master switch).

    Distinct from ``CircuitBreakerOpenError`` (a per-agent or auto-tripped fleet
    breaker): this is a deliberate, human-thrown stop on ``tenants.agents_paused``.
    Every agent refuses to run for the tenant until an admin clears it via the
    ``set_agents_paused`` MCP tool. Raised before any work or cost is incurred.
    """

    def __init__(self, tenant_id: str, reason: str | None) -> None:
        self.tenant_id = tenant_id
        self.reason = reason
        super().__init__(
            f"workspace agents paused (tenant {tenant_id}): "
            f"{reason or 'no reason given'}"
        )
