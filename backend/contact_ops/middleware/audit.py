"""Audit middleware — writes one `action_event` row per authenticated request.

The single load-bearing promise per design doc §2.7 + §3.6: every state-changing
operation is recorded as an immutable event with full provenance. For HTTP-level
audit (this middleware), we record the request itself as a generic
`http.<METHOD> <path>` event. MCP tool-level audit (when Phase 1 tools land)
will write more structured events from inside the tool handlers.

Writes are:
- synchronous (inside dispatch), so audit can't be lost on graceful shutdown
- via the `contact_ops_audit` role (separate connection pool), so a future
  tightening of the app role's privileges on `action_event` doesn't break
  the audit path
- targeting the `action_event` table in the public schema (NOT a `contact_ops.*`
  schema — the migrations don't create that)
- including every NOT NULL column the schema mandates
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

import structlog
from fastapi import Request, Response
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from contact_ops.agents.observability.metrics import AUDIT_WRITE_FAILURES_TOTAL
from contact_ops.core.database import get_audit_db_context

logger = structlog.get_logger(__name__)


class AuditMiddleware(BaseHTTPMiddleware):
    """Write an `action_event` row per authenticated request.

    Skipped for: unauthenticated paths (health probes, docs, OPTIONS) and
    requests whose JWT validation hasn't populated `request.state.jwt_claims`
    (the JWT middleware would have already 401'd anything unauthenticated, so
    if we reach here without claims, the request is going to a SKIP_PATHS
    endpoint anyway).
    """

    SKIP_PATHS = frozenset({
        "/health",
        "/health/live",
        "/health/ready",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/",
        "/metrics",
    })

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in self.SKIP_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = round((time.monotonic() - start) * 1000, 2)

        # Audit only requests that carried a validated JWT. The JWT middleware
        # is the gatekeeper; this is a belt-and-suspenders check.
        claims = getattr(request.state, "jwt_claims", None)
        if not claims:
            return response

        await self._record(request, response, claims, elapsed_ms)
        return response

    async def _record(
        self,
        request: Request,
        response: Response,
        claims: dict[str, Any],
        elapsed_ms: float,
    ) -> None:
        try:
            tenant_id = claims.get("tenant_id")
            if not tenant_id:
                # JWT middleware should have rejected; defensive skip.
                return

            # uc_uid is normalized (falls back to `sub`) in
            # JWTValidationMiddleware; bind it on the audit session too so the
            # row is attributed and so the session matches the app session's
            # context. tenant_id is the load-bearing one for the
            # `ae_audit_insert` WITH CHECK once the audit role is RLS-subject.
            uc_uid = claims.get("uc_uid") or claims.get("sub")

            actor_chain = getattr(request.state, "actor_chain", {"sub": claims.get("sub")})
            human_authority = getattr(request.state, "human_authority", claims.get("sub")) or ""

            event_type = f"http.{request.method} {request.url.path}"
            payload: dict[str, Any] = {
                "method": request.method,
                "path": str(request.url.path),
                "query": str(request.url.query) if request.url.query else None,
                "status_code": response.status_code,
                "duration_ms": elapsed_ms,
                "client_host": request.client.host if request.client else None,
                "user_agent": request.headers.get("user-agent"),
                "request_id": str(
                    getattr(request.state, "request_id", "")
                    or request.headers.get("x-request-id")
                    or ""
                ),
            }
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            content_hash = hashlib.sha256(payload_json.encode("utf-8")).digest()

            # HTTP-level audit doesn't have a natural aggregate; use a generated
            # UUID. Tool-level audit (Phase 1) will use the actual entity_id
            # of the touched record.
            aggregate_id = uuid.uuid4()
            event_status = "applied" if 200 <= response.status_code < 400 else "rejected"

            async with get_audit_db_context(
                tenant_id=str(tenant_id), uc_uid=uc_uid
            ) as db:
                await db.execute(
                    text(
                        """
                        INSERT INTO action_event (
                            event_type, event_version,
                            tenant_id, aggregate_type, aggregate_id,
                            payload, actor, actor_type,
                            human_authority,
                            evidence, status,
                            content_hash
                        ) VALUES (
                            :event_type, 1,
                            CAST(:tenant_id AS uuid), 'interaction'::entity_kind,
                            CAST(:aggregate_id AS uuid),
                            CAST(:payload AS jsonb), CAST(:actor AS jsonb), 'human'::actor_type,
                            CAST(:human_authority AS uuid),
                            CAST(:evidence AS jsonb), CAST(:status AS event_status),
                            :content_hash
                        )
                        """
                    ),
                    {
                        "event_type": event_type,
                        "tenant_id": str(tenant_id),
                        "aggregate_id": str(aggregate_id),
                        "payload": payload_json,
                        "actor": json.dumps(actor_chain, sort_keys=True, separators=(",", ":")),
                        "human_authority": str(human_authority) if human_authority else None,
                        "evidence": json.dumps(
                            {"source": "http_audit",
                             "client_id": claims.get("azp") or claims.get("aud")},
                            sort_keys=True, separators=(",", ":"),
                        ),
                        "status": event_status,
                        "content_hash": content_hash,
                    },
                )
        except Exception as exc:
            # Audit must not break the response, but this must page: a failed
            # audit write breaks the product's provenance contract.
            AUDIT_WRITE_FAILURES_TOTAL.labels(
                tenant=str(claims.get("tenant_id") or "unknown")
            ).inc()
            logger.error(
                "audit_write_failed",
                error=str(exc),
                path=str(request.url.path),
                tenant_id=str(claims.get("tenant_id")) if claims.get("tenant_id") else None,
            )
