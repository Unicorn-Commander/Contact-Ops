"""Contact-Ops FastAPI application.

Hosts the MCP server (JSON-RPC, ~95 tools across 23 domains) plus health
endpoints. No Data Intel catalogue/verify endpoints carried over — those live in
the upstream Data Intel service at `verify.centerdeep.online`.

Middleware order matters: CORS first (handles preflight), then JWT validation
(rejects unauthenticated requests), then audit (records every authenticated
request). Routers run after all three.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from contact_ops.agents.observability.metrics import metrics_endpoint
from contact_ops.agents.observability.otel import init_otel
from contact_ops.api.auth import router as auth_router
from contact_ops.api.chat import router as chat_router
from contact_ops.api.connectors import router as connectors_router
from contact_ops.api.import_csv import router as import_csv_router
from contact_ops.api.import_vcard import router as import_vcard_router
from contact_ops.api.people import router as people_router
from contact_ops.api.test_brigade_jwt import router as test_brigade_jwt_router
from contact_ops.api.webhooks import router as webhooks_router
from contact_ops.api.well_known import router as well_known_router
from contact_ops.carddav.router import carddav_router
from contact_ops.core.config import get_settings
from contact_ops.core.redis import close_redis, init_redis
from contact_ops.mcp import router as mcp_router
from contact_ops.middleware import (
    AuditMiddleware,
    BodySizeLimitMiddleware,
    JWTValidationMiddleware,
    RateLimitMiddleware,
    RequestMetricsMiddleware,
    SecurityHeadersMiddleware,
)
from contact_ops.observability.sentry import init_sentry
from contact_ops.ops.rls_status import (
    RLSSelfTestError,
    evaluate_rls_isolation,
    set_rls_status,
)
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


async def _rls_boot_self_test(settings: Any) -> None:
    """Adaptive RLS self-test. Safe to run pre-cutover (privileged role today).

    In a throwaway, always-rolled-back transaction on ``engine``:

    * Reads ``rolsuper``/``rolbypassrls`` for ``current_user``.
    * Requires two configured, distinct UUIDs. For each probe tenant, confirms
      that it is strict-mode and has at least one owned person visible, then
      counts persons owned by the OTHER tenant while the first tenant is bound.
      Strict probe tenants are required because standard-mode membership/ACL
      sharing is an intentional, authorized cross-tenant read.
    * Privileged role (superuser or bypassrls): RLS is inert — log a LOUD
      WARNING (``rls_inert_privileged_role``) but DO NOT refuse boot. This is
      today's pre-cutover state.
    * Non-privileged role: a non-zero cross-tenant count means RLS is not
      isolating — RAISE (``rls_self_test_failed``) to refuse boot. Zero ->
      ``rls_self_test_passed``.

    Every database/diagnostic failure propagates to the lifespan handler, which
    records a failed status and refuses boot. There is no silent skip path.
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

            configured = [str(t).strip() for t in (settings.RLS_SELFTEST_TENANT_IDS or []) if t]
            try:
                tenant_ids = [str(UUID(t)) for t in configured]
            except ValueError as exc:
                set_rls_status("failed", reason="invalid_probe_tenant_id")
                raise RLSSelfTestError(
                    "RLS self-test requires valid UUID probe tenant ids"
                ) from exc
            if len(tenant_ids) != 2 or len(set(tenant_ids)) != 2:
                set_rls_status("failed", reason="two_distinct_probe_tenants_required")
                raise RLSSelfTestError(
                    "RLS self-test requires exactly two distinct configured tenant ids"
                )

            bound_tenant, other_tenant = tenant_ids[0], tenant_ids[1]
            probe_counts: list[int] = []
            cross_counts: list[int] = []
            for probe_tenant in tenant_ids:
                await conn.execute(
                    text("SELECT set_config(:k, :v, true)"),
                    {"k": settings.TENANT_GUC_NAME, "v": probe_tenant},
                )
                isolation_mode = (
                    await conn.execute(text("SELECT current_tenant_isolation()"))
                ).scalar_one()
                if not privileged and isolation_mode != "strict":
                    set_rls_status("failed", reason="probe_tenant_not_strict")
                    raise RLSSelfTestError(
                        "RLS self-test probe tenants must both use strict isolation"
                    )
                probe_count = (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM persons "
                            "WHERE canonical_owner_tenant_id = CAST(:tenant AS uuid)"
                        ),
                        {"tenant": probe_tenant},
                    )
                ).scalar_one()
                probe_counts.append(int(probe_count))
                cross_count = (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM persons "
                            "WHERE canonical_owner_tenant_id <> CAST(:bound AS uuid)"
                        ),
                        {"bound": probe_tenant},
                    )
                ).scalar_one()
                cross_counts.append(int(cross_count))

            try:
                decision = evaluate_rls_isolation(
                    privileged=privileged,
                    probe_counts=(probe_counts[0], probe_counts[1]),
                    cross_counts=(cross_counts[0], cross_counts[1]),
                )
            except RLSSelfTestError:
                set_rls_status(
                    "failed",
                    bound_tenant=bound_tenant,
                    other_tenant=other_tenant,
                    cross_tenant_person_counts=cross_counts,
                )
                raise

            if decision == "inert_privileged":
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
                    cross_tenant_person_counts=cross_counts,
                )
                set_rls_status(
                    "inert_privileged",
                    bound_tenant=bound_tenant,
                    other_tenant=other_tenant,
                    cross_tenant_person_counts=cross_counts,
                )
                return

            logger.info(
                "rls_self_test_passed",
                bound_tenant=bound_tenant,
                other_tenant=other_tenant,
            )
            set_rls_status(
                "passed",
                bound_tenant=bound_tenant,
                other_tenant=other_tenant,
                cross_tenant_person_counts=cross_counts,
            )
        finally:
            # Always discard — this transaction is purely diagnostic.
            await trans.rollback()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Initialise error tracking FIRST so any boot-time failure is captured.
    # Dormant (no-op) unless SENTRY_DSN is set.
    init_sentry(settings)
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

    # Adaptive RLS self-test. Refuses boot when the configured probe contract
    # fails or a non-privileged role leaks cross-tenant rows.
    try:
        await _rls_boot_self_test(settings)
    except RLSSelfTestError:
        raise
    except (OperationalError, OSError) as exc:
        # P0: a down/unreachable Postgres at boot must be FATAL, not a warning.
        # Booting "healthy" against a dead DB turns an obvious outage into
        # confusing per-request failures (Docker healthcheck passes, traffic is
        # accepted, every call then errors). Refuse to start so the orchestrator
        # restarts/holds instead of serving a broken box.
        # NB: a refused/timed-out connection surfaces as ConnectionRefusedError /
        # TimeoutError (OSError subclasses) on asyncpg, NOT SQLAlchemy
        # OperationalError — so OSError must be caught here too, or the down-DB
        # case falls through to the warn-and-continue handler below.
        logger.error("db_unreachable_at_boot", error=str(exc))
        set_rls_status("failed", reason="database_unreachable")
        raise
    except Exception as exc:  # noqa: BLE001 — fail closed on every self-test error
        set_rls_status("failed", reason="self_test_error")
        logger.error("rls_self_test_errored", error=str(exc), exc_info=True)
        raise

    # OTel SDK init for agent tracing. Idempotent; no-op when the SDK is
    # missing (e.g. dev without OTel installed).
    init_otel(service_name=f"{settings.APP_NAME.lower()}-api")

    # Redis client for rate-limiting + ephemeral caches. Best-effort: the
    # rate-limit middleware fails open when Redis is unreachable, so a Redis
    # outage must NOT crash boot -- log and continue. Without this call the
    # global client is never initialised and get_redis() always raises, which
    # silently forces the rate limiter open (rate_limit_redis_unavailable on
    # every request). See core/redis.py + middleware/rate_limit.py.
    try:
        await init_redis()
    except Exception as exc:  # noqa: BLE001 -- Redis is optional / fail-open
        logger.warning("redis_init_failed", error=str(exc))

    prime_brigade_jwks_cache(settings)
    asyncio.create_task(register_with_brigade(settings))

    yield

    from contact_ops.core.database import close_db
    logger.info("contact_ops_shutting_down")
    await close_redis()
    await close_db()


