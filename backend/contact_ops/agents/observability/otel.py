"""OpenTelemetry SDK init + GenAI semantic conventions.

GenAI conventions per the OTel spec (W3C):

* ``gen_ai.system``                 — provider, e.g. ``anthropic`` / ``litellm``
* ``gen_ai.request.model``          — model id, e.g. ``claude-opus-4-7``
* ``gen_ai.request.max_tokens``     — caller's limit
* ``gen_ai.response.model``         — model id the API actually used
* ``gen_ai.response.finish_reasons``— ``stop`` / ``length`` / ``error``
* ``gen_ai.usage.input_tokens``     — prompt tokens
* ``gen_ai.usage.output_tokens``    — completion tokens

The Laminar collector reads these directly to build the agent transcript
view. Phase 3 wraps every BaseAgent run in an ``agent_span`` and every LLM
call in a child span with the GenAI attributes set.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Final
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)


class GenAIAttributes:
    """Stable attribute keys for GenAI semantic conventions."""

    SYSTEM: Final = "gen_ai.system"
    REQUEST_MODEL: Final = "gen_ai.request.model"
    REQUEST_MAX_TOKENS: Final = "gen_ai.request.max_tokens"
    REQUEST_TEMPERATURE: Final = "gen_ai.request.temperature"
    RESPONSE_MODEL: Final = "gen_ai.response.model"
    RESPONSE_FINISH_REASONS: Final = "gen_ai.response.finish_reasons"
    USAGE_INPUT_TOKENS: Final = "gen_ai.usage.input_tokens"
    USAGE_OUTPUT_TOKENS: Final = "gen_ai.usage.output_tokens"
    USAGE_CACHE_READ_TOKENS: Final = "gen_ai.usage.cache_read_tokens"
    USAGE_CACHE_WRITE_TOKENS: Final = "gen_ai.usage.cache_write_tokens"


_INITIALIZED = False
_PROVIDER: Any = None


def init_otel(*, service_name: str = "contact-ops-agents") -> None:
    """Initialize the OTel SDK with OTLP gRPC exporter.

    Idempotent; calling twice is a no-op. The exporter targets the env
    var ``OTEL_EXPORTER_OTLP_ENDPOINT`` (default Laminar at
    ``http://contact-ops-laminar:4317`` per docs/AGENTS_DEPLOY.md).
    """
    global _INITIALIZED, _PROVIDER
    if _INITIALIZED:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        logger.warning("otel_sdk_import_failed", error=str(exc))
        _INITIALIZED = True  # avoid retry storms
        return

    endpoint = os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "http://contact-ops-laminar:4317"
    )

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": os.environ.get("CONTACT_OPS_VERSION", "phase-3"),
            "deployment.environment": os.environ.get("ENV", "dev"),
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _PROVIDER = provider
    _INITIALIZED = True
    logger.info("otel_initialized", endpoint=endpoint, service=service_name)


def tracer() -> Any:
    """Return the OTel tracer (no-op tracer if SDK not present)."""
    try:
        from opentelemetry import trace

        return trace.get_tracer("contact_ops.agents")
    except ImportError:
        return _NoOpTracer()


@contextmanager
def agent_span(
    *,
    agent_slug: str,
    run_id: UUID,
    tenant_id: UUID,
    agent_version: str,
) -> Any:
    """Open the per-run span; subclass code adds attrs / records exceptions."""
    t = tracer()
    with t.start_as_current_span(
        f"agent.{agent_slug}",
        attributes={
            "contactops.agent.slug": agent_slug,
            "contactops.agent.version": agent_version,
            "contactops.agent.run_id": str(run_id),
            "contactops.tenant_id": str(tenant_id),
        },
    ) as span:
        yield span


class _NoOpSpan:
    """Drop-in span for environments without the OTel SDK installed."""

    def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
        return

    def add_event(self, *_args: Any, **_kwargs: Any) -> None:
        return

    def record_exception(self, *_args: Any, **_kwargs: Any) -> None:
        return

    def set_status(self, *_args: Any, **_kwargs: Any) -> None:
        return

    def end(self) -> None:
        return


class _NoOpTracer:
    @contextmanager
    def start_as_current_span(
        self, _name: str, attributes: dict[str, Any] | None = None
    ) -> Any:
        yield _NoOpSpan()
