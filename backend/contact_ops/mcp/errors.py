"""Stable MCP tool error catalog.

Tool handlers raise ``ToolError`` for application-level failures. The JSON-RPC
transport remains successful; the MCP tool result carries ``isError: true`` and
``structuredContent`` with these stable codes.
"""
# ruff: noqa: I001

from __future__ import annotations


PERSON_NOT_FOUND = "PERSON_NOT_FOUND"
ORG_NOT_FOUND = "ORG_NOT_FOUND"
AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
VALIDATION_FAILED = "VALIDATION_FAILED"
INSUFFICIENT_ROLE = "INSUFFICIENT_ROLE"
INSUFFICIENT_SCOPE = "INSUFFICIENT_SCOPE"
ENTITLEMENT_REQUIRED = "ENTITLEMENT_REQUIRED"
QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
TENANT_MISMATCH = "TENANT_MISMATCH"
PERSON_MERGED = "PERSON_MERGED"
LEGAL_HOLD = "LEGAL_HOLD"
HIPAA_BLOCKED = "HIPAA_BLOCKED"
DUPLICATE_RECORD = "DUPLICATE_RECORD"
DEPENDENCY_VIOLATION = "DEPENDENCY_VIOLATION"
IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"

# Tool-specific codes from design doc §5 used by Phase 1 tools.
STALE_ETAG = "STALE_ETAG"
NO_FILTER_PROVIDED = "NO_FILTER_PROVIDED"
INVALID_CURSOR = "INVALID_CURSOR"
BATCH_TOO_LARGE = "BATCH_TOO_LARGE"
EDGE_NOT_FOUND = "EDGE_NOT_FOUND"
ALREADY_ENDED = "ALREADY_ENDED"
CONFIRMATION_PHRASE_MISMATCH = "CONFIRMATION_PHRASE_MISMATCH"
RELATIONSHIP_ALREADY_EXISTS = "RELATIONSHIP_ALREADY_EXISTS"


class ToolError(Exception):
    """Application-level MCP tool failure."""

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.hint = hint


def error_result(error: ToolError) -> dict[str, object]:
    """Format a ``ToolError`` as an MCP tool result."""

    structured: dict[str, object] = {
        "code": error.code,
        "retryable": error.retryable,
    }
    if error.hint is not None:
        structured["hint"] = error.hint
    return {
        "content": [{"type": "text", "text": error.message}],
        "isError": True,
        "structuredContent": structured,
    }
