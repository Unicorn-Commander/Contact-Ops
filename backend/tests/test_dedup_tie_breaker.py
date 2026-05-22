from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from contact_ops.agents.errors import CostBudgetExceededError
from contact_ops.agents.dedup.tie_breaker import run_tie_breaker


def _mock_acompletion(content: str) -> AsyncMock:
    return AsyncMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content=content))],
            usage=MagicMock(prompt_tokens=50, completion_tokens=10),
        )
    )


@pytest.mark.asyncio
class TestTieBreaker:
    TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

    @patch("litellm.acompletion", new_callable=AsyncMock)
    async def test_tie_breaker_same_person(self, mock_acompletion: AsyncMock) -> None:
        mock_acompletion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="SAME_PERSON, records match on email and phone."))],
            usage=MagicMock(prompt_tokens=50, completion_tokens=10),
        )
        result = await run_tie_breaker(
            person_a={"name": "Alice", "email": "REMOVED"},
            person_b={"name": "Alice", "email": "REMOVED"},
            tenant_id=self.TENANT_ID,
        )
        assert result.verdict == "SAME_PERSON"
        assert result.bit_nudge == 2.0

    @patch("litellm.acompletion", new_callable=AsyncMock)
    async def test_tie_breaker_different_person(self, mock_acompletion: AsyncMock) -> None:
        mock_acompletion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="DIFFERENT_PERSON, names and emails don't match."))],
            usage=MagicMock(prompt_tokens=50, completion_tokens=10),
        )
        result = await run_tie_breaker(
            person_a={"name": "Alice", "email": "REMOVED"},
            person_b={"name": "Bob", "email": "REMOVED"},
            tenant_id=self.TENANT_ID,
        )
        assert result.verdict == "DIFFERENT_PERSON"
        assert result.bit_nudge == -2.0

    @patch("litellm.acompletion", new_callable=AsyncMock)
    async def test_tie_breaker_uncertain(self, mock_acompletion: AsyncMock) -> None:
        mock_acompletion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="UNCERTAIN, insufficient evidence."))],
            usage=MagicMock(prompt_tokens=50, completion_tokens=10),
        )
        result = await run_tie_breaker(
            person_a={"name": "Alice"},
            person_b={"name": "Bob"},
            tenant_id=self.TENANT_ID,
        )
        assert result.verdict == "UNCERTAIN"
        assert result.bit_nudge == 0.0

    @patch("litellm.acompletion", new_callable=AsyncMock)
    async def test_tie_breaker_cost_guard_fires(self, mock_acompletion: AsyncMock) -> None:
        mock_cost_guard = AsyncMock()
        mock_cost_guard.check_and_record = AsyncMock(
            side_effect=CostBudgetExceededError(
                layer=3,
                message="Monthly budget exhausted",
                agent_slug="dedup-tiebreaker",
                tenant_id=str(self.TENANT_ID),
            )
        )
        result = await run_tie_breaker(
            person_a={"name": "Alice"},
            person_b={"name": "Bob"},
            tenant_id=self.TENANT_ID,
            cost_guard=mock_cost_guard,
        )
        assert result.verdict == "UNCERTAIN"
        assert result.bit_nudge == 0.0
        mock_acompletion.assert_not_called()

    @patch("litellm.acompletion", new_callable=AsyncMock)
    async def test_tie_breaker_pii_redacted(self, mock_acompletion: AsyncMock) -> None:
        mock_acompletion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="SAME_PERSON"))],
            usage=MagicMock(prompt_tokens=50, completion_tokens=5),
        )
        await run_tie_breaker(
            person_a={"name": "Alice", "email": "REMOVED", "phone": "555-0100"},
            person_b={"name": "Bob", "email": "REMOVED", "phone": "555-0199"},
            tenant_id=self.TENANT_ID,
        )
        call_args = mock_acompletion.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "REDACTED" in prompt
        assert "REMOVED" not in prompt
