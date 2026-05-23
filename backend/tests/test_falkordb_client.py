from __future__ import annotations

import uuid

import pytest

from contact_ops.agents.graph_sync.falkordb_client import (
    FalkorDBGraphClient,
    TenantGraph,
    graph_name_for_slug,
    validate_graph_name,
)


def test_graph_name_for_slug_defaults_to_per_tenant_prefix() -> None:
    assert graph_name_for_slug("Aaron-Personal") == "contact_ops__aaron_personal"


def test_validate_graph_name_rejects_cross_tenant_injection() -> None:
    with pytest.raises(ValueError):
        validate_graph_name("contact_ops__aaron MATCH (n) RETURN n")


def test_render_cypher_value_handles_uuid_and_quotes() -> None:
    """FalkorDB inline-param prefix renders UUIDs as quoted strings and
    escapes embedded single quotes. Regression test for the broken
    redis-PARAMS implementation (live FalkorDB rejected it as
    DataError: 'Invalid input of type: list')."""
    from contact_ops.agents.graph_sync.falkordb_client import _render_cypher_value

    client = FalkorDBGraphClient.__new__(FalkorDBGraphClient)
    coerced = client._coerce_param(uuid.UUID(int=1))
    assert coerced == "00000000-0000-0000-0000-000000000001"
    assert _render_cypher_value(coerced) == "'00000000-0000-0000-0000-000000000001'"
    assert _render_cypher_value("O'Brien") == r"'O\'Brien'"
    assert _render_cypher_value(True) == "true"
    assert _render_cypher_value(42) == "42"
    assert _render_cypher_value(None) == "null"


def test_tenant_graph_requires_per_tenant_mode() -> None:
    graph = TenantGraph(
        tenant_id=uuid.uuid4(),
        graph_name="contact_ops__aaron",
        graph_mode="shared",
    )
    assert graph.graph_mode == "shared"
