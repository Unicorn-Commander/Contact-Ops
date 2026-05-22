"""Contact-Ops FastAPI application.

Phase 0 surface: health endpoints + MCP scaffold (JSON-RPC) only. No Data Intel
catalogue/verify endpoints carried over — those live in the upstream Data Intel
service at `verify.centerdeep.online`.

Middleware order matters: CORS first (handles preflight), then JWT validation
(rejects unauthenticated requests), then audit (records every authenticated
request). Routers run after all three.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from contact_ops.carddav import carddav_router
from contact_ops.core.config import get_settings
from contact_ops.mcp import router as mcp_router
from contact_ops.middleware import AuditMiddleware, JWTValidationMiddleware
from contact_ops.routers import health_router

# Configure structured logging (JSON for prod-ready ingestion via Loki/Promtail)
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info(
        "contact_ops_starting",
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION,
        env=settings.ENV,
        standalone_mode=settings.STANDALONE_MODE,
    )
    if settings.STANDALONE_MODE:
        logger.warning(
            "standalone_mode_active",
            note="JWT validation is bypassed. Use only in dev/test environments.",
        )

    yield

    from contact_ops.core.database import close_db
    logger.info("contact_ops_shutting_down")
    await close_db()


def create_application() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        description=(
            "Contact-Ops — agent-first, MCP-native canonical contact + organization "
            "registry. The MCP server is the product; this FastAPI app hosts it plus "
            "health probes. See /Users/aaronstransky/Documents/Contact-Ops-MCP-Design.md "
            "for the design contract."
        ),
        version=settings.APP_VERSION,
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
        openapi_url="/openapi.json" if settings.is_development else None,
        lifespan=lifespan,
    )

    # CORS — restrictive by default. Dev sets CORS_ORIGINS explicitly via env.
    # `allow_credentials=True` is incompatible with `allow_origins=["*"]` per
    # browser rules; the validator on Settings refuses that combo at startup.
    if settings.CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ORIGINS,
            allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID", "X-Process-Time"],
            max_age=600,
        )

    # JWT validation — must run before audit so audit has claims to record.
    app.add_middleware(JWTValidationMiddleware, settings=settings)
    # Audit — runs after JWT, records request + response with full actor chain.
    app.add_middleware(AuditMiddleware)

    # Routes
    app.include_router(health_router)
    app.include_router(mcp_router)
    # CardDAV runs its own HTTP Basic auth (per-device app passwords).
    # The JWT middleware skips /carddav and /.well-known/carddav via
    # JWTValidationMiddleware.SKIP_PREFIXES — see middleware/jwt_validation.py.
    app.include_router(carddav_router)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        return {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "design_doc": "see Contact-Ops-MCP-Design.md",
            "mcp": "/mcp",
            "health": "/health",
        }

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Any, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
            error=str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    return app


app = create_application()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "contact_ops.main:app",
        host="0.0.0.0",
        port=8501,
        reload=settings.is_development,
        log_level=settings.LOG_LEVEL.lower(),
    )
