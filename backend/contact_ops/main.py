"""Contact-Ops FastAPI application.

Phase 0 surface: health endpoints + MCP scaffold (JSON-RPC) only. No Data Intel
catalogue/verify endpoints carried over — those live in the upstream Data Intel
service at `verify.centerdeep.online`.

Middleware order matters: CORS first (handles preflight), then JWT validation
(rejects unauthenticated requests), then audit (records every authenticated
request). Routers run after all three.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from contact_ops.agents.observability.metrics import metrics_endpoint
from contact_ops.agents.observability.otel import init_otel
from contact_ops.api.auth import router as auth_router
from contact_ops.api.billing import router as billing_router
from contact_ops.api.chat import router as chat_router
from contact_ops.api.connectors import router as connectors_router
from contact_ops.api.import_vcard import router as import_vcard_router
from contact_ops.api.test_brigade_jwt import router as test_brigade_jwt_router
from contact_ops.api.well_known import router as well_known_router
from contact_ops.carddav.router import carddav_router
from contact_ops.core.config import get_settings
from contact_ops.mcp import router as mcp_router
from contact_ops.middleware import AuditMiddleware, JWTValidationMiddleware
from contact_ops.routers import health_router
from contact_ops.services.brigade_jwt_verifier import prime_brigade_jwks_cache
from contact_ops.services.brigade_registration import register_with_brigade

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


class RLSSelfTestError(RuntimeError):
    """Raised to refuse boot when RLS is enforced but does not isolate tenants.

    Only raised for the non-privileged-role case where a cross-tenant
    ``persons`` count came back non-zero — i.e. the runtime role is RLS-subject
    yet a tenant can see another tenant's rows. That is a hard fail.
    """


async def _rls_boot_self_test(settings: Any) -> None:
    """Adaptive RLS self-test. Safe to run pre-cutover (privileged role today).

    In a throwaway, always-rolled-back transaction on ``engine``:

    * Reads ``rolsuper``/``rolbypassrls`` for ``current_user``.
    * Picks two distinct tenant ids, sets ``app.tenant_id`` to one, and counts
      ``persons`` owned by the OTHER tenant.
    * Privileged role (superuser or bypassrls): RLS is inert — log a LOUD
      WARNING (``rls_inert_privileged_role``) but DO NOT refuse boot. This is
      today's pre-cutover state.
    * Non-privileged role: a non-zero cross-tenant count means RLS is not
      isolating — RAISE (``rls_self_test_failed``) to refuse boot. Zero ->
      ``rls_self_test_passed``.

    Resilience: any failure to RUN the test (DB hiccup, missing table, etc.)
    is logged as a WARNING and swallowed, EXCEPT the explicit non-privileged
    cross-tenant!=0 case, which must propagate and refuse boot.
    """
    from contact_ops.core.database import engine

    async with engine.connect() as conn:
        # Throwaway transaction — we never commit; rollback on exit guarantees
        # the SET LOCAL app.tenant_id never leaks to a pooled connection.
        trans = await conn.begin()
        try:
            role_row = (
                await conn.execute(
                    text(
                        "SELECT rolsuper, rolbypassrls FROM pg_roles "
                        "WHERE rolname = current_user"
                    )
                )
            ).first()
            is_super = bool(role_row[0]) if role_row else False
            is_bypassrls = bool(role_row[1]) if role_row else False
            privileged = is_super or is_bypassrls

            configured = [
                str(t) for t in (settings.RLS_SELFTEST_TENANT_IDS or []) if t
            ]
            if len(configured) >= 2:
                # Under the runtime role, enumerating tenants returns nothing
                # (RLS hides them without a bound GUC), so use configured probe
                # ids to actively assert isolation instead of skipping.
                tenant_ids = configured[:2]
            else:
                tenant_ids = [
                    str(r[0])
                    for r in (
                        await conn.execute(
                            text("SELECT id FROM tenants ORDER BY id LIMIT 2")
                        )
                    ).all()
                ]

            if len(tenant_ids) < 2:
                # Can't assert cross-tenant isolation with fewer than two
                # tenants. Still surface the role posture so operators know.
                logger.info(
                    "rls_self_test_skipped",
                    reason="fewer_than_two_tenants",
                    tenant_count=len(tenant_ids),
                    privileged_role=privileged,
                    rolsuper=is_super,
                    rolbypassrls=is_bypassrls,
                )
                return

            bound_tenant, other_tenant = tenant_ids[0], tenant_ids[1]
            await conn.execute(
                text("SELECT set_config(:k, :v, true)"),
                {"k": settings.TENANT_GUC_NAME, "v": bound_tenant},
            )
            cross_count = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM persons "
                        "WHERE canonical_owner_tenant_id = CAST(:other AS uuid)"
                    ),
                    {"other": other_tenant},
                )
            ).scalar_one()

            if privileged:
                logger.warning(
                    "rls_inert_privileged_role",
                    note=(
                        "Runtime role is superuser/bypassrls; RLS is NOT "
                        "DB-enforced. Expected pre-cutover. Re-point "
                        "DATABASE_URL at a non-privileged role to enforce."
                    ),
                    rolsuper=is_super,
                    rolbypassrls=is_bypassrls,
                    bound_tenant=bound_tenant,
                    other_tenant=other_tenant,
                    cross_tenant_person_count=cross_count,
                )
                return

            if cross_count != 0:
                logger.error(
                    "rls_self_test_failed",
                    note=(
                        "Non-privileged role can read another tenant's persons "
                        "under RLS. Refusing to boot."
                    ),
                    bound_tenant=bound_tenant,
                    other_tenant=other_tenant,
                    cross_tenant_person_count=cross_count,
                )
                raise RLSSelfTestError(
                    "RLS self-test failed: cross-tenant person count "
                    f"={cross_count} under non-privileged role"
                )

            logger.info(
                "rls_self_test_passed",
                bound_tenant=bound_tenant,
                other_tenant=other_tenant,
            )
        finally:
            # Always discard — this transaction is purely diagnostic.
            await trans.rollback()


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

    # Adaptive RLS self-test. Refuses boot ONLY when a non-privileged role
    # leaks cross-tenant rows; otherwise warns (privileged) or passes/skips.
    try:
        await _rls_boot_self_test(settings)
    except RLSSelfTestError:
        raise
    except Exception as exc:  # noqa: BLE001 — diagnostic test must not crash boot
        logger.warning("rls_self_test_errored", error=str(exc), exc_info=True)

    # OTel SDK init for agent tracing. Idempotent; no-op when the SDK is
    # missing (e.g. dev without OTel installed).
    init_otel(service_name=f"{settings.APP_NAME.lower()}-api")
    prime_brigade_jwks_cache(settings)
    asyncio.create_task(register_with_brigade(settings))

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
    app.include_router(auth_router)
    # Billing proxy → Ops-Center (Stripe + Lago). Authenticated API router, so
    # it runs after the JWT middleware like the others. INERT until
    # OPS_CENTER_URL + OPS_CENTER_SERVICE_KEY are set.
    app.include_router(billing_router)
    app.include_router(chat_router)
    app.include_router(connectors_router)
    app.include_router(import_vcard_router)
    app.include_router(well_known_router)
    if settings.ENV != "prod":
        app.include_router(test_brigade_jwt_router)
    app.include_router(mcp_router)
    # CardDAV runs its own HTTP Basic auth (per-device app passwords).
    # The JWT middleware skips /carddav and /.well-known/carddav via
    # JWTValidationMiddleware.SKIP_PREFIXES — see middleware/jwt_validation.py.
    app.include_router(carddav_router)

    # Prometheus scrape endpoint for the agent-fleet metrics registry.
    # Stays unauthenticated (Prom scrape) but is read-only and exposes only
    # ``contactops_agent_*`` metrics — no caller-supplied input.
    app.add_api_route(
        "/metrics",
        metrics_endpoint,
        methods=["GET"],
        include_in_schema=False,
    )

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
