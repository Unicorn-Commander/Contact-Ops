"""Agent registry tests (in-process)."""

from __future__ import annotations

import pytest

from contact_ops.agents.registry import (
    AgentClass,
    AgentDef,
    Reversibility,
    _clear_registry_for_tests,
    get_agent,
    list_agents,
    register_agent,
)
from contact_ops.agents.trust import TrustTier


@pytest.fixture(autouse=True)
def _clear_registry():
    _clear_registry_for_tests()
    yield
    _clear_registry_for_tests()


def _example_def(slug: str = "dedup") -> AgentDef:
    return AgentDef(
        slug=slug,
        name="Dedup Agent",
        version="1.0.0",
        agent_class=AgentClass.BATCH,
        description="Probabilistic record linkage via Splink",
        cost_budget_monthly_cents=2000,
        initial_trust_tier=TrustTier.T0_PROBATION,
        triggers=("0 */6 * * *",),
        declared_capabilities=("contacts.list_recent", "qdrant.search"),
    )


def test_register_and_get():
    d = _example_def()
    register_agent(d)
    assert get_agent("dedup") == d


def test_get_unknown_returns_none():
    assert get_agent("nonexistent") is None


def test_register_idempotent_on_identical_def():
    d = _example_def()
    register_agent(d)
    register_agent(d)  # no exception
    assert get_agent("dedup") == d


def test_register_rejects_conflicting_definition():
    register_agent(_example_def())
    different = AgentDef(
        slug="dedup",
        name="Dedup Agent v2",
        version="2.0.0",
        agent_class=AgentClass.BATCH,
        description="changed",
        cost_budget_monthly_cents=3000,
        initial_trust_tier=TrustTier.T0_PROBATION,
        triggers=("0 */6 * * *",),
    )
    with pytest.raises(ValueError, match="already registered"):
        register_agent(different)


def test_list_returns_sorted_by_slug():
    register_agent(_example_def("zebra"))
    register_agent(_example_def("alpha"))
    register_agent(_example_def("mango"))
    slugs = [a.slug for a in list_agents()]
    assert slugs == ["alpha", "mango", "zebra"]


def test_agentdef_rejects_empty_slug():
    with pytest.raises(ValueError):
        AgentDef(
            slug="",
            name="x",
            version="1",
            agent_class=AgentClass.BATCH,
            description="x",
            cost_budget_monthly_cents=1,
            initial_trust_tier=TrustTier.T0_PROBATION,
            triggers=("manual",),
        )


def test_agentdef_rejects_invalid_slug_chars():
    with pytest.raises(ValueError):
        AgentDef(
            slug="bad slug!",
            name="x",
            version="1",
            agent_class=AgentClass.BATCH,
            description="x",
            cost_budget_monthly_cents=1,
            initial_trust_tier=TrustTier.T0_PROBATION,
            triggers=("manual",),
        )


def test_agentdef_rejects_negative_budget():
    with pytest.raises(ValueError):
        AgentDef(
            slug="x",
            name="x",
            version="1",
            agent_class=AgentClass.BATCH,
            description="x",
            cost_budget_monthly_cents=0,
            initial_trust_tier=TrustTier.T0_PROBATION,
            triggers=("manual",),
        )


def test_agentdef_continuous_requires_continuous_trigger():
    with pytest.raises(ValueError, match="continuous"):
        AgentDef(
            slug="lifecycle",
            name="Lifecycle",
            version="1",
            agent_class=AgentClass.CONTINUOUS,
            description="x",
            cost_budget_monthly_cents=1,
            initial_trust_tier=TrustTier.T0_PROBATION,
            triggers=("0 0 * * *",),
        )


def test_agentdef_batch_requires_cron_or_manual():
    with pytest.raises(ValueError, match="BATCH"):
        AgentDef(
            slug="dedup",
            name="Dedup",
            version="1",
            agent_class=AgentClass.BATCH,
            description="x",
            cost_budget_monthly_cents=1,
            initial_trust_tier=TrustTier.T0_PROBATION,
            triggers=("event:contacts.inserted",),
        )


def test_agentdef_event_driven_requires_event_trigger():
    with pytest.raises(ValueError, match="EVENT_DRIVEN"):
        AgentDef(
            slug="voice-match",
            name="Voice Match",
            version="1",
            agent_class=AgentClass.EVENT_DRIVEN,
            description="x",
            cost_budget_monthly_cents=1,
            initial_trust_tier=TrustTier.T0_PROBATION,
            triggers=("0 */6 * * *",),
        )


def test_reversibility_enum_values():
    assert Reversibility.REVERSIBLE.value == "reversible"
    assert Reversibility.IRREVERSIBLE.value == "irreversible"
    assert Reversibility.SOFT_DELETE.value == "soft_delete"


def test_agent_class_enum_values():
    assert AgentClass.BATCH.value == "batch"
    assert AgentClass.EVENT_DRIVEN.value == "event"
    assert AgentClass.CONTINUOUS.value == "continuous"
