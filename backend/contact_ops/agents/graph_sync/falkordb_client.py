"""Async FalkorDB wrapper with tenant-scoped graph routing.

The FalkorDB Python client is sync-only in the pinned 1.2.2 line. This module
uses redis.asyncio directly for GRAPH.QUERY so workers and MCP tools stay
async without adding a second thread pool abstraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlparse
from uuid import UUID

import redis.asyncio as redis
import structlog

from contact_ops.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)

GRAPH_NAME_RE = re.compile(r"^contact_ops__[a-z0-9][a-z0-9_]{0,62}$")
SLUG_RE = re.compile(r"[^a-z0-9_]+")


@dataclass(frozen=True)
class TenantGraph:
    tenant_id: UUID
    graph_name: str
    graph_mode: str = "per_org_graph"


@dataclass(frozen=True)
class GraphQueryResult:
    header: list[Any]
    rows: list[list[Any]]
    statistics: list[Any]


def graph_name_for_slug(slug: str, *, prefix: str = "contact_ops__") -> str:
    normalized = SLUG_RE.sub("_", slug.strip().lower().replace("-", "_")).strip("_")
    if not normalized:
        raise ValueError("tenant slug cannot produce an empty graph name")
    graph_name = f"{prefix}{normalized[:63]}"
    validate_graph_name(graph_name)
    return graph_name


def validate_graph_name(graph_name: str) -> str:
    if not GRAPH_NAME_RE.fullmatch(graph_name):
        raise ValueError(f"invalid Contact-Ops graph name: {graph_name!r}")
    return graph_name


class FalkorDBGraphClient:
    """Minimal async client for parameterized FalkorDB graph queries."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._redis: redis.Redis | None = None

    async def connect(self) -> None:
        if self._redis is not None:
            return
        self._redis = cast(Any, redis.from_url)(
            self._settings.FALKORDB_URL,
            decode_responses=True,
            socket_timeout=10,
            socket_connect_timeout=5,
        )

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def ping(self) -> bool:
        await self.connect()
        assert self._redis is not None
        ping_result = self._redis.ping()
        if hasattr(ping_result, "__await__"):
            return bool(await ping_result)
        return bool(ping_result)

    async def query(
        self,
        tenant_graph: TenantGraph,
        cypher: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_ms: int = 10_000,
    ) -> GraphQueryResult:
        """Run one parameterized query against exactly one tenant graph.

        FalkorDB does NOT accept a separate ``PARAMS`` Redis arg. The
        parameters are prefixed inline to the Cypher query as
        ``CYPHER key1=value1 key2=value2 ... MATCH ...``. This is the
        wire format both falkordb-py and the official Redis CLI use.
        """
        graph_name = validate_graph_name(tenant_graph.graph_name)
        if tenant_graph.graph_mode not in {"per_org_graph", "per_org_instance"}:
            raise ValueError("Phase 4 graph queries require per-tenant graph routing")
        await self.connect()
        assert self._redis is not None

        full_query = cypher
        if params:
            param_prefix = "CYPHER " + " ".join(
                f"{key}={_render_cypher_value(self._coerce_param(value))}"
                for key, value in params.items()
            )
            full_query = f"{param_prefix} {cypher}"

        args: list[Any] = [
            "GRAPH.QUERY",
            graph_name,
            full_query,
            # NOTE: intentionally NOT --compact. The readers (extract_ego_graph,
            # graph_overview) expect decoded scalars + label-name strings, which
            # is what non-compact GRAPH.QUERY returns. --compact returns
            # [type, value] pairs and integer label ids, which these break on.
            "TIMEOUT",
            str(timeout_ms),
        ]
        raw = await cast(Any, self._redis.execute_command)(*args)
        return self._parse_result(raw)

    async def bootstrap_graph(self, tenant_graph: TenantGraph) -> None:
        """Create the indexes Phase 4 depends on. Safe to run repeatedly."""
        index_queries = [
            "CREATE INDEX FOR (n:Person) ON (n.id)",
            "CREATE INDEX FOR (n:Organization) ON (n.id)",
            "CREATE INDEX FOR (n:Topic) ON (n.id)",
            "CREATE INDEX FOR (n:Tag) ON (n.id)",
            "CREATE INDEX FOR (n:Email) ON (n.id)",
            "CREATE INDEX FOR (n:Phone) ON (n.id)",
        ]
        for cypher in index_queries:
            try:
                await self.query(tenant_graph, cypher, {})
            except Exception as exc:  # FalkorDB raises if the index already exists.
                logger.debug(
                    "graph_index_bootstrap_skipped",
                    graph=tenant_graph.graph_name,
                    cypher=cypher,
                    error=str(exc),
                )

    def _coerce_param(self, value: Any) -> Any:
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, list | tuple):
            return [self._coerce_param(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._coerce_param(item) for key, item in value.items()}
        return value

    def _parse_result(self, raw: Any) -> GraphQueryResult:
        if not isinstance(raw, list):
            return GraphQueryResult(header=[], rows=[], statistics=[raw])
        if len(raw) == 3:
            header, rows, statistics = raw
            return GraphQueryResult(
                header=list(header or []),
                rows=list(rows or []),
                statistics=list(statistics or []),
            )
        if len(raw) == 2:
            header, rows = raw
            return GraphQueryResult(header=list(header or []), rows=list(rows or []), statistics=[])
        return GraphQueryResult(header=[], rows=[], statistics=list(raw))


def falkor_host_port(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    return parsed.hostname or "localhost", parsed.port or 6379


def _render_cypher_value(value: Any) -> str:
    """Render a parameter value as a Cypher literal (FalkorDB inline params).

    FalkorDB's ``CYPHER k=v`` prefix expects Cypher-formatted values:
    strings are single-quoted with backslash-escaped quotes, lists are
    bracketed, numbers and booleans are bare.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_render_cypher_value(v) for v in value) + "]"
    if isinstance(value, dict):
        body = ", ".join(
            f"{k}: {_render_cypher_value(v)}" for k, v in value.items()
        )
        return "{" + body + "}"
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"
