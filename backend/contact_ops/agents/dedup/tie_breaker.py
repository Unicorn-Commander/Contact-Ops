from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from contact_ops.agents.errors import CostBudgetExceededError

_PII_FIELDS: frozenset[str] = frozenset({
    "email", "phone", "dob", "ssn", "government_id",
})


@dataclass
class TieBreakerResult:
    verdict: str  # "SAME_PERSON" | "DIFFERENT_PERSON" | "UNCERTAIN"
    reason: str
    model: str
    raw_response: str
    tokens_input: int = 0
    tokens_output: int = 0
    cost_cents: int = 0
    bit_nudge: float = 0.0


TIE_BREAKER_PROMPT = """You are a contact deduplication agent. Two records are candidates for merge.
Output: SAME_PERSON | DIFFERENT_PERSON | UNCERTAIN, plus a one-sentence reason.

Record A: {a_json}
Record B: {b_json}

Cite specific fields in your reasoning."""


# Tenants with legal exemption from PII redaction.
# Populated at startup from a config table or env var.
_LEGAL_OVERRIDE_TENANTS: frozenset[str] = frozenset()


def _redact_pii(d: dict[str, Any], *, tenant_id: UUID) -> dict[str, Any]:
    """Return a deep copy of *d* with PII fields replaced by "REDACTED",
    unless *tenant_id* has a legal override.
    """
    if str(tenant_id) in _LEGAL_OVERRIDE_TENANTS:
        return d
    out = deepcopy(d)
    for key in out:
        if key.lower() in _PII_FIELDS and out[key] is not None:
            out[key] = "REDACTED"
    return out


_FALLBACK_RESPONSE = json.dumps({
    "verdict": "UNCERTAIN",
    "reason": "LLM tie-breaker unavailable (no LiteLLM gateway configured)",
})


async def run_tie_breaker(
    *,
    person_a: dict[str, Any],
    person_b: dict[str, Any],
    tenant_id: UUID,
    cost_guard=None,
    db_session=None,
    model: str = "qwen-3.6-35b-moe",
) -> TieBreakerResult:
    """Run the optional LLM tie-breaker for borderline pairs.

    Args:
        person_a: Dict of person_a's field-level summary (PII redacted for
            non-legal tenants).
        person_b: Dict of person_b's field-level summary.
        tenant_id: Tenant UUID for cost guard.
        cost_guard: Optional CostGuard instance for budget enforcement.
        db_session: Optional DB session for cost recording.
        model: LiteLLM model slug.

    Returns:
        TieBreakerResult with verdict, reason, and bit_nudge.
        bit_nudge is +2 for SAME_PERSON, -2 for DIFFERENT_PERSON, 0 for
        UNCERTAIN.

    The LLM's verdict contributes a small bit nudge but NEVER overrides
    the Fellegi-Sunter posterior.
    """
    a_redacted = _redact_pii(person_a, tenant_id=tenant_id)
    b_redacted = _redact_pii(person_b, tenant_id=tenant_id)

    a_json = json.dumps(a_redacted, sort_keys=True, default=str)
    b_json = json.dumps(b_redacted, sort_keys=True, default=str)
    prompt = TIE_BREAKER_PROMPT.format(a_json=a_json, b_json=b_json)

    estimated_tokens = len(prompt.split()) + 50
    estimated_cents = 1

    if cost_guard is not None:
        try:
            await cost_guard.check_and_record(
                agent_slug="dedup-tiebreaker",
                tenant_id=tenant_id,
                estimated_tokens=estimated_tokens,
                estimated_cents=estimated_cents,
            )
        except CostBudgetExceededError:
            return TieBreakerResult(
                verdict="UNCERTAIN",
                reason="Cost budget exceeded",
                model=model,
                raw_response="",
                bit_nudge=0.0,
            )

    try:
        import litellm  # pyright: ignore[reportMissingImports]

        has_litellm = True
    except ImportError:
        has_litellm = False

    if has_litellm:
        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=100,
        )
        raw_text: str = response.choices[0].message.content or ""
        tokens_input: int = (
            response.usage.prompt_tokens
            if hasattr(response.usage, "prompt_tokens")
            else 0
        )
        tokens_output: int = (
            response.usage.completion_tokens
            if hasattr(response.usage, "completion_tokens")
            else 0
        )
        cost_cents: int = estimated_cents
    else:
        raw_text = _FALLBACK_RESPONSE
        tokens_input = 0
        tokens_output = 0
        cost_cents = 0

    verdict: str = "UNCERTAIN"
    for token in ("SAME_PERSON", "DIFFERENT_PERSON", "UNCERTAIN"):
        if token in raw_text:
            verdict = token
            break

    if verdict in raw_text:
        parts = raw_text.split(verdict, 1)
        reason: str = parts[1].strip().lstrip(",;:\n")
    else:
        reason = raw_text.strip()

    nudge_map = {"SAME_PERSON": 2.0, "DIFFERENT_PERSON": -2.0, "UNCERTAIN": 0.0}
    bit_nudge: float = nudge_map[verdict]

    if cost_guard is not None and has_litellm:
        cost_guard.record_call(cents=cost_cents)

    return TieBreakerResult(
        verdict=verdict,
        reason=reason,
        model=model,
        raw_response=raw_text,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        cost_cents=cost_cents,
        bit_nudge=bit_nudge,
    )
