from __future__ import annotations

import pytest

from contact_ops.agents.dedup.hipaa_fence import PersonRef, crosses_hipaa_fence


@pytest.mark.asyncio
class TestHipaaFence:
    async def test_same_tenant_safe(self) -> None:
        tenant = "00000000-0000-0000-0000-000000000001"
        a = PersonRef(id=tenant, tenant_id=tenant)
        b = PersonRef(id=tenant, tenant_id=tenant)
        assert await crosses_hipaa_fence(a, b) is False

    async def test_different_tenant_blocks(self) -> None:
        t1 = "00000000-0000-0000-0000-000000000001"
        t2 = "00000000-0000-0000-0000-000000000002"
        a = PersonRef(id=t1, tenant_id=t1)
        b = PersonRef(id=t2, tenant_id=t2)
        assert await crosses_hipaa_fence(a, b) is True

    async def test_hipaa_mode_independent(self) -> None:
        t1 = "00000000-0000-0000-0000-000000000001"
        t2 = "00000000-0000-0000-0000-000000000002"
        a = PersonRef(id=t1, tenant_id=t1)
        b = PersonRef(id=t2, tenant_id=t2)
        # The fence only checks tenant_id equality, not hipaa_mode
        assert await crosses_hipaa_fence(a, b) is True
