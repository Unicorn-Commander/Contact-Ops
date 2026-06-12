"""HTTP request metrics middleware (P-00075 ops).

Records request count / duration / in-flight gauge into the EXISTING dedicated
Prometheus registry (agents/observability/metrics.py), exposed at /metrics.

Cardinality discipline: labels use the matched route TEMPLATE
(request.scope["route"].path_format, e.g. "/api/people/{person_id}"), never the
raw path — id-heavy raw paths would explode the time series. Unmatched requests
(404s, etc.) bucket under "__unmatched__" so a scanner can't mint unbounded
labels. No tenant id label: /metrics is unauthenticated.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from contact_ops.agents.observability.metrics import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_IN_PROGRESS,
    HTTP_REQUESTS_TOTAL,
)

# Don't measure the scrape endpoint or health probes — they're high-frequency
# infra noise that would dominate the histograms.
_SKIP_PATHS = frozenset({"/metrics", "/health", "/health/ready", "/health/live"})


def _route_template(request: Request) -> str:
    """The matched route template, or a bounded fallback for unmatched routes."""
    route = request.scope.get("route")
    template = getattr(route, "path_format", None)
    return template or "__unmatched__"


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        method = request.method
        start = time.perf_counter()
        status = "500"  # default if the handler raises before producing a response
        HTTP_REQUESTS_IN_PROGRESS.labels(method=method).inc()
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            # Route template is only resolvable after call_next has run the router.
            template = _route_template(request)
            elapsed = time.perf_counter() - start
            HTTP_REQUESTS_IN_PROGRESS.labels(method=method).dec()
            HTTP_REQUESTS_TOTAL.labels(method=method, path=template, status=status).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=template).observe(elapsed)
