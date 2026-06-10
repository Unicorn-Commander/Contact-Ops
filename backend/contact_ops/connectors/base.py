"""Connector base classes and shared orchestration."""

from __future__ import annotations

import asyncio
import json
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import httpx
from cryptography.fernet import Fernet
from pydantic import BaseModel, Field
from sqlalchemy import text

from contact_ops.core.config import get_settings
from contact_ops.core.database import async_session_maker, bind_session_context
from contact_ops.importers.base import CanonicalImportRecord
from contact_ops.services.agents.confidence_approver import run_confidence_approver
from contact_ops.services.agents.dedup_agent import run_dedup_agent
from contact_ops.services.agents.quality_filter import run_quality_filter
from contact_ops.services.proposal_emit import emit_person_create_proposal, import_fingerprint

if TYPE_CHECKING:
    from contact_ops.mcp.registry import MCPContext

Provider = Literal["icloud", "m365", "gmail"]


class ConnectorRunResult(BaseModel):
    parsed_count: int = 0
    proposed_count: int = 0
    deduped_count: int = 0
    skipped_count: int = 0
    errors: list[str] = Field(default_factory=list)


class Connector(ABC):
    provider: Provider

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    @abstractmethod
    async def pull(self, ctx: MCPContext, since: datetime | None) -> list[CanonicalImportRecord]:
        """Return canonical records from the upstream provider."""

    async def ensure_fresh_token(self) -> bool:
        """Refresh the stored OAuth access token when it is expired or near expiry.

        OAuth access tokens are short-lived (Google/Microsoft ~1h); the stored
        ``access_token`` must be refreshed from the long-lived ``refresh_token``
        before each pull or the provider returns 401. Providers that override
        this mutate ``self.payload`` in place and return ``True`` so the caller
        re-persists it.

        The default is a no-op for connectors that authenticate without an
        expiring token (e.g. iCloud app-specific passwords).
        """
        return False

    async def refresh_access_token(self) -> bool:
        """Force-refresh the provider access token, if supported."""
        return False


class ConnectorReconnectRequiredError(RuntimeError):
    """Raised when stored OAuth consent is no longer refreshable."""


def token_needs_refresh(payload: dict[str, Any], *, skew_seconds: int = 300) -> bool:
    """True when the access token is missing, unparseable, or within ``skew_seconds`` of expiry.

    The skew window refreshes a token that is still technically valid but would
    expire mid-pull, avoiding a 401 on a long paginated fetch.
    """
    expires_at = payload.get("expires_at")
    if not expires_at:
        return True
    try:
        expiry = datetime.fromisoformat(str(expires_at))
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return expiry <= datetime.now(UTC) + timedelta(seconds=skew_seconds)


def apply_refreshed_token(payload: dict[str, Any], refreshed: dict[str, Any]) -> None:
    """Merge a refreshed token response into ``payload`` in place.

    Providers commonly omit the refresh token (Google) and sometimes the scope
    on a refresh response, so the prior values are preserved when absent.
    """
    payload["access_token"] = refreshed["access_token"]
    payload["expires_at"] = refreshed["expires_at"]
    if refreshed.get("refresh_token"):
        payload["refresh_token"] = refreshed["refresh_token"]
    if refreshed.get("scopes"):
        payload["scopes"] = refreshed["scopes"]


def encrypt_payload(payload: dict[str, Any]) -> bytes:
    return _fernet().encrypt(json.dumps(payload, sort_keys=True, default=str).encode())


def decrypt_payload(value: bytes) -> dict[str, Any]:
    decoded = json.loads(_fernet().decrypt(value).decode())
    if not isinstance(decoded, dict):
        raise ValueError("connector payload is not an object")
    return decoded


async def run_connector_pull(
    *,
    ctx: MCPContext,
    connector_id: uuid.UUID,
    connector: Connector,
    dry_run: bool,
    triggered_by: Literal["user", "schedule", "agent"] = "user",
) -> tuple[uuid.UUID, ConnectorRunResult]:
    run_id = await _start_run(ctx, connector_id, triggered_by)
    result = ConnectorRunResult()
    try:
        # Refresh the upstream OAuth token if it has expired, BEFORE pulling.
        # Persisted in its own committed session so a freshly minted token is
        # not lost when the request transaction rolls back on a later error.
        await _refresh_and_persist_if_needed(
            tenant_id=ctx.tenant_id,
            connector_id=connector_id,
            connector=connector,
            force=False,
        )
        try:
            records = await connector.pull(ctx, since=None)
        except Exception as exc:
            if not _is_provider_unauthorized(exc):
                raise
            refreshed = await _refresh_and_persist_if_needed(
                tenant_id=ctx.tenant_id,
                connector_id=connector_id,
                connector=connector,
                force=True,
            )
            if not refreshed:
                raise
            records = await connector.pull(ctx, since=None)
        seen: set[str] = set()
        upload_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"connector:{connector_id}:{datetime.now(UTC).date()}",
        )
        for index, record in enumerate(records):
            result.parsed_count += 1
            fingerprint = import_fingerprint(record)
            if fingerprint in seen:
                result.deduped_count += 1
                continue
            seen.add(fingerprint)
            if dry_run:
                continue
            await emit_person_create_proposal(
                ctx=ctx,
                record=record,
                upload_id=upload_id,
                source={
                    "action": "connector_pull",
                    "provider": connector.provider,
                    "connector_id": str(connector_id),
                    "connector_run_id": str(run_id),
                    "record_index": index,
                    "source_record_id": record.source_record_id,
                },
            )
            result.proposed_count += 1
        await _finish_run(ctx, connector_id, run_id, result, None)
    except Exception as exc:
        result.errors.append(str(exc))
        # The MCP request transaction (ctx.db) is rolled back when this raises,
        # so the failure must be recorded in an independent committed session or
        # it vanishes from run history and the connector's status.
        await _persist_run_failure(ctx, connector_id, triggered_by, result, str(exc))
        raise
    return run_id, result


