"""MCP OAuth protected-resource discovery and bearer challenges.

The MCP host is a separate public origin from the Contact-Ops web app. Keep
the resource identifier and the RFC 9728 metadata URL in one module so the
challenge cannot drift from the document that OAuth clients fetch.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

from fastapi import APIRouter
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from contact_ops.core.config import get_settings

MCP_PATH = "/mcp"
MCP_REALM = "contact-ops-mcp"
PRM_PATH = "/.well-known/oauth-protected-resource"
PRM_MCP_PATH = f"{PRM_PATH}{MCP_PATH}"
MCP_RESOURCE_DOCUMENTATION = (
    "https://unicorncommander.ai/platform/bring-your-own-ai"
)
MCP_CORS_METHODS = "GET, POST, OPTIONS"
MCP_CORS_HEADERS = (
    "Authorization, Content-Type, Accept, Mcp-Session-Id, "
    "Mcp-Protocol-Version, Last-Event-ID"
)
MCP_CORS_EXPOSE_HEADERS = "Mcp-Session-Id"

# Keycloak's anonymous-DCR policy allows exactly these scopes. The ``mcp``
# scope carries the Contact-Ops audience and user/tenant claims into the token.
MCP_SCOPES = ("openid", "profile", "email", "offline_access", "mcp")

router = APIRouter(tags=["oauth-discovery"])


def canonical_mcp_url() -> str:
    """Return the configured public MCP resource identifier."""
    return get_settings().MCP_CANONICAL_URL.rstrip("/")


def protected_resource_metadata_url() -> str:
    """Return the path-suffixed RFC 9728 URL named by MCP 401 challenges."""
    base = canonical_mcp_url()
    origin = base[: -len(MCP_PATH)]
    return f"{origin}{PRM_MCP_PATH}"


def metadata_document() -> dict[str, object]:
    """Build the RFC 9728 protected-resource metadata document."""
    issuer = get_settings().KEYCLOAK_ISSUER.rstrip("/")
    return {
        "resource": canonical_mcp_url(),
        "authorization_servers": [issuer],
        "bearer_methods_supported": ["header"],
        "scopes_supported": list(MCP_SCOPES),
        "resource_name": "Contact-Ops MCP",
        "resource_documentation": MCP_RESOURCE_DOCUMENTATION,
    }


def _metadata_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": (
            "Authorization, Content-Type, Accept, Mcp-Session-Id, "
            "Mcp-Protocol-Version, Last-Event-ID"
        ),
        "Cache-Control": "public, max-age=3600",
    }


def _metadata_response() -> JSONResponse:
    return JSONResponse(metadata_document(), headers=_metadata_headers())


def mcp_unauthorized_response(*, token_present: bool) -> JSONResponse:
    """Build the MCP 401 challenge without reflecting bearer material."""
    challenge = (
        f'Bearer realm="{MCP_REALM}", '
        f'resource_metadata="{protected_resource_metadata_url()}"'
    )
    if token_present:
        challenge += ', error="invalid_token"'

    return JSONResponse(
        {
            "error": "unauthorized",
            "detail": "Invalid bearer token" if token_present else "Missing bearer token",
        },
        status_code=401,
        headers={"WWW-Authenticate": challenge},
    )


def bearer_token_present(headers: Mapping[str, str]) -> bool:
    """Return whether a non-empty Bearer value was presented."""
    value = headers.get("authorization") or headers.get("Authorization") or ""
    if not value.lower().startswith("bearer "):
        return False
    return bool(value[7:].strip())


def _normalise_origin(value: str) -> str:
    return value.strip().rstrip("/")


def _is_mcp_path(path: str) -> bool:
    return path in {MCP_PATH, f"{MCP_PATH}/"}


def _is_prm_path(path: str) -> bool:
    return path == PRM_PATH or path.startswith(f"{PRM_PATH}/")


def _allowed_mcp_origins(extra_origins: Iterable[str] = ()) -> set[str]:
    settings = get_settings()
    configured = (
        *settings.CORS_ORIGINS,
        settings.FRONTEND_URL,
        *settings.MCP_ALLOWED_ORIGINS,
        *extra_origins,
    )
    canonical_origin = urlsplit(canonical_mcp_url())._replace(path="", query="", fragment="")
    configured = (*configured, canonical_origin.geturl())
    return {_normalise_origin(value) for value in configured if value}


def _mcp_cors_headers(origin: str | None) -> dict[str, str]:
    headers = {
        "Access-Control-Allow-Methods": MCP_CORS_METHODS,
        "Access-Control-Allow-Headers": MCP_CORS_HEADERS,
        "Access-Control-Expose-Headers": MCP_CORS_EXPOSE_HEADERS,
        "Access-Control-Max-Age": "3600",
    }
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
    return headers


def _upsert_header(message: Message, name: str, value: str) -> None:
    headers = list(message.get("headers") or [])
    encoded_name = name.lower().encode("latin-1")
    headers = [(key, raw) for key, raw in headers if key.lower() != encoded_name]
    headers.append((encoded_name, value.encode("latin-1")))
    message["headers"] = headers


class MCPPathCORSMiddleware:
    """Handle MCP preflight/origin checks before the bearer middleware."""

    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str] = ()) -> None:
        self.app = app
        self.allowed_origins = tuple(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")
        origin = Headers(scope=scope).get("origin")

        if _is_prm_path(path) and method == "OPTIONS":
            await _metadata_response()(scope, receive, send)
            return

        if _is_mcp_path(path):
            if origin and _normalise_origin(origin) not in _allowed_mcp_origins(
                self.allowed_origins
            ):
                await JSONResponse(
                    {"error": "forbidden", "detail": "Origin not allowed"},
                    status_code=403,
                )(scope, receive, send)
                return
            if method == "OPTIONS":
                await self._preflight_response(origin)(scope, receive, send)
                return

        async def send_wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = dict(message)
                if _is_prm_path(path):
                    for name, value in _metadata_headers().items():
                        _upsert_header(message, name, value)
                elif _is_mcp_path(path) and origin:
                    for name, value in _mcp_cors_headers(origin).items():
                        _upsert_header(message, name, value)
            await send(message)

        await self.app(scope, receive, send_wrapped)

    @staticmethod
    def _preflight_response(origin: str | None) -> Response:
        return Response(status_code=204, headers=_mcp_cors_headers(origin))


@router.api_route(PRM_PATH, methods=["GET", "OPTIONS"], include_in_schema=False)
async def protected_resource_metadata_root() -> JSONResponse:
    """Serve RFC 9728 metadata at the origin-level well-known path."""
    return _metadata_response()


@router.api_route(PRM_MCP_PATH, methods=["GET", "OPTIONS"], include_in_schema=False)
async def protected_resource_metadata_mcp() -> JSONResponse:
    """Serve RFC 9728 metadata at the path-suffixed well-known path."""
    return _metadata_response()


@router.api_route(f"{PRM_PATH}/", methods=["GET", "OPTIONS"], include_in_schema=False)
async def protected_resource_metadata_root_slash() -> RedirectResponse:
    """Normalize the optional trailing slash on the root well-known path."""
    return RedirectResponse(url=PRM_PATH, status_code=308)


@router.api_route(
    f"{PRM_MCP_PATH}/",
    methods=["GET", "OPTIONS"],
    include_in_schema=False,
)
async def protected_resource_metadata_mcp_slash() -> JSONResponse:
    """Keep the slash-suffixed compatibility spelling public and cacheable."""
    return _metadata_response()


__all__ = (
    "MCP_PATH",
    "MCPPathCORSMiddleware",
    "MCP_REALM",
    "MCP_SCOPES",
    "PRM_MCP_PATH",
    "PRM_PATH",
    "bearer_token_present",
    "canonical_mcp_url",
    "metadata_document",
    "mcp_unauthorized_response",
    "protected_resource_metadata_url",
    "router",
)
