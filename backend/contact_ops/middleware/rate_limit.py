"""Redis-backed fixed-window rate-limit middleware.

DORMANT-SAFE + SHADOW-FIRST, mirroring the entitlement gate
(:mod:`contact_ops.mcp.entitlement`) and the dormant email factory:

  * The middleware is only ADDED to the app when ``settings.RATE_LIMIT_ENABLED``
    is true (wiring lives in ``main.py``; this module never self-mounts).
  * Even once mounted it is SHADOW-FIRST: when ``settings.RATE_LIMIT_SHADOW``
    (default True) it logs a ``rate_limit_would_block`` event with the resolved
    key + count and ALLOWS the request. It only returns a real ``429`` once the
    shadow flag is flipped off. This lets real traffic shape the chosen limit in
    the logs before anything is ever rejected.

Identity & trust model
----------------------
The limit key is the authenticated subject when present
(``request.state.jwt_claims['sub']``, set upstream by
:class:`JWTValidationMiddleware`) so a logged-in user is limited as one identity
regardless of source IP, and falls back to the client IP for unauthenticated
traffic (e.g. CardDAV — though CardDAV is exempt here, see below).

Behind Traefik, ``request.client.host`` is the proxy, so a client-supplied
``X-Forwarded-For`` is only honored when the immediate peer
(``request.client.host``) is in ``settings.TRUSTED_PROXY_IPS``. Otherwise the
header is ignored and the real peer address is used — an untrusted client can
never spoof its way into a different bucket.

Fail-open
---------
Rate limiting is a guardrail, never a hard dependency: if Redis is unavailable
or any limiter step raises, the request is ALLOWED and a warning is logged. A
broken limiter must never 500 real traffic.

Exempt paths
------------
``/health*``, ``/metrics``, and the CardDAV surface (``/carddav*`` +
``/.well-known/carddav``). CardDAV has its own limiter in
:mod:`contact_ops.carddav`; double-limiting it here would penalize iOS
Contacts.app sync bursts twice.
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from contact_ops.core.config import Settings
from contact_ops.core.redis import RedisUnavailableError, get_redis

logger = structlog.get_logger(__name__)

# Atomic fixed-window increment: bump the counter and, on the FIRST hit in a
# window, set its expiry to the window length (+1s slack so the key always
# outlives the window it represents). Returns the post-increment count. Doing
# the INCR+EXPIRE in one server-side script avoids a race where a crash between
# the two commands could leave a counter with no TTL (a permanently-stuck key).
_FIXED_WINDOW_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""

# Window unit -> seconds. Accepts singular/plural and a couple of short forms so
# a config string like "120/minute", "1000/hour" or "5/sec" all parse.
_WINDOW_UNITS: dict[str, int] = {
    "second": 1,
    "seconds": 1,
    "sec": 1,
    "s": 1,
    "minute": 60,
    "minutes": 60,
    "min": 60,
    "m": 60,
    "hour": 3600,
    "hours": 3600,
    "hr": 3600,
    "h": 3600,
    "day": 86400,
    "days": 86400,
    "d": 86400,
}

# Fallback when RATE_LIMIT_DEFAULT can't be parsed. Generous on purpose: a
# misconfigured limit string must not accidentally throttle everyone.
_FALLBACK_LIMIT = 120
_FALLBACK_WINDOW = 60


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window, Redis-backed request rate limiter (dormant + shadow-first).

    Constructed with ``(app, settings)`` and added in ``main.py`` ONLY when
    ``settings.RATE_LIMIT_ENABLED`` is true.
    """

    SKIP_PATHS = frozenset(
        {
            "/health",
            "/health/live",
            "/health/ready",
            "/metrics",
        }
    )

    # Prefix bypass. CardDAV self-limits (per-device sync bursts), so it must not
    # be double-counted here. Health endpoints may grow suffixed variants
    # (``/health/...``) so they get prefix treatment too.
    SKIP_PREFIXES: tuple[str, ...] = (
        "/health",
        "/carddav",
        "/.well-known/carddav",
    )

    def __init__(self, app: Any, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings
        self._limit, self._window = _parse_rate(
            getattr(settings, "RATE_LIMIT_DEFAULT", None)
        )
        # Normalize the trusted-proxy list once into a set for O(1) membership.
        trusted = getattr(settings, "TRUSTED_PROXY_IPS", None) or []
        self._trusted_proxies: frozenset[str] = frozenset(
            str(ip).strip() for ip in trusted if str(ip).strip()
        )
        self._shadow: bool = bool(getattr(settings, "RATE_LIMIT_SHADOW", True))

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        if (
            request.method == "OPTIONS"
            or path in self.SKIP_PATHS
            or any(
                path == p or path.startswith(p + "/") for p in self.SKIP_PREFIXES
            )
        ):
            return await call_next(request)

        key = self._resolve_key(request)
        now = int(time.time())
        window_bucket = now // self._window
        # Reset epoch for this bucket (when the count drops back to 0).
        reset_at = (window_bucket + 1) * self._window
        redis_key = f"co:rl:{key}:{window_bucket}"

        count = await self._incr(redis_key)
        if count is None:
            # Redis down / errored — fail open. Already warned in _incr().
            return await call_next(request)

        remaining = max(self._limit - count, 0)
        over_limit = count > self._limit

        if over_limit:
            retry_after = max(reset_at - now, 1)
            if self._shadow:
                logger.warning(
                    "rate_limit_would_block",
                    key=key,
                    path=path,
                    method=request.method,
                    count=count,
                    limit=self._limit,
                    window_s=self._window,
                    retry_after_s=retry_after,
                )
                # Shadow mode: ALLOW, but still annotate the response so the
                # limit is observable end-to-end before enforcement.
                response = await call_next(request)
                _apply_headers(
                    response, self._limit, remaining, reset_at, retry_after=None
                )
                return response

            logger.info(
                "rate_limit_blocked",
                key=key,
                path=path,
                method=request.method,
                count=count,
                limit=self._limit,
                window_s=self._window,
                retry_after_s=retry_after,
            )
            return _too_many_requests(
                self._limit, remaining, reset_at, retry_after
            )

        response = await call_next(request)
        _apply_headers(response, self._limit, remaining, reset_at, retry_after=None)
        return response

    # ---------- identity / key resolution ----------

    def _resolve_key(self, request: Request) -> str:
        """Bucket key: authenticated subject if present, else client IP."""
        claims = getattr(request.state, "jwt_claims", None)
        if isinstance(claims, dict):
            sub = claims.get("sub")
            if sub:
                return f"sub:{sub}"
        return f"ip:{self._client_ip(request)}"

    def _client_ip(self, request: Request) -> str:
        """Resolve the caller IP, honoring X-Forwarded-For only from a trusted peer.

        ``request.client.host`` is the immediate TCP peer. Behind Traefik that's
        the proxy, so we read the first hop of ``X-Forwarded-For`` ONLY when that
        peer is in the trusted set; otherwise an untrusted client could set the
        header to land in (or evade) an arbitrary bucket.
        """
        peer = request.client.host if request.client else None
        if peer and peer in self._trusted_proxies:
            xff = request.headers.get("x-forwarded-for")
            if xff:
                first = xff.split(",")[0].strip()
                if first:
                    return first
        return peer or "unknown"

    # ---------- redis ----------

    async def _incr(self, redis_key: str) -> int | None:
        """Atomically increment + expire the window counter.

        Returns the post-increment count, or ``None`` if Redis is unavailable
        (fail open).
        """
        try:
            redis = await get_redis()
            result = await redis.eval(
                _FIXED_WINDOW_LUA, 1, redis_key, str(self._window)
            )
            return int(result)
        except RedisUnavailableError:
            logger.warning("rate_limit_redis_unavailable", key=redis_key)
            return None
        except Exception as exc:  # noqa: BLE001 - guardrail must never 500
            logger.warning(
                "rate_limit_redis_error", key=redis_key, error=str(exc)
            )
            return None


# ---------- helpers (module level so they're easy to test) ----------


def _parse_rate(raw: Any) -> tuple[int, int]:
    """Parse a ``"<count>/<window>"`` string into ``(count, window_seconds)``.

    Accepts forms like ``"120/minute"``, ``"1000/hour"``, ``"5/second"``,
    ``"500/15minutes"`` (a leading multiplier on the unit). Falls back to a
    generous default on anything unparseable so a typo never throttles everyone.
    """
    if not raw or not isinstance(raw, str):
        return _FALLBACK_LIMIT, _FALLBACK_WINDOW

    parts = raw.strip().split("/", 1)
    if len(parts) != 2:
        logger.warning("rate_limit_unparseable", value=raw)
        return _FALLBACK_LIMIT, _FALLBACK_WINDOW

    count_str, window_str = parts[0].strip(), parts[1].strip().lower()
    try:
        count = int(count_str)
    except ValueError:
        logger.warning("rate_limit_unparseable", value=raw)
        return _FALLBACK_LIMIT, _FALLBACK_WINDOW
    if count <= 0:
        logger.warning("rate_limit_nonpositive_count", value=raw)
        return _FALLBACK_LIMIT, _FALLBACK_WINDOW

    # Optional leading integer multiplier on the unit, e.g. "15minutes".
    multiplier = 1
    unit = window_str
    idx = 0
    while idx < len(window_str) and window_str[idx].isdigit():
        idx += 1
    if idx > 0:
        try:
            multiplier = int(window_str[:idx])
        except ValueError:
            multiplier = 1
        unit = window_str[idx:].strip()

    unit_seconds = _WINDOW_UNITS.get(unit)
    if unit_seconds is None or multiplier <= 0:
        logger.warning("rate_limit_unknown_window", value=raw)
        return _FALLBACK_LIMIT, _FALLBACK_WINDOW

    window = unit_seconds * multiplier
    return count, max(window, 1)


def _apply_headers(
    response: Response,
    limit: int,
    remaining: int,
    reset_at: int,
    *,
    retry_after: int | None,
) -> None:
    """Attach IETF-draft RateLimit headers to a response.

    ``RateLimit-Reset`` is expressed as delta-seconds (per the draft) from now.
    """
    now = int(time.time())
    response.headers["RateLimit-Limit"] = str(limit)
    response.headers["RateLimit-Remaining"] = str(max(remaining, 0))
    response.headers["RateLimit-Reset"] = str(max(reset_at - now, 0))
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)


def _too_many_requests(
    limit: int, remaining: int, reset_at: int, retry_after: int
) -> Response:
    response = Response(
        content=b'{"detail":"rate limit exceeded"}',
        status_code=429,
        media_type="application/json",
    )
    _apply_headers(response, limit, remaining, reset_at, retry_after=retry_after)
    return response
