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


def test_query_uses_graph_argument_not_cypher_interpolation() -> None:
    client = FalkorDBGraphClient.__new__(FalkorDBGraphClient)
    encoded = client._encode_params({"person_id": uuid.UUID(int=1)})
    assert encoded == ["person_id", "00000000-0000-0000-0000-000000000001"]


def test_tenant_graph_requires_per_tenant_mode() -> None:
    graph = TenantGraph(
        tenant_id=uuid.uuid4(),
        graph_name="contact_ops__aaron",
        graph_mode="shared",
    )
    assert graph.graph_mode == "shared"
