"""Retry and DLQ policy for GraphSyncWorker."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryDecision:
    next_status: str
    delay_seconds: int
    promote_to_dlq: bool


MAX_ATTEMPTS = 5
BASE_DELAY_SECONDS = 2
MAX_DELAY_SECONDS = 300


def decide_retry(attempts_after_failure: int) -> RetryDecision:
    if attempts_after_failure >= MAX_ATTEMPTS:
        return RetryDecision(next_status="dlq", delay_seconds=0, promote_to_dlq=True)
    delay = min(MAX_DELAY_SECONDS, BASE_DELAY_SECONDS * (2 ** max(0, attempts_after_failure - 1)))
    return RetryDecision(next_status="pending", delay_seconds=delay, promote_to_dlq=False)