def create_application() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        description=(
            "Contact-Ops — agent-first, MCP-native canonical contact + organization "
            "registry. The MCP server is the product; this FastAPI app hosts it plus "
            "health probes. See ARCHITECTURE.md and INTEGRATION_GUIDE.md for the "
            "design and integration contracts."
        ),
        version=settings.APP_VERSION,
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
        openapi_url="/openapi.json" if settings.is_development else None,
        lifespan=lifespan,
    )

    # CORS is mounted LAST in this function (the OUTERMOST middleware), so its
    # headers attach to ALL responses including JWT 401s and rate-limit 429s, and
    # it answers OPTIONS preflight before any auth/rate-limit work. The
    # add_middleware call is at the end of the middleware section below.

    # JWT validation — must run before audit so audit has claims to record.
    app.add_middleware(JWTValidationMiddleware, settings=settings)
    # Audit — runs after JWT, records request + response with full actor chain.
    app.add_middleware(AuditMiddleware)
    # HTTP request metrics — labels by route template only; dormant-safe (cheap)
    # and on by default, flip METRICS_HTTP_ENABLED=false to disable.
    if settings.METRICS_HTTP_ENABLED:
        app.add_middleware(RequestMetricsMiddleware)

    # Exposure hardening (P-00075). Each is dormant by default + shadow-first;
    # main.py only mounts the ones turned on. Starlette runs the LAST-added
    # middleware OUTERMOST. CORS is added LAST (below), so the request flow is:
    #   CORS -> SecurityHeaders -> RateLimit -> BodySize -> [RequestMetrics]
    #   -> Audit -> JWT -> route.
    # RateLimit + BodySize sit OUTER of JWT so they shed abusive/oversized traffic
    # (incl. 401-spam) before auth work; RateLimit keys by IP when there are no
    # claims yet (degrades gracefully). CORS is outermost so its headers attach
    # even to JWT 401s and rate-limit 429s (the browser sees a real status, not a
    # misleading CORS error).
    if settings.BODY_SIZE_LIMIT_ENABLED:
        app.add_middleware(BodySizeLimitMiddleware, settings=settings)
    if settings.RATE_LIMIT_ENABLED:
        app.add_middleware(RateLimitMiddleware, settings=settings)
    if settings.SECURITY_HEADERS_ENABLED:
        app.add_middleware(SecurityHeadersMiddleware, settings=settings)

    # CORS — OUTERMOST (added last). Restrictive by default; dev sets CORS_ORIGINS
    # via env. allow_credentials=True is incompatible with allow_origins=["*"] per
    # browser rules; the Settings validator refuses that combo at startup. Being
    # outermost, CORS headers attach to error responses too (JWT 401s, rate-limit
    # 429s) so the browser sees a real status instead of a misleading CORS wall,
    # and OPTIONS preflight is answered before auth/rate-limit work.
    if settings.CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ORIGINS,
            allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            # The browser app IS an MCP client over Streamable HTTP, so the MCP
            # transport headers must be allowed (request) and exposed (response).
            # Without Mcp-Session-Id the session can never be read and every call
            # fails CORS preflight with "Disallowed CORS headers".
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-Request-ID",
                "Mcp-Session-Id",
                "Mcp-Protocol-Version",
                "Last-Event-ID",
            ],
            expose_headers=[
                "X-Request-ID",
                "X-Process-Time",
                "Mcp-Session-Id",
                "Mcp-Protocol-Version",
            ],
            max_age=600,
        )

    # Routes
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(connectors_router)
    app.include_router(people_router)
    # Lago billing webhook (subscription.created / terminated -> plan_tier).
    # Authenticated inline by verifying Lago's per-payload RS256 signature JWT
    # against its public key (or an opt-in LAGO_WEBHOOK_STATIC_SECRET for
    # proxy-injected deploys); the JWT middleware skips /api/webhooks via
    # SKIP_PREFIXES — see middleware/jwt_validation.py. Fails closed (503) until a
    # signature can be verified.
    app.include_router(webhooks_router)
    app.include_router(import_vcard_router)
    app.include_router(import_csv_router)
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
