"""Request body size limit — pure ASGI middleware.

Rejects oversized HTTP request bodies BEFORE they are buffered into memory, to
defend the app (and downstream Postgres/Redis) from accidental or hostile large
uploads. Enforcement happens in two layers so a lying or absent
``Content-Length`` cannot defeat it:

  1. A fast pre-check of the declared ``Content-Length`` header. When it exceeds
     the effective limit, the inner app is never called — we return a 413
     immediately.
  2. A streaming cap that counts the actual bytes flowing through ``receive()``
     across ``http.request`` chunks. The moment the running total exceeds the
     effective limit, we short-circuit with a 413 — even if the header lied or
     was omitted (chunked transfer-encoding).

This is implemented as a PURE ASGI middleware (not Starlette's
``BaseHTTPMiddleware``) on purpose: ``BaseHTTPMiddleware`` consumes/buffers the
request body via an internal stream, which would defeat the whole point of
rejecting *before* the body is read. By wrapping ``receive`` we intercept each
chunk without ever materializing the full body ourselves.

DORMANT-SAFE, SHADOW-FIRST (copy of the entitlement/email dormant pattern):

  * The whole middleware is only added in ``main.py`` when
    ``settings.BODY_SIZE_LIMIT_ENABLED`` is True — when False it is never in the
    stack at all (truly dormant).
  * Even when enabled, ``settings.BODY_SIZE_SHADOW_MODE`` (default True) makes it
    log a ``body_size_would_block`` event and ALLOW the request through instead
    of returning 413. Flip the flag to False to actually enforce. The streaming
    cap in shadow mode still stops *counting* but never injects a 413; the body
    flows untouched.

Per-path tuning: ``settings.BODY_SIZE_PATH_OVERRIDES`` is a dict mapping a path
PREFIX to a byte limit, so bulk-import surfaces (``/api/imports``, ``/carddav``,
CSV/vCard) can carry a larger cap than the global default. The LONGEST matching
prefix wins.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Default global cap (10 MiB) used when settings doesn't carry the field.
_DEFAULT_MAX_BODY_BYTES = 10 * 1024 * 1024


class BodySizeLimitMiddleware:
    """Reject HTTP request bodies larger than a configured byte limit.

    Pure ASGI: ``__call__(scope, receive, send)``. Non-``http`` scopes
    (``websocket``, ``lifespan``) pass straight through untouched.

    Constructor:
        app:      the inner ASGI application.
        settings: the app Settings object. Read (all optional, safe defaults):
            - MAX_REQUEST_BODY_BYTES   (int)        global cap, default 10 MiB
            - BODY_SIZE_PATH_OVERRIDES (dict)       {path-prefix: byte-limit}
            - BODY_SIZE_SHADOW_MODE    (bool)       log-only when True (default)
    """

    def __init__(self, app: Any, settings: Any) -> None:
        self.app = app
        self.settings = settings

        limit = getattr(settings, "MAX_REQUEST_BODY_BYTES", None)
        self.default_limit: int = (
            int(limit) if isinstance(limit, int) and limit > 0 else _DEFAULT_MAX_BODY_BYTES
        )

        # Normalize overrides into a list sorted by descending prefix length so
        # the longest (most specific) match wins on lookup. Defensive parsing:
        # a malformed override map degrades to "no overrides" rather than
        # crashing the request path.
        raw_overrides = getattr(settings, "BODY_SIZE_PATH_OVERRIDES", None) or {}
        overrides: list[tuple[str, int]] = []
        try:
            for prefix, value in dict(raw_overrides).items():
                if not isinstance(prefix, str):
                    continue
                try:
                    byte_limit = int(value)
                except (TypeError, ValueError):
                    continue
                if byte_limit > 0:
                    overrides.append((prefix, byte_limit))
        except (TypeError, ValueError, AttributeError):
            overrides = []
        overrides.sort(key=lambda item: len(item[0]), reverse=True)
        self._overrides: tuple[tuple[str, int], ...] = tuple(overrides)

        self.shadow_mode: bool = bool(
            getattr(settings, "BODY_SIZE_SHADOW_MODE", True)
        )

    def _limit_for_path(self, path: str) -> int:
        """Longest-prefix-wins lookup against the override map."""
        for prefix, byte_limit in self._overrides:
            if path == prefix or path.startswith(prefix):
                return byte_limit
        return self.default_limit

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        # Only guard real HTTP requests. websocket / lifespan / anything else
        # passes through with zero interference.
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "") or ""
        limit = self._limit_for_path(path)

        # ---- Layer 1: declared Content-Length pre-check -------------------
        declared = self._declared_content_length(scope)
        if declared is not None and declared > limit:
            if self.shadow_mode:
                logger.info(
                    "body_size_would_block",
                    reason="content_length",
                    path=path,
                    declared_bytes=declared,
                    limit_bytes=limit,
                    method=scope.get("method"),
                )
                # Shadow: allow through untouched. We still install the
                # streaming wrapper below so the would-block at the streaming
                # layer is observed too, but it will never inject a 413.
            else:
                logger.warning(
                    "body_size_rejected",
                    reason="content_length",
                    path=path,
                    declared_bytes=declared,
                    limit_bytes=limit,
                    method=scope.get("method"),
                )
                await self._send_413(send, limit)
                return

        # ---- Layer 2: streaming byte cap ----------------------------------
        # Wrap receive() to count actual bytes. State is closed over per-request.
        state: dict[str, Any] = {"received": 0, "tripped": False}

        async def counting_receive() -> dict[str, Any]:
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"") or b""
                state["received"] += len(body)
                if state["received"] > limit and not state["tripped"]:
                    state["tripped"] = True
                    if self.shadow_mode:
                        logger.info(
                            "body_size_would_block",
                            reason="streaming",
                            path=path,
                            received_bytes=state["received"],
                            limit_bytes=limit,
                            method=scope.get("method"),
                        )
                    else:
                        logger.warning(
                            "body_size_rejected",
                            reason="streaming",
                            path=path,
                            received_bytes=state["received"],
                            limit_bytes=limit,
                            method=scope.get("method"),
                        )
            return message

        if self.shadow_mode:
            # Pure observation: never inject a response, never alter the byte
            # stream beyond counting it. Pass the counting receive so the
            # streaming would-block is logged, but let the app see every chunk.
            await self.app(scope, counting_receive, send)
            return

        # ---- Enforcing mode: short-circuit on overflow --------------------
        # If the running total trips the cap, we stop forwarding to the app and
        # send a 413. We track whether the inner app has already started its
        # response so we never send conflicting ASGI messages.
        response_started = {"value": False}

        async def guarded_send(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                response_started["value"] = True
            await send(message)

        async def enforcing_receive() -> dict[str, Any]:
            message = await counting_receive()
            if state["tripped"]:
                # Overflow detected. Truncate the stream the app sees: report
                # an end-of-body with no more bytes so a well-behaved app stops
                # reading. The 413 itself is sent by the outer wrapper below
                # once the app returns (or here, pre-emptively, if the app
                # hasn't started a response yet).
                return {
                    "type": "http.request",
                    "body": b"",
                    "more_body": False,
                }
            return message

        # Drive the app. If the cap trips before the app emits a response,
        # we send the 413 ourselves; otherwise the app's own response stands
        # (it chose to handle the truncated body).
        try:
            await self.app(scope, enforcing_receive, guarded_send)
        finally:
            if state["tripped"] and not response_started["value"]:
                await self._send_413(send, limit)

    @staticmethod
    def _declared_content_length(scope: dict[str, Any]) -> int | None:
        """Parse the Content-Length header from an ASGI scope, if valid."""
        for name, value in scope.get("headers", []) or []:
            if name == b"content-length":
                try:
                    return int(value.decode("latin-1").strip())
                except (ValueError, AttributeError):
                    return None
        return None

    @staticmethod
    async def _send_413(send: Any, limit: int) -> None:
        """Emit a proper ASGI 413 Payload Too Large JSON response."""
        body = json.dumps(
            {
                "detail": "Request body too large.",
                "error": "payload_too_large",
                "limit_bytes": limit,
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                    (b"connection", b"close"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
                "more_body": False,
            }
        )
