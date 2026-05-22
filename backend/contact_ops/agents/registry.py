"""Agent registry: ``AgentDef`` + ``register_agent`` / ``get_agent`` / ``list_agents``.

Mirrors the MCP tool registry in ``contact_ops.mcp.registry``. The in-process
``_REGISTRY`` dict is populated at import time by each agent module's
``register_agent(AgentDef(...))`` call. The Postgres ``agent_registry`` table
is the durable mirror, written on deploy by ``runtime.ensure_agents_registered``.

The split is intentional. The in-process registry is the source of truth for
*which Python class implements this agent slug*. The Postgres table is the
source of truth for *what trust tier and budget apply to this agent in this
tenant*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from contact_ops.agents.trust import TrustTier


class AgentClass(str, Enum):
    """Runtime class — determines which runner executes the agent."""

    BATCH = "batch"
    EVENT_DRIVEN = "event"
    CONTINUOUS = "continuous"


class Reversibility(str, Enum):
    """Reversibility class — gates auto-apply at every trust tier.

    See Phase 3 Design §2 (Architectural principles: "Reversibility is
    mandatory") and §3.3 (action_event.reversibility_class).
    """

    REVERSIBLE = "reversible"
    """Additive or undoable by symbolic deletion (tag, link, observe)."""

    REVERSIBLE_24H = "reversible_24h"
    """Reversible within a 24h cooldown window (field set, alias add)."""

    SOFT_DELETE = "soft_delete"
    """Tombstone (archive). 90-day restore window per project convention."""

    IRREVERSIBLE = "irreversible"
    """Hard delete, outbound send, external write. Never auto-applies."""


@dataclass(frozen=True)
class AgentDef:
    """Declarative definition of an agent.

    Registered once at module import time; persisted to Postgres on deploy.
    All fields are required so that a partial registration cannot ride to
    production unnoticed.
    """

    slug: str
    name: str
    version: str
    agent_class: AgentClass
    description: str
    cost_budget_monthly_cents: int
    initial_trust_tier: TrustTier
    triggers: tuple[str, ...] = field(default_factory=tuple)
    """Cron expressions, ``event:<channel>`` strings, or the literal
    ``"continuous"`` for always-on workers."""

    declared_capabilities: tuple[str, ...] = field(default_factory=tuple)
    """MCP tool slugs the agent calls. Used for least-privilege scope checks."""

    def __post_init__(self) -> None:
        if not self.slug:
            raise ValueError("AgentDef.slug is required")
        if not self.slug.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                f"AgentDef.slug must be alnum/_/-: got {self.slug!r}"
            )
        if self.cost_budget_monthly_cents <= 0:
            raise ValueError(
                "AgentDef.cost_budget_monthly_cents must be positive"
            )
        if self.agent_class == AgentClass.CONTINUOUS:
            if not any(t == "continuous" for t in self.triggers):
                raise ValueError(
                    f"agent {self.slug}: AgentClass.CONTINUOUS requires "
                    f'a "continuous" trigger entry'
                )
        if self.agent_class == AgentClass.BATCH:
            # Heuristic: BATCH agents need at least one cron-style trigger
            # (5+ space-separated fields) OR a manual-trigger sentinel.
            for trig in self.triggers:
                fields = trig.split()
                if len(fields) >= 5 or trig == "manual":
                    break
            else:
                raise ValueError(
                    f"agent {self.slug}: AgentClass.BATCH requires at least "
                    f'one cron-style trigger or "manual" sentinel'
                )
        if self.agent_class == AgentClass.EVENT_DRIVEN:
            if not any(t.startswith("event:") for t in self.triggers):
                raise ValueError(
                    f"agent {self.slug}: AgentClass.EVENT_DRIVEN requires "
                    f'at least one "event:<channel>" trigger'
                )


_REGISTRY: dict[str, AgentDef] = {}


def register_agent(agent_def: AgentDef) -> None:
    """Register an agent definition. Idempotent on (slug, version)."""
    existing = _REGISTRY.get(agent_def.slug)
    if existing is not None:
        if existing == agent_def:
            return  # idempotent re-registration
        raise ValueError(
            f"agent {agent_def.slug!r} already registered with different "
            f"definition: existing={existing!r} new={agent_def!r}"
        )
    _REGISTRY[agent_def.slug] = agent_def


def get_agent(slug: str) -> AgentDef | None:
    """Return the AgentDef for ``slug``, or None if unregistered."""
    return _REGISTRY.get(slug)


def list_agents() -> list[AgentDef]:
    """Return all registered agents sorted by slug."""
    return [_REGISTRY[slug] for slug in sorted(_REGISTRY)]


def _clear_registry_for_tests() -> None:
    """Test-only: drop all registered agents."""
    _REGISTRY.clear()
