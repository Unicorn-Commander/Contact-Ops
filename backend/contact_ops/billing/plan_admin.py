"""Plan-tier lifecycle — the ONLY runtime writers of ``tenants.plan_tier``.

Before this module nothing changed ``tenants.plan_tier`` at runtime: migration
0041 added the column (new tenants default ``'free'``, existing ones were
grandfathered to ``'enterprise'``) and no code path ever issued an UPDATE. New
self-served tenants were therefore permanently ``'free'`` and no payment event
could grant entitlement. This module closes that gap with two writers, both of
which route through :func:`set_tenant_plan_tier`:

  * the operator CLI (``contact_ops.billing.plan_cli``) — a human moving a tenant
    between tiers by hand;
  * the Lago webhook (``contact_ops.api.webhooks``) — a payment event
    (``subscription.created`` / ``terminated``) driving entitlement.

Why the ADMIN (BYPASSRLS) DSN and not the app role: the ``tenants_modify`` RLS
policy (migration 0015) lets an app-role session UPDATE its OWN tenant row
(``id = current_tenant_id()``), and the table-level UPDATE grant covers the
``plan_tier`` column added later by 0041. That means an app-role session bound to
tenant X could ``UPDATE tenants SET plan_tier='enterprise'`` — a self-service
privilege escalation. Plan changes are an OPERATOR / billing-substrate decision,
never a tenant-reachable one, so every write here goes through the migration
superuser DSN (``MIGRATION_DATABASE_URL``, BYPASSRLS) which is only ever wielded
by out-of-band operator tooling and the authenticated webhook — NEVER by a
request carrying a tenant JWT.

Every change writes an append-only ``action_event`` (aggregate_type='tenant',
event_type='tenant.plan_tier_change') carrying before/after tiers, the actor, the
source, and a reason — the money audit trail. Writes are transactional and
idempotent: setting a tenant to the tier it already holds is a no-op that emits
no event.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from contact_ops.billing.catalog import DEFAULT_TIER
from contact_ops.core.config import Settings

logger = structlog.get_logger(__name__)

# The only tiers a plan_tier may hold — mirrors the CHECK constraint added by
# migration 0041 (tenants_plan_tier_check) and entitlement.TIER_LADDER's keys.
# We validate against this in-process too so a bad tier is rejected with a clear
# message BEFORE it hits the DB (where it would surface as an opaque CHECK
# violation).
VALID_TIERS: frozenset[str] = frozenset({"free", "pro", "enterprise"})

# action_event descriptors for a plan-tier change.
_EVENT_TYPE = "tenant.plan_tier_change"
_AGGREGATE_TYPE = "tenant"


class PlanTierError(ValueError):
    """Raised for a caller error (unknown tenant, invalid tier, no admin DSN)."""


@dataclass(frozen=True)
class PlanTierChange:
    """Result of a :func:`set_tenant_plan_tier` call.

    Attributes:
        tenant_id: the tenant whose tier was evaluated.
        before: the tier held before the call.
        after: the tier held after the call (== ``before`` when ``changed`` is
            False).
        changed: True when the UPDATE + audit event actually ran; False on the
            idempotent no-op (already on the requested tier).
        event_id: the ``action_event.event_id`` written, or None on a no-op.
    """

    tenant_id: uuid.UUID
    before: str
    after: str
    changed: bool
    event_id: uuid.UUID | None


def _admin_async_url(settings: Settings) -> str | None:
    """The migration/admin DSN normalised to the asyncpg driver, or None.

    Same idiom as ``agents/retention_sweep._admin_async_url`` and
    ``services/_bootstrap_cli._admin_async_url``: the runtime role is NOBYPASSRLS
    and could self-upgrade its own tenant under ``tenants_modify``, so plan writes
    MUST use the BYPASSRLS ``MIGRATION_DATABASE_URL``. Returns None when it is
    unset — the caller then refuses to write rather than falling back to the
    app role.
    """
    raw = settings.MIGRATION_DATABASE_URL
    if not raw:
        return None
    scheme, _, rest = raw.partition("://")
    if not rest:
        return raw
    return f"postgresql+asyncpg://{rest}"


async def _write_change(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    new_tier: str,
    actor: str,
    reason: str,
    source: str,
) -> PlanTierChange:
    """Do the read-lock -> compare -> UPDATE + audit inside an open session.

    Split out from :func:`set_tenant_plan_tier` so tests can drive it against an
    injected (BYPASSRLS) session without spinning a second engine.
    """
    current = await session.execute(
        text("SELECT plan_tier FROM tenants WHERE id = CAST(:t AS uuid) FOR UPDATE"),
        {"t": str(tenant_id)},
    )
    before = current.scalar()
    if before is None:
        raise PlanTierError(f"tenant {tenant_id} not found")
    before = str(before)

    if before == new_tier:
        # Idempotent no-op: already on the requested tier. Emit NO event — the
        # audit log records real transitions only.
        logger.info(
            "plan_tier_noop",
            tenant_id=str(tenant_id),
            tier=new_tier,
            source=source,
        )
        return PlanTierChange(
            tenant_id=tenant_id,
            before=before,
            after=before,
            changed=False,
            event_id=None,
        )

    await session.execute(
        text(
            "UPDATE tenants SET plan_tier = :tier, updated_at = now() "
            "WHERE id = CAST(:t AS uuid)"
        ),
        {"tier": new_tier, "t": str(tenant_id)},
    )

    # Append-only audit row. Written under the admin DSN which BYPASSRLS, so the
    # ae_audit_insert WITH CHECK (tenant_id = current_tenant_id()) does not apply;
    # we still set tenant_id correctly and hash-chain off the tenant's last event,
    # matching mcp.audit_helper.emit_action_event so the chain stays continuous.
    payload = {
        "before": {"plan_tier": before},
        "after": {"plan_tier": new_tier},
    }
    payload_bytes = json.dumps(
        payload, sort_keys=True, default=str, separators=(",", ":")
    ).encode()
    content_hash = hashlib.sha256(payload_bytes).digest()
    # Chain off the tenant's most recent event. event_id (uuidv7) is the
    # tiebreak: within a single transaction every row shares one
    # transaction_timestamp() proposed_at, so ordering by proposed_at alone is
    # ambiguous for back-to-back writes; the monotonic uuidv7 event_id resolves
    # it so the hash chain is deterministic even for multiple changes in one txn.
    prev_hash = await session.scalar(
        text(
            "SELECT content_hash FROM action_event WHERE tenant_id = CAST(:t AS uuid) "
            "ORDER BY proposed_at DESC, event_id DESC LIMIT 1"
        ),
        {"t": str(tenant_id)},
    )

    actor_chain = {"sub": actor, "source": source}
    evidence = {"source": source, "reason": reason}
    row = await session.execute(
        text(
            """
            INSERT INTO action_event
                (event_type, tenant_id, aggregate_type, aggregate_id,
                 payload, actor, actor_type, rationale, evidence, status,
                 content_hash, prev_event_hash, applied_at)
            VALUES
                (:event_type, CAST(:tenant AS uuid), CAST(:aggregate_type AS entity_kind),
                 CAST(:tenant AS uuid), CAST(:payload AS jsonb), CAST(:actor AS jsonb),
                 CAST(:actor_type AS actor_type), :rationale, CAST(:evidence AS jsonb),
                 CAST('applied' AS event_status), :content_hash, :prev_hash, now())
            RETURNING event_id
            """
        ),
        {
            "event_type": _EVENT_TYPE,
            "tenant": str(tenant_id),
            "aggregate_type": _AGGREGATE_TYPE,
            "payload": json.dumps(payload),
            "actor": json.dumps(actor_chain),
            # A payment event is an external system; an operator CLI run is a
            # human decision. Both are non-agent so confidence stays NULL.
            "actor_type": (
                "external_system" if source == "lago_webhook" else "human"
            ),
            "rationale": reason,
            "evidence": json.dumps(evidence),
            "content_hash": content_hash,
            "prev_hash": prev_hash,
        },
    )
    event_id = row.scalar_one()
    logger.info(
        "plan_tier_changed",
        tenant_id=str(tenant_id),
        before=before,
        after=new_tier,
        source=source,
        event_id=str(event_id),
    )
    return PlanTierChange(
        tenant_id=tenant_id,
        before=before,
        after=new_tier,
        changed=True,
        event_id=event_id,
    )


async def set_tenant_plan_tier(
    *,
    tenant_id: uuid.UUID,
    new_tier: str,
    settings: Settings,
    actor: str,
    reason: str,
    source: str,
    session: AsyncSession | None = None,
) -> PlanTierChange:
    """Set a tenant's plan tier under the admin DSN with an audit event.

    This is the single chokepoint both the operator CLI and the Lago webhook use.
    It validates ``new_tier``, then (unless a ``session`` is injected for tests)
    opens a short-lived engine on the BYPASSRLS ``MIGRATION_DATABASE_URL`` and
    performs the change transactionally.

    Args:
        tenant_id: tenant to move.
        new_tier: target tier — must be one of :data:`VALID_TIERS`.
        settings: app settings (for the admin DSN).
        actor: who/what initiated it (an operator id, or ``"lago_webhook"``);
            recorded in the audit event's actor chain.
        reason: human-readable justification, recorded on the event.
        source: ``"operator_cli"`` or ``"lago_webhook"`` — sets the audit
            actor_type and appears in the evidence.
        session: an already-open BYPASSRLS session (tests only). When provided,
            the caller owns the transaction (commit/rollback); when omitted, this
            function owns a self-contained transaction.

    Returns:
        A :class:`PlanTierChange` describing the (possibly no-op) transition.

    Raises:
        PlanTierError: unknown tier, missing admin DSN, or unknown tenant.
    """
    if new_tier not in VALID_TIERS:
        raise PlanTierError(
            f"invalid tier {new_tier!r}; expected one of {sorted(VALID_TIERS)}"
        )

    if session is not None:
        # Caller-owned transaction (tests inject a BYPASSRLS session).
        return await _write_change(
            session,
            tenant_id=tenant_id,
            new_tier=new_tier,
            actor=actor,
            reason=reason,
            source=source,
        )

    admin_url = _admin_async_url(settings)
    if not admin_url:
        raise PlanTierError(
            "MIGRATION_DATABASE_URL unset; the runtime role cannot safely write "
            "plan_tier (RLS would let a tenant self-upgrade). Configure the admin "
            "DSN to change plan tiers."
        )

    engine = create_async_engine(admin_url)
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as owned:
            try:
                result = await _write_change(
                    owned,
                    tenant_id=tenant_id,
                    new_tier=new_tier,
                    actor=actor,
                    reason=reason,
                    source=source,
                )
                await owned.commit()
                return result
            except Exception:
                await owned.rollback()
                raise
    finally:
        await engine.dispose()


async def provision_lago_customer(
    *,
    tenant_id: uuid.UUID | str,
    plan_tier: str,
    settings: Settings,
) -> None:
    """Best-effort: ensure a Lago customer exists for a freshly provisioned tenant.

    Called at tenant provisioning so a tenant's later metered usage events do not
    422 ("unknown customer") and get dropped — before this, ``ensure_customer``
    had ZERO callers, so no tenant ever had a Lago customer record. Delegates to
    the configured billing provider, which is DORMANT by default (the ``manual``
    provider and the unconfigured Lago provider both no-op with a
    ``billing_would_ensure_customer`` log), so this is inert until billing is
    wired up.

    NEVER raises — provisioning a customer is a side channel that must not fail a
    signup. Any error is swallowed to a warning, matching the Garage-provision
    posture at the onboard call site.
    """
    # Imported lazily: ``provider`` imports ``contact_ops.mcp.entitlement``, which
    # triggers MCP tool registration (including tenancy.py, which imports THIS
    # module). A top-level import would create a circular import at package load.
    from contact_ops.billing.provider import get_billing_provider

    try:
        provider = get_billing_provider(settings)
        await provider.ensure_customer(str(tenant_id), plan_tier or DEFAULT_TIER)
    except Exception as exc:  # noqa: BLE001 — customer provisioning is best-effort
        logger.warning(
            "lago_ensure_customer_failed",
            tenant_id=str(tenant_id),
            error=type(exc).__name__,
        )
