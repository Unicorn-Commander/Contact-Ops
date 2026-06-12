"""Security-headers middleware — attaches constant hardening headers to every response.

A purely additive, dormant-safe response shaper: it appends a fixed set of
precomputed security headers to EVERY response, including error responses the
JWT middleware short-circuits with (a 401 still gets ``X-Content-Type-Options``
etc.). It reads no per-request input, opens no connection, and never blocks —
so it can sit anywhere in the stack without a latency or correctness cost.

Dormant / shadow-first posture (mirrors the entitlement + email modules):
- The whole middleware is added in ``main.py`` ONLY when
  ``SECURITY_HEADERS_ENABLED`` is set, so the module itself does not re-check
  that flag.
- ``SECURITY_HEADERS_SHADOW_MODE`` (default True) gives the one safe escape
  hatch this kind of module can have: a header like CSP or
  Cross-Origin-Opener-Policy CAN break a browser client, so in shadow mode the
  middleware computes the exact header set it WOULD attach, logs a single
  ``security_headers_would_set`` event with that set, and attaches NOTHING.
  Flip shadow off to actually enforce. The log lets you confirm the values on
  real traffic before pinning them.

Tuning the middleware reads from the ``Settings`` object passed to its
constructor:
- ``CONTENT_SECURITY_POLICY`` — CSP value, applied ONLY to ``text/html``
  responses (the API serves JSON; HTML is the only thing a CSP protects, and a
  default-deny CSP on JSON is pointless noise). Default is a hard deny.
- ``SECURITY_HSTS_ENABLED`` — when True, add Strict-Transport-Security. OFF by
  default so we never pin HTTPS in dev / self-host running over plain http.
- ``SECURITY_HEADERS_SHADOW_MODE`` — see above.

All non-CSP, non-HSTS headers are CONSTANT and precomputed once at construction.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from contact_ops.core.config import Settings

logger = structlog.get_logger(__name__)

# Constant baseline headers — safe on every response (JSON, HTML, or an error
# body) and cheap to copy. CSP and HSTS are handled separately because they are
# conditional (content-type / settings gated).
_BASE_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    # Lock down the high-risk powerful features the API never uses. Empty
    # allow-list = deny for every origin (including self).
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

# 2 years, in seconds. Only emitted when SECURITY_HSTS_ENABLED.
_HSTS_VALUE = "max-age=63072000; includeSubDomains"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Append constant security headers to every response.

    Additive only: never inspects the request body, never blocks, never opens a
    connection. In shadow mode it logs the header set it would have applied and
    attaches nothing, so a potentially client-breaking header (CSP / COOP) can be
    validated against real traffic before enforcement.
    """

    def __init__(self, app: Any, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings
        self.shadow_mode: bool = bool(
            getattr(settings, "SECURITY_HEADERS_SHADOW_MODE", True)
        )
        self.hsts_enabled: bool = bool(getattr(settings, "SECURITY_HSTS_ENABLED", False))
        # Precompute the full constant header set once. CSP is per-response
        # (content-type gated) so it is NOT baked in here.
        self._constant_headers: dict[str, str] = dict(_BASE_SECURITY_HEADERS)
        if self.hsts_enabled:
            self._constant_headers["Strict-Transport-Security"] = _HSTS_VALUE
        self._csp_value: str = (
            getattr(settings, "CONTENT_SECURITY_POLICY", None)
            or "default-src 'none'; frame-ancestors 'none'"
        )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)

        # Build the exact set we intend to attach for THIS response (CSP only on
        # HTML). Computed the same way in shadow + enforce so the log is truthful.
        to_set: dict[str, str] = dict(self._constant_headers)
        if self._is_html(response):
            to_set["Content-Security-Policy"] = self._csp_value

        if self.shadow_mode:
            # Shadow-first: log what we WOULD attach and leave the response
            # untouched, so a CSP/COOP value can be vetted on live traffic
            # before it can break a browser client.
            logger.info(
                "security_headers_would_set",
                path=str(request.url.path),
                status_code=response.status_code,
                headers=sorted(to_set.keys()),
            )
            return response

        for name, value in to_set.items():
            response.headers[name] = value
        return response

    @staticmethod
    def _is_html(response: Response) -> bool:
        """True when the response declares a text/html content type.

        The API serves JSON, so CSP is only meaningful (and only attached) on the
        rare HTML response. Matches on the media type prefix so a charset
        parameter (``text/html; charset=utf-8``) still counts.
        """
        content_type = response.headers.get("content-type", "")
        return content_type.split(";", 1)[0].strip().lower() == "text/html"