async def _refresh_and_persist_if_needed(
    *,
    tenant_id: uuid.UUID,
    connector_id: uuid.UUID,
    connector: Connector,
    force: bool,
) -> bool:
    try:
        changed = (
            await connector.refresh_access_token()
            if force
            else await connector.ensure_fresh_token()
        )
    except Exception as exc:
        if _is_invalid_grant(exc):
            message = (
                "reconnect_required: OAuth refresh failed with invalid_grant; "
                "the account revoked consent or the refresh token expired"
            )
            await _mark_connector_error(
                tenant_id=tenant_id,
                connector_id=connector_id,
                message=message,
            )
            raise ConnectorReconnectRequiredError(message) from exc
        raise
    if changed:
        await _persist_connector_payload(tenant_id, connector_id, connector.payload)
    return changed


def _is_provider_unauthorized(exc: Exception) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401


def _is_invalid_grant(exc: Exception) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    try:
        body = exc.response.json()
    except ValueError:
        body = {}
    if isinstance(body, dict) and body.get("error") == "invalid_grant":
        return True
    return "invalid_grant" in exc.response.text


async def _start_run(
    ctx: MCPContext,
    connector_id: uuid.UUID,
    triggered_by: str,
) -> uuid.UUID:
    row = await ctx.db.execute(
        text(
            """
            INSERT INTO connector_runs (tenant_id, connector_id, triggered_by)
            VALUES (CAST(:tenant_id AS uuid), CAST(:connector_id AS uuid), :triggered_by)
            RETURNING id
            """
        ),
        {
            "tenant_id": str(ctx.tenant_id),
            "connector_id": str(connector_id),
            "triggered_by": triggered_by,
        },
    )
    return uuid.UUID(str(row.scalar_one()))


async def _finish_run(
    ctx: MCPContext,
    connector_id: uuid.UUID,
    run_id: uuid.UUID,
    result: ConnectorRunResult,
    error_message: str | None,
) -> None:
    status = "failed" if error_message else ("partial" if result.errors else "succeeded")
    summary = {
        "parsed": result.parsed_count,
        "proposed": result.proposed_count,
        "deduped": result.deduped_count,
        "skipped": result.skipped_count,
        "errors_count": len(result.errors),
    }
    await ctx.db.execute(
        text(
            """
            UPDATE connector_runs
            SET completed_at = now(), status = :status,
                parsed_count = :parsed, proposed_count = :proposed,
                deduped_count = :deduped, skipped_count = :skipped,
                error_message = CAST(:error_message AS text)
            WHERE id = CAST(:run_id AS uuid)
              AND tenant_id = CAST(:tenant_id AS uuid)
            """
        ),
        {
            "run_id": str(run_id),
            "tenant_id": str(ctx.tenant_id),
            "status": status,
            "parsed": result.parsed_count,
            "proposed": result.proposed_count,
            "deduped": result.deduped_count,
            "skipped": result.skipped_count,
            "error_message": error_message,
        },
    )
    await ctx.db.execute(
        text(
            """
            UPDATE connector_configs
            SET last_pull_at = now(),
                last_pull_summary = CAST(:summary AS jsonb),
                last_error = CAST(:error_message AS text),
                status = CASE
                    WHEN CAST(:error_message AS text) IS NULL THEN 'configured'
                    ELSE 'error'
                END
            WHERE id = CAST(:connector_id AS uuid)
              AND tenant_id = CAST(:tenant_id AS uuid)
            """
        ),
        {
            "connector_id": str(connector_id),
            "tenant_id": str(ctx.tenant_id),
            "summary": json.dumps(summary),
            "error_message": error_message,
        },
    )
    if status == "succeeded" and result.proposed_count > 0:
        asyncio.create_task(_run_post_pull_agent_pipeline(ctx.tenant_id))


