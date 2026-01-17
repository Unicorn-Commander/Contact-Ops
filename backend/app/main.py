"""
Center Deep Data Intel - FastAPI Application

Main application entry point for the Data Intel API service.
Provides email verification, company data management, and enrichment services.
"""

from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.redis import close_redis, init_redis
from app.routers import admin_router, catalogue_router, health_router, usage_router, verify_router

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
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
async def lifespan(app: FastAPI):
    """
    Application lifespan handler.

    Handles startup and shutdown events for the application.
    """
    # Startup
    settings = get_settings()

    logger.info(
        "Starting Center Deep Data Intel API",
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION,
        debug=settings.DEBUG,
        standalone_mode=settings.STANDALONE_MODE,
        log_level=settings.LOG_LEVEL,
    )

    logger.info(
        "Configuration loaded",
        database_url=settings.DATABASE_URL.split("@")[-1] if "@" in settings.DATABASE_URL else "configured",
        redis_url=settings.REDIS_URL.split("@")[-1] if "@" in settings.REDIS_URL else settings.REDIS_URL,
        smtp_timeout=settings.SMTP_TIMEOUT,
        smtp_concurrency=settings.SMTP_CONCURRENCY,
    )

    if settings.STANDALONE_MODE:
        logger.warning(
            "Running in STANDALONE_MODE - authentication is disabled. "
            "Do not use in production!"
        )

    # Initialize Redis connection
    try:
        await init_redis()
        logger.info("Redis connection initialized successfully")
    except Exception as e:
        logger.error(
            "Failed to initialize Redis connection",
            error=str(e),
            hint="Job storage will be unavailable. Check Redis connection settings.",
        )
        # Continue startup - the app can still function without Redis for sync operations

    yield

    # Shutdown
    logger.info("Shutting down Center Deep Data Intel API")

    # Close Redis connection
    try:
        await close_redis()
        logger.info("Redis connection closed")
    except Exception as e:
        logger.warning("Error closing Redis connection", error=str(e))


def create_application() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance
    """
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        description=(
            "Center Deep Data Intel API - Email verification, company data management, "
            "and B2B intelligence services."
        ),
        version=settings.APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Configure CORS middleware
    # In production, restrict origins to specific domains
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # TODO: Restrict in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Process-Time"],
    )

    # Include routers
    app.include_router(health_router)
    app.include_router(verify_router)
    app.include_router(catalogue_router)
    app.include_router(admin_router)
    app.include_router(usage_router)

    # Root endpoint
    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        """Root endpoint returning API information."""
        return {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs": "/docs",
            "health": "/health",
        }

    # Global exception handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request, exc: Exception) -> JSONResponse:
        """Handle uncaught exceptions."""
        logger.error(
            "Unhandled exception",
            path=request.url.path,
            method=request.method,
            error=str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "type": type(exc).__name__,
            },
        )

    return app


# Create the application instance
app = create_application()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