async def _persist_connector_payload(
    tenant_id: uuid.UUID,
    connector_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    """Persist a refreshed connector payload in its own committed session.

    A token refreshed mid-request must outlive the request transaction (which
    the MCP layer rolls back on any later error), so it is written and committed
    independently using a stable service identity for RLS/attribution.
    """
    settings = get_settings()
    async with async_session_maker() as db:
        await bind_session_context(
            db, str(tenant_id), "service:connector-token-refresh", settings
        )
        await db.execute(
            text(
                """
                UPDATE connector_configs
                SET encrypted_payload = :payload
                WHERE id = CAST(:connector_id AS uuid)
                  AND tenant_id = CAST(:tenant_id AS uuid)
                """
            ),
            {
                "payload": encrypt_payload(payload),
                "connector_id": str(connector_id),
                "tenant_id": str(tenant_id),
            },
        )
        await db.commit()


async def _mark_connector_error(
    *,
    tenant_id: uuid.UUID,
    connector_id: uuid.UUID,
    message: str,
) -> None:
    settings = get_settings()
    async with async_session_maker() as db:
        await bind_session_context(
            db, str(tenant_id), "service:connector-token-refresh", settings
        )
        await db.execute(
            text(
                """
                UPDATE connector_configs
                SET status = 'error',
                    last_error = :message
                WHERE id = CAST(:connector_id AS uuid)
                  AND tenant_id = CAST(:tenant_id AS uuid)
                """
            ),
            {
                "message": message,
                "connector_id": str(connector_id),
                "tenant_id": str(tenant_id),
            },
        )
        await db.commit()


async def _persist_run_failure(
    ctx: MCPContext,
    connector_id: uuid.UUID,
    triggered_by: str,
    result: ConnectorRunResult,
    error_message: str,
) -> None:
    """Durably record a failed connector run in an independent committed session.

    Mirrors the error branch of :func:`_finish_run`, but commits on its own
    connection so the failed run row and the connector's ``error`` status
    survive the request transaction's rollback.
    """
    settings = get_settings()
    summary = {
        "parsed": result.parsed_count,
        "proposed": result.proposed_count,
        "deduped": result.deduped_count,
        "skipped": result.skipped_count,
        "errors_count": len(result.errors),
    }
    async with async_session_maker() as db:
        await bind_session_context(db, str(ctx.tenant_id), "service:connector-pull", settings)
        await db.execute(
            text(
                """
                INSERT INTO connector_runs
                    (tenant_id, connector_id, triggered_by, completed_at, status,
                     parsed_count, proposed_count, deduped_count, skipped_count, error_message)
                VALUES (CAST(:tenant_id AS uuid), CAST(:connector_id AS uuid), :triggered_by,
                        now(), 'failed', :parsed, :proposed, :deduped, :skipped,
                        CAST(:error_message AS text))
                """
            ),
            {
                "tenant_id": str(ctx.tenant_id),
                "connector_id": str(connector_id),
                "triggered_by": triggered_by,
                "parsed": result.parsed_count,
                "proposed": result.proposed_count,
                "deduped": result.deduped_count,
                "skipped": result.skipped_count,
                "error_message": error_message,
            },
        )
        await db.execute(
            text(
                """
                UPDATE connector_configs
                SET last_pull_at = now(),
                    last_pull_summary = CAST(:summary AS jsonb),
                    last_error = CAST(:error_message AS text),
                    status = 'error'
                WHERE id = CAST(:connector_id AS uuid)
                  AND tenant_id = CAST(:tenant_id AS uuid)
                """
            ),
            {
                "connector_id": str(connector_id),
                "tenant_id": str(ctx.tenant_id),
                "summary": json.dumps(summary),
                "error_message": error_message,
            },
        )
        await db.commit()


async def _run_post_pull_agent_pipeline(tenant_id: uuid.UUID) -> None:
    # Let the request transaction commit before the independent worker session reads proposals.
    await asyncio.sleep(0.1)
    settings = get_settings()
    # Pure background worker (post-pull agent pipeline) — no human uc_uid in
    # scope. Bind a stable service identity so attribution is never null.
    async with async_session_maker() as db:
        await bind_session_context(
            db, str(tenant_id), "service:connector-pull-pipeline", settings
        )
        try:
            await run_dedup_agent(db, tenant_id=tenant_id)
            await run_quality_filter(db, tenant_id=tenant_id)
            await run_confidence_approver(db, tenant_id=tenant_id)
            await db.commit()
        except Exception:
            await db.rollback()
            raise


def _fernet() -> Fernet:
    key = get_settings().CONNECTOR_ENCRYPTION_KEY
    if not key:
        raise RuntimeError("CONNECTOR_ENCRYPTION_KEY is required for connector encryption")
    return Fernet(key.encode())
