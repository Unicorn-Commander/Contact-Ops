"""inbox mutations — approve/reject/snooze + bulk + revert + keep-both.

Each function returns the freshly-written ``inbox_decisions`` row id and
updates ``action_event.status`` (and ``snoozed_until``) as appropriate.
Per Aaron's B3, the server is source-of-truth for typed-phrase validation,
tier selection, and skip-reason classification — never trusts the client.

Phase 3.3a scope: this module records the *decision* faithfully and flips
``action_event.status``. Downstream effect application (the actual person
merge for a dedup approval, the field-promotion for a "keep both", the
agent-callback for a "revert") is the responsibility of the agent that
produced the proposal (Dedup, Voice Match, etc. read applied/reverted
decisions and effect them in their next sweep). The ``inbox_decisions``
row captures everything needed for that downstream work.

The trust-ladder math (alpha/beta posterior update) lives in the
Calibration Daemon (Phase 3.4). This module just stamps the decision.
"""
# ruff: noqa: E501

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

from contact_ops.schemas.inbox import (
    BulkSkipReason,
    RejectMode,
    Tier,
)
from contact_ops.services.downstream_apply import (
    apply_downstream_effect,
)
from contact_ops.services.inbox_query import get_proposal_for_decision
from contact_ops.services.suppression_rules import create_suppression_rule

# B4: server undo window = 30s. Longer than client's 4s to absorb network.
UNDO_WINDOW = timedelta(seconds=30)

# B8: T0 revert window = 5min per Aaron's spec.
REVERT_WINDOW = timedelta(minutes=5)


class InboxMutationError(ValueError):
    """Raised when an inbox mutation is rejected by server-side validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---- typed-phrase derivation + validation ----


def _expected_phrase(
    *,
    is_hipaa: bool,
    is_delete: bool,
    is_cross_tenant: bool,
    bulk_count: int,
    entity_display_name: str | None,
    source_tenant_slug: str | None,
    target_tenant_slug: str | None,
) -> str | None:
    """Return the typed phrase the user must enter for a Tier-4 approve.

    None means no typed-phrase gate (caller is Tier ≤ 3). The order
    mirrors design doc §11.6 Tier-4 triggers.
    """
    if is_delete and entity_display_name:
        return entity_display_name
    if is_hipaa:
        return "approve hipaa"
    if bulk_count > 10:
        return f"approve {bulk_count} items"
    if is_cross_tenant and source_tenant_slug and target_tenant_slug:
        return f"{source_tenant_slug} to {target_tenant_slug}"
    return None


def _phrases_match(supplied: str | None, expected: str | None) -> bool:
    """Case-insensitive + whitespace-trimmed; exact otherwise per B3."""
    if expected is None:
        return True
    if supplied is None:
        return False
    return supplied.strip().casefold() == expected.strip().casefold()


# ---- core: write_inbox_decision ----


async def _write_inbox_decision(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    proposal_id: uuid.UUID,
    cluster_id: uuid.UUID | None,
    reviewer_id: uuid.UUID,
    decision: str,
    tier_assigned: int,
    time_to_decide_sec: int | None,
    keyboard_path: bool,
    typed_phrase_used: bool,
    field_choices: dict[str, Any] | None,
    agent_confidence: float | None,
) -> uuid.UUID:
    result = await db.execute(
        text(
            """
            INSERT INTO inbox_decisions (
                tenant_id, proposal_id, cluster_id, reviewer_id,
                decision, tier_assigned, time_to_decide_sec,
                keyboard_path, typed_phrase_used, field_choices,
                agent_confidence
            ) VALUES (
                CAST(:tenant AS uuid), CAST(:proposal AS uuid),
                CAST(:cluster AS uuid), CAST(:reviewer AS uuid),
                :decision, :tier, :ttd,
                :kb, :typed, CAST(:choices AS jsonb),
                :conf
            )
            RETURNING id
            """
        ),
        {
            "tenant": str(tenant_id),
            "proposal": str(proposal_id),
            "cluster": str(cluster_id) if cluster_id else None,
            "reviewer": str(reviewer_id),
            "decision": decision,
            "tier": tier_assigned,
            "ttd": time_to_decide_sec,
            "kb": keyboard_path,
            "typed": typed_phrase_used,
            "choices": json.dumps(field_choices) if field_choices else None,
            "conf": agent_confidence,
        },
    )
    return uuid.UUID(str(result.scalar_one()))


# ---- approve ----


async def approve_proposal(
    *,
    db: AsyncSession,
    app_db: AsyncSession | None = None,
    tenant_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    proposal_id: uuid.UUID,
    tier_assigned: Tier,
    typed_phrase: str | None,
    field_choices: dict[str, Any] | None,
    custom_values: dict[str, Any] | None,
    time_to_decide_sec: int | None,
    keyboard_path: bool,
    typed_phrase_used: bool,
) -> dict[str, Any]:
    """Apply a proposal. Returns ``{applied, action_event_id, inbox_decision_id}``.

    Server-side validation:
    * proposal must exist and belong to caller's tenant
    * proposal must be in status='proposed' (rejects already-resolved)
    * tier_assigned must agree with server-computed tier (raises STALE_TIER_POLICY)
    * if server-computed tier == 4: typed_phrase must match expected phrase
    """
    row = await get_proposal_for_decision(db=db, proposal_id=proposal_id, tenant_id=tenant_id)
    if row is None:
        raise InboxMutationError("PROPOSAL_NOT_FOUND", "proposal not found in this tenant")
    if row["status"] != "proposed":
        raise InboxMutationError(
            "ALREADY_RESOLVED",
            f"proposal is in status={row['status']}, cannot approve",
        )

    server_tier = _compute_tier(row)
    if tier_assigned != server_tier:
        raise InboxMutationError(
            "STALE_TIER_POLICY",
            f"client tier {tier_assigned} disagrees with server {server_tier}",
        )

    if server_tier == 4:
        expected = _expected_phrase(
            is_hipaa=bool(row["tenant_hipaa_mode"]),
            is_delete=row["event_type"] in {"contact.delete", "tag.remove", "edge.remove", "person.delete", "organization.delete"},
            is_cross_tenant=row["target_tenant_id"] is not None,
            bulk_count=_bulk_count_of(row),
            entity_display_name=None,  # delete-mode in single approve uses HIPAA phrase fallback
            source_tenant_slug=None,
            target_tenant_slug=None,
        )
        if not _phrases_match(typed_phrase, expected):
            raise InboxMutationError(
                "TYPED_PHRASE_MISMATCH",
                f"typed phrase did not match expected (expected {expected!r})",
            )

    if row["event_type"] == "person.create":
        await _apply_person_create_proposal(
            db=app_db or db,
            tenant_id=tenant_id,
            proposal_id=proposal_id,
        )

    # Flip action_event to applied
    await db.execute(
        text(
            """
            UPDATE action_event
            SET status = 'applied', applied_at = now(), approved_by = CAST(:reviewer AS uuid),
                decided_by_user_id = CAST(:reviewer AS uuid),
                time_to_decide_seconds = COALESCE(:ttd, time_to_decide_seconds),
                snoozed_until = NULL
            WHERE event_id = CAST(:id AS uuid)
            """
        ),
        {
            "id": str(proposal_id),
            "reviewer": str(reviewer_id),
            "ttd": time_to_decide_sec,
        },
    )

    # Realize the proposal's real-world effect when it needs more than a status
    # flip. A dedup.propose_merge approval actually merges the two person records
    # via the canonical merge_people path; without this the contacts stayed
    # duplicated and calibration learned a positive outcome for a merge that
    # never happened. Runs in this same transaction (db == audit, app_db == app),
    # so a merge failure raises and rolls the approval + outcome back.
    merge_result = await apply_downstream_effect(
        event_type=row["event_type"],
        app_db=app_db or db,
        audit_db=db,
        tenant_id=tenant_id,
        payload=row["payload"],
        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
        actor_chain={"sub": str(reviewer_id)},
        human_authority=str(reviewer_id),
        request_id="inbox-approve",
    )
    if merge_result is not None:
        logger.info(
            "proposal_downstream_effect_applied",
            proposal_id=str(proposal_id),
            event_type=row["event_type"],
            result=merge_result,
        )

    decision_id = await _write_inbox_decision(
        db=db,
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        cluster_id=None,
        reviewer_id=reviewer_id,
        decision="approve",
        tier_assigned=server_tier,
        time_to_decide_sec=time_to_decide_sec,
        keyboard_path=keyboard_path,
        typed_phrase_used=typed_phrase_used,
        field_choices={
            "choices": field_choices,
            "custom_values": custom_values,
        } if field_choices or custom_values else None,
        agent_confidence=float(row["confidence"]) if row["confidence"] is not None else None,
    )

    return {
        "applied": True,
        "action_event_id": proposal_id,
        "inbox_decision_id": decision_id,
    }


async def _apply_person_create_proposal(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    proposal_id: uuid.UUID,
    graph_client: Any = None,
) -> None:
    row = await db.execute(
        text(
            """
            SELECT aggregate_id, payload, decision_payload
            FROM action_event
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND event_id = CAST(:proposal_id AS uuid)
              AND event_type = 'person.create'
            """
        ),
        {"tenant_id": str(tenant_id), "proposal_id": str(proposal_id)},
    )
    proposal = row.mappings().first()
    if proposal is None:
        raise InboxMutationError("PROPOSAL_NOT_FOUND", "person.create proposal not found")

    person_id = uuid.UUID(str(proposal["aggregate_id"]))
    existing = await db.scalar(
        text(
            """
            SELECT id
            FROM persons
            WHERE id = CAST(:person_id AS uuid)
              AND canonical_owner_tenant_id = CAST(:tenant_id AS uuid)
            """
        ),
        {"person_id": str(person_id), "tenant_id": str(tenant_id)},
    )
    if existing is not None:
        return

    payload = _payload_after(proposal["payload"], proposal["decision_payload"])
    display_name = str(payload.get("display_name") or "Unnamed Contact")
    await db.execute(
        text(
            """
            INSERT INTO persons (
                id, display_name, given_name, family_name, additional_names,
                honorific_prefix, honorific_suffix, nicknames, birthday,
                headline, occupation_title, canonical_owner_tenant_id,
                vcard_uid, etag
            ) VALUES (
                CAST(:id AS uuid), :display_name, :given_name, :family_name,
                CAST(:additional_names AS text[]), :honorific_prefix,
                :honorific_suffix, CAST(:nicknames AS text[]),
                CAST(:birthday AS jsonb), :headline, :occupation_title,
                CAST(:tenant_id AS uuid), :vcard_uid, :etag
            )
            """
        ),
        {
            "id": str(person_id),
            "display_name": display_name,
            "given_name": payload.get("given_name"),
            "family_name": payload.get("family_name"),
            "additional_names": payload.get("additional_names") or [],
            "honorific_prefix": payload.get("honorific_prefix"),
            "honorific_suffix": payload.get("honorific_suffix"),
            "nicknames": payload.get("nicknames") or [],
            "birthday": json.dumps(payload.get("birthday")) if payload.get("birthday") else None,
            "headline": payload.get("headline"),
            "occupation_title": payload.get("occupation_title"),
            "tenant_id": str(tenant_id),
            "vcard_uid": f"urn:uuid:{person_id}",
            "etag": str(uuid.uuid4()),
        },
    )
    await db.execute(
        text(
            """
            INSERT INTO person_tenant_membership (
                person_id, tenant_id, notes, tags
            ) VALUES (
                CAST(:person_id AS uuid), CAST(:tenant_id AS uuid),
                :notes, CAST(:tags AS text[])
            )
            ON CONFLICT (person_id, tenant_id) DO NOTHING
            """
        ),
        {
            "person_id": str(person_id),
            "tenant_id": str(tenant_id),
            "notes": payload.get("notes"),
            "tags": payload.get("tags") or [],
        },
    )
    await _insert_person_children(db, person_id, payload)
    # P0: approved contacts must appear in the relationship graph. The approval
    # path wrote Postgres only — the FalkorDB node was never created, so approved
    # people were invisible in the graph view. Sync it now (best-effort). A
    # caller (bulk_approve) may pass a shared client to avoid per-contact setup.
    await _sync_person_to_graph(db, tenant_id, person_id, payload, client=graph_client)


async def _sync_person_to_graph(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    person_id: uuid.UUID,
    payload: dict[str, Any],
    *,
    client: Any = None,
) -> None:
    """Best-effort upsert of an approved contact into the tenant's FalkorDB graph
    so it shows up in the relationship view immediately. Postgres is the source of
    truth; a graph-sync failure must NEVER fail the approval. This mirrors the
    synchronous graph-write pattern already used by enrichment.py and the dedup
    merge executor (the async graph_sync_outbox is unwired on this branch).

    Pass ``client`` (a connected FalkorDBGraphClient) to REUSE one connection
    across a batch — bulk_approve does this so a 100-contact approval does one
    client + one bootstrap instead of 100. When ``client`` is None we own a
    fresh client (and bootstrap it); a shared client is bootstrapped once by
    the caller, so we skip the 6 redundant CREATE INDEX round-trips here."""
    try:
        from contact_ops.agents.graph_sync.cypher_writes import build_write
        from contact_ops.agents.graph_sync.falkordb_client import (
            FalkorDBGraphClient,
            TenantGraph,
            graph_name_for_slug,
        )
        from contact_ops.models import Tenant

        tenant = await db.get(Tenant, tenant_id)
        if tenant is None:
            return
        graph_name = tenant.graph_name or graph_name_for_slug(tenant.slug)
        tg = TenantGraph(
            tenant_id=tenant_id, graph_name=graph_name, graph_mode=tenant.graph_mode
        )
        # Pull the just-inserted primary email/phone for the node props (same
        # shape graph_backfill.py writes), so the node matches the rest of the graph.
        primary_email = await db.scalar(
            text(
                "SELECT address FROM emails WHERE person_id = CAST(:p AS uuid) "
                "ORDER BY is_primary DESC, created_at LIMIT 1"
            ),
            {"p": str(person_id)},
        )
        primary_phone = await db.scalar(
            text(
                "SELECT e164 FROM phones WHERE person_id = CAST(:p AS uuid) "
                "ORDER BY is_primary DESC, created_at LIMIT 1"
            ),
            {"p": str(person_id)},
        )
        node = {
            "id": str(person_id),
            "tenant_id": str(tenant_id),
            "display_name": str(payload.get("display_name") or "Unnamed Contact"),
            "given_name": payload.get("given_name"),
            "family_name": payload.get("family_name"),
            "primary_email": primary_email,
            "primary_phone": primary_phone,
            "linkedin_url": None,
            "updated_at": datetime.now(UTC).isoformat(),
            "confidence": 1.0,
            "provenance_event_id": None,
        }
        own_client = client is None
        gc = client if client is not None else FalkorDBGraphClient()
        try:
            if own_client:
                await gc.bootstrap_graph(tg)
            w = build_write("person", "upsert", node)
            await gc.query(tg, w.cypher, w.params)
        finally:
            if own_client:
                await gc.close()
    except Exception:  # noqa: BLE001 — graph sync is advisory; never fail approval
        logger.warning("approval_graph_sync_failed", person_id=str(person_id), exc_info=True)


def _payload_after(payload: Any, decision_payload: Any) -> dict[str, Any]:
    if isinstance(decision_payload, str):
        decision_payload = json.loads(decision_payload)
    if isinstance(decision_payload, dict):
        after = decision_payload.get("payload_after")
        if isinstance(after, dict):
            return after
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, dict):
        after = payload.get("after")
        if isinstance(after, dict):
            return after
    return {}


async def _insert_person_children(
    db: AsyncSession,
    person_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    for index, email in enumerate(_list_of_dicts(payload.get("emails"))):
        await db.execute(
            text(
                """
                INSERT INTO emails (person_id, address, type, label, is_primary, confidence)
                VALUES (
                    CAST(:person_id AS uuid), lower(:address),
                    CAST(:type AS email_type), :label, :is_primary, :confidence
                )
                """
            ),
            {
                "person_id": str(person_id),
                "address": str(email.get("address") or ""),
                "type": _email_type(str(email.get("type") or "other")),
                "label": email.get("label"),
                "is_primary": bool(email.get("is_primary")) or index == 0,
                "confidence": 0.9,
            },
        )
    for index, phone in enumerate(_list_of_dicts(payload.get("phones"))):
        await db.execute(
            text(
                """
                INSERT INTO phones (person_id, e164, type, label, is_primary, confidence)
                VALUES (
                    CAST(:person_id AS uuid), :e164,
                    CAST(:type AS phone_type), :label, :is_primary, :confidence
                )
                """
            ),
            {
                "person_id": str(person_id),
                "e164": str(phone.get("e164") or ""),
                "type": _phone_type(str(phone.get("type") or "other")),
                "label": phone.get("label"),
                "is_primary": bool(phone.get("is_primary")) or index == 0,
                "confidence": 0.9,
            },
        )
    for index, address in enumerate(_list_of_dicts(payload.get("addresses"))):
        await db.execute(
            text(
                """
                INSERT INTO postal_addresses (
                    person_id, type, label, is_primary, po_box, street_address,
                    extended_address, locality, region, postal_code, country_name,
                    country_code, confidence
                ) VALUES (
                    CAST(:person_id AS uuid), CAST(:type AS address_type), :label,
                    :is_primary, :po_box, :street_address, :extended_address,
                    :locality, :region, :postal_code, :country_name, :country_code,
                    :confidence
                )
                """
            ),
            {
                "person_id": str(person_id),
                "type": _address_type(str(address.get("type") or "other")),
                "label": address.get("label"),
                "is_primary": bool(address.get("is_primary")) or index == 0,
                "po_box": address.get("po_box"),
                "street_address": address.get("street_address"),
                "extended_address": address.get("extended_address"),
                "locality": address.get("locality"),
                "region": address.get("region"),
                "postal_code": address.get("postal_code"),
                "country_name": address.get("country_name"),
                "country_code": address.get("country_code"),
                "confidence": 0.9,
            },
        )
    for identifier in _list_of_dicts(payload.get("identifiers")):
        await db.execute(
            text(
                """
                INSERT INTO identifiers (person_id, namespace, value, url, verified, confidence)
                VALUES (
                    CAST(:person_id AS uuid), :namespace, :value, :url, :verified, :confidence
                )
                """
            ),
            {
                "person_id": str(person_id),
                "namespace": str(identifier.get("namespace") or "external"),
                "value": str(identifier.get("value") or ""),
                "url": identifier.get("url"),
                "verified": bool(identifier.get("verified")),
                "confidence": 0.9,
            },
        )


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _email_type(value: str) -> str:
    if value == "home":
        return "personal"
    if value in {"personal", "work", "school", "other", "alias"}:
        return value
    return "other"


def _phone_type(value: str) -> str:
    if value in {"mobile", "home", "work", "fax", "main", "other"}:
        return value
    return "other"


def _address_type(value: str) -> str:
    if value in {"home", "work", "billing", "shipping", "mailing", "other"}:
        return value
    return "other"


# ---- reject ----


async def reject_proposal(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    proposal_id: uuid.UUID,
    mode: RejectMode,
    reason: str | None,
    tier_assigned: Tier,
    time_to_decide_sec: int | None,
    keyboard_path: bool,
    suppression_aggregate_id: uuid.UUID | None,
    suppression_field_name: str | None,
    suppression_expires_at: datetime | None,
) -> dict[str, Any]:
    """Reject a proposal with mode dispatch. See RejectMode for semantics."""
    row = await get_proposal_for_decision(db=db, proposal_id=proposal_id, tenant_id=tenant_id)
    if row is None:
        raise InboxMutationError("PROPOSAL_NOT_FOUND", "proposal not found in this tenant")

    suppression_rule_id: uuid.UUID | None = None
    decision_label = "reject"

    if mode == "undo":
        # Undo flips an "applied" decision back to rejected within 30s.
        if row["status"] != "applied":
            raise InboxMutationError(
                "NOT_UNDOABLE",
                f"undo only works on status=applied; got {row['status']}",
            )
        applied_at = row["applied_at"]
        if applied_at is None:
            raise InboxMutationError("NOT_UNDOABLE", "applied_at is missing")
        age = datetime.now(UTC) - (applied_at if applied_at.tzinfo else applied_at.replace(tzinfo=UTC))
        if age > UNDO_WINDOW:
            raise InboxMutationError(
                "UNDO_WINDOW_EXPIRED",
                f"undo window expired ({age.total_seconds():.1f}s > {UNDO_WINDOW.total_seconds()}s)",
            )
        await db.execute(
            text(
                """
                UPDATE action_event
                SET status = 'rejected', decided_by_user_id = CAST(:reviewer AS uuid),
                    applied_at = NULL
                WHERE event_id = CAST(:id AS uuid)
                """
            ),
            {"id": str(proposal_id), "reviewer": str(reviewer_id)},
        )
        decision_label = "undo"
    elif mode == "mute":
        # Mute creates a suppression rule (the proposal itself rejects too).
        if row["status"] != "proposed":
            raise InboxMutationError(
                "ALREADY_RESOLVED",
                f"cannot mute resolved proposal (status={row['status']})",
            )
        suppression_rule_id = await create_suppression_rule(
            db=db,
            tenant_id=tenant_id,
            agent_slug=str(row["agent_slug"]),
            aggregate_type=str(row["aggregate_type"]),
            aggregate_id=suppression_aggregate_id or uuid.UUID(str(row["aggregate_id"])),
            field_name=suppression_field_name,
            created_by=reviewer_id,
            expires_at=suppression_expires_at,
            note=reason,
        )
        await _mark_rejected(db=db, proposal_id=proposal_id, reviewer_id=reviewer_id)
        decision_label = "mute"
    elif mode == "dismiss_duplicate":
        if row["status"] != "proposed":
            raise InboxMutationError(
                "ALREADY_RESOLVED",
                f"cannot dismiss-duplicate resolved proposal (status={row['status']})",
            )
        await _mark_rejected(db=db, proposal_id=proposal_id, reviewer_id=reviewer_id)
        decision_label = "dismiss_duplicate"
    else:  # mode == "reject"
        if row["status"] != "proposed":
            raise InboxMutationError(
                "ALREADY_RESOLVED",
                f"cannot reject resolved proposal (status={row['status']})",
            )
        await _mark_rejected(db=db, proposal_id=proposal_id, reviewer_id=reviewer_id)
        decision_label = "reject"

    decision_id = await _write_inbox_decision(
        db=db,
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        cluster_id=None,
        reviewer_id=reviewer_id,
        decision=decision_label,
        tier_assigned=tier_assigned,
        time_to_decide_sec=time_to_decide_sec,
        keyboard_path=keyboard_path,
        typed_phrase_used=False,
        field_choices={"reason": reason} if reason else None,
        agent_confidence=float(row["confidence"]) if row["confidence"] is not None else None,
    )

    return {
        "rejected": True,
        "inbox_decision_id": decision_id,
        "suppression_rule_id": suppression_rule_id,
    }


async def _mark_rejected(
    *,
    db: AsyncSession,
    proposal_id: uuid.UUID,
    reviewer_id: uuid.UUID,
) -> None:
    await db.execute(
        text(
            """
            UPDATE action_event
            SET status = 'rejected', decided_by_user_id = CAST(:reviewer AS uuid),
                snoozed_until = NULL
            WHERE event_id = CAST(:id AS uuid)
            """
        ),
        {"id": str(proposal_id), "reviewer": str(reviewer_id)},
    )


# ---- snooze ----


async def snooze_proposal(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    proposal_id: uuid.UUID,
    snooze_until: datetime,
    snooze_reason: str,
    pegged_event_id: uuid.UUID | None,
    tier_assigned: Tier,
    keyboard_path: bool,
) -> dict[str, Any]:
    row = await get_proposal_for_decision(db=db, proposal_id=proposal_id, tenant_id=tenant_id)
    if row is None:
        raise InboxMutationError("PROPOSAL_NOT_FOUND", "proposal not found in this tenant")
    if row["status"] != "proposed":
        raise InboxMutationError(
            "ALREADY_RESOLVED",
            f"can only snooze proposed proposals; got status={row['status']}",
        )
    now = datetime.now(UTC)
    target = snooze_until if snooze_until.tzinfo else snooze_until.replace(tzinfo=UTC)
    if target <= now:
        raise InboxMutationError(
            "INVALID_SNOOZE_UNTIL",
            "snooze_until must be in the future",
        )

    await db.execute(
        text(
            """
            UPDATE action_event
            SET snoozed_until = :until
            WHERE event_id = CAST(:id AS uuid)
            """
        ),
        {"id": str(proposal_id), "until": target},
    )

    decision_id = await _write_inbox_decision(
        db=db,
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        cluster_id=None,
        reviewer_id=reviewer_id,
        decision="snooze",
        tier_assigned=tier_assigned,
        time_to_decide_sec=None,
        keyboard_path=keyboard_path,
        typed_phrase_used=False,
        field_choices={
            "snooze_until": target.isoformat(),
            "snooze_reason": snooze_reason,
            "pegged_event_id": str(pegged_event_id) if pegged_event_id else None,
        },
        agent_confidence=float(row["confidence"]) if row["confidence"] is not None else None,
    )

    return {"snoozed": True, "inbox_decision_id": decision_id}


# ---- revert_auto_applied ----


async def revert_auto_applied(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    proposal_id: uuid.UUID,
    reason: str | None,
) -> dict[str, Any]:
    """B8: undo a T0 auto-apply within a 5-minute window.

    Distinct from ``reject_proposal(mode="undo")`` — that one targets an
    inbox-session approval (≤30s); this targets a never-touched T0
    auto-apply by the agent (≤5min) and writes ``decision='revert'``.
    """
    row = await get_proposal_for_decision(db=db, proposal_id=proposal_id, tenant_id=tenant_id)
    if row is None:
        raise InboxMutationError("PROPOSAL_NOT_FOUND", "proposal not found in this tenant")
    if row["status"] != "applied":
        raise InboxMutationError(
            "NOT_REVERTABLE",
            f"revert only works on status=applied; got {row['status']}",
        )
    applied_at = row["applied_at"]
    if applied_at is None:
        raise InboxMutationError("NOT_REVERTABLE", "applied_at is missing")
    age = datetime.now(UTC) - (applied_at if applied_at.tzinfo else applied_at.replace(tzinfo=UTC))
    if age > REVERT_WINDOW:
        raise InboxMutationError(
            "REVERT_WINDOW_EXPIRED",
            f"revert window expired ({age.total_seconds():.1f}s > {REVERT_WINDOW.total_seconds()}s)",
        )

    await db.execute(
        text(
            """
            UPDATE action_event
            SET status = 'reverted', decided_by_user_id = CAST(:reviewer AS uuid)
            WHERE event_id = CAST(:id AS uuid)
            """
        ),
        {"id": str(proposal_id), "reviewer": str(reviewer_id)},
    )

    decision_id = await _write_inbox_decision(
        db=db,
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        cluster_id=None,
        reviewer_id=reviewer_id,
        decision="revert",
        tier_assigned=0,
        time_to_decide_sec=int(age.total_seconds()),
        keyboard_path=False,
        typed_phrase_used=False,
        field_choices={"reason": reason} if reason else None,
        agent_confidence=float(row["confidence"]) if row["confidence"] is not None else None,
    )

    return {
        "reverted": True,
        "inbox_decision_id": decision_id,
        "reverted_at": datetime.now(UTC),
        "age_seconds": int(age.total_seconds()),
    }


# ---- bulk ----


async def bulk_approve(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    proposal_ids: list[uuid.UUID],
    typed_phrase: str | None,
    tier_assigned: Tier,
    time_to_decide_sec: int | None,
    keyboard_path: bool,
) -> dict[str, Any]:
    """B6: best-effort. Apply what's eligible, skip + reason the rest."""
    applied = 0
    skipped: list[uuid.UUID] = []
    reasons: dict[str, BulkSkipReason] = {}
    decision_ids: list[uuid.UUID] = []

    # If bulk > 10, the typed phrase is required server-side too.
    if len(proposal_ids) > 10:
        expected = f"approve {len(proposal_ids)} items"
        if not _phrases_match(typed_phrase, expected):
            raise InboxMutationError(
                "TYPED_PHRASE_MISMATCH",
                f"bulk >10 requires typed phrase {expected!r}",
            )

    # One shared FalkorDB client + a single bootstrap for the whole batch instead
    # of one-per-contact (a 100-contact bulk-approve otherwise did ~100 client
    # setups and ~700 redundant CREATE INDEX round-trips). Created lazily — only
    # if the batch actually contains a person.create — and closed once in finally.
    graph_client: Any = None
    try:
        for pid in proposal_ids:
            row = await get_proposal_for_decision(db=db, proposal_id=pid, tenant_id=tenant_id)
            if row is None:
                skipped.append(pid)
                reasons[str(pid)] = "not_found"
                continue
            if row["status"] != "proposed":
                skipped.append(pid)
                reasons[str(pid)] = "already_resolved"
                continue
            tier_here = _compute_tier(row)
            if tier_here == 4:
                # Tier 4 in selection is excluded from bulk per design doc §11.6.
                skipped.append(pid)
                reasons[str(pid)] = "tier_4_in_selection"
                continue
            if bool(row["tenant_hipaa_mode"]):
                skipped.append(pid)
                reasons[str(pid)] = "hipaa_requires_t4"
                continue

            # P0: actually EFFECT person.create proposals (create the person + its
            # children + graph node), exactly as single approve_proposal does. Bulk
            # previously only flipped status='applied' without creating anything, so
            # bulk-approving imported contacts marked them done but never produced a
            # Person — the owner's main workflow for a large import was a silent no-op.
            if row["event_type"] == "person.create":
                if graph_client is None:
                    from contact_ops.agents.graph_sync.falkordb_client import (
                        FalkorDBGraphClient,
                    )

                    graph_client = FalkorDBGraphClient()
                await _apply_person_create_proposal(
                    db=db, tenant_id=tenant_id, proposal_id=pid, graph_client=graph_client
                )

            # Realize a dedup merge (or any future non-trivial effect) before the
            # status flip, in a per-item SAVEPOINT so one failure skips just that
            # item instead of poisoning the whole batch transaction. Cluster edges
            # whose persons are already unified return without error (no-op).
            if row["event_type"] == "dedup.propose_merge":
                try:
                    async with db.begin_nested():
                        await apply_downstream_effect(
                            event_type=row["event_type"],
                            app_db=db,
                            audit_db=db,
                            tenant_id=tenant_id,
                            payload=row["payload"],
                            confidence=(
                                float(row["confidence"])
                                if row["confidence"] is not None
                                else None
                            ),
                            actor_chain={"sub": str(reviewer_id)},
                            human_authority=str(reviewer_id),
                            request_id="inbox-bulk-approve",
                        )
                except Exception:
                    logger.warning(
                        "bulk_approve_merge_failed",
                        proposal_id=str(pid),
                        exc_info=True,
                    )
                    skipped.append(pid)
                    reasons[str(pid)] = "merge_failed"
                    continue

            # apply
            await db.execute(
                text(
                    """
                    UPDATE action_event
                    SET status = 'applied', applied_at = now(),
                        approved_by = CAST(:reviewer AS uuid),
                        decided_by_user_id = CAST(:reviewer AS uuid),
                        snoozed_until = NULL
                    WHERE event_id = CAST(:id AS uuid)
                    """
                ),
                {"id": str(pid), "reviewer": str(reviewer_id)},
            )
            decision_id = await _write_inbox_decision(
                db=db,
                tenant_id=tenant_id,
                proposal_id=pid,
                cluster_id=None,
                reviewer_id=reviewer_id,
                decision="approve",
                tier_assigned=tier_here,
                time_to_decide_sec=time_to_decide_sec,
                keyboard_path=keyboard_path,
                typed_phrase_used=typed_phrase is not None,
                field_choices=None,
                agent_confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            )
            decision_ids.append(decision_id)
            applied += 1
    finally:
        if graph_client is not None:
            await graph_client.close()

    return {
        "applied": applied,
        "skipped": skipped,
        "reasons": reasons,
        "inbox_decision_ids": decision_ids,
    }


async def bulk_reject(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    proposal_ids: list[uuid.UUID],
    reason: str | None,
    tier_assigned: Tier,
    time_to_decide_sec: int | None,
    keyboard_path: bool,
) -> dict[str, Any]:
    rejected = 0
    skipped: list[uuid.UUID] = []
    reasons: dict[str, BulkSkipReason] = {}
    decision_ids: list[uuid.UUID] = []

    for pid in proposal_ids:
        row = await get_proposal_for_decision(db=db, proposal_id=pid, tenant_id=tenant_id)
        if row is None:
            skipped.append(pid)
            reasons[str(pid)] = "not_found"
            continue
        if row["status"] != "proposed":
            skipped.append(pid)
            reasons[str(pid)] = "already_resolved"
            continue
        await _mark_rejected(db=db, proposal_id=pid, reviewer_id=reviewer_id)
        decision_id = await _write_inbox_decision(
            db=db,
            tenant_id=tenant_id,
            proposal_id=pid,
            cluster_id=None,
            reviewer_id=reviewer_id,
            decision="reject",
            tier_assigned=tier_assigned,
            time_to_decide_sec=time_to_decide_sec,
            keyboard_path=keyboard_path,
            typed_phrase_used=False,
            field_choices={"reason": reason} if reason else None,
            agent_confidence=float(row["confidence"]) if row["confidence"] is not None else None,
        )
        decision_ids.append(decision_id)
        rejected += 1

    return {
        "rejected": rejected,
        "skipped": skipped,
        "reasons": reasons,
        "inbox_decision_ids": decision_ids,
    }


# ---- resolve_conflict_keep_both ----


async def resolve_conflict_keep_both(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    primary_proposal_id: uuid.UUID,
    conflicting_proposal_id: uuid.UUID,
    source_tags: dict[uuid.UUID, str],
    tier_assigned: Tier,
    time_to_decide_sec: int | None,
) -> dict[str, Any]:
    """B3: separate from approve. Promotes both proposals as parallel facts.

    Effect: both action_events flip to 'applied'; proposal_conflict row
    flagged 'keep_both'. Downstream consumers (Dedup / enrichment) treat
    these as two assertions tagged with source.
    """
    primary = await get_proposal_for_decision(
        db=db, proposal_id=primary_proposal_id, tenant_id=tenant_id
    )
    conflicting = await get_proposal_for_decision(
        db=db, proposal_id=conflicting_proposal_id, tenant_id=tenant_id
    )
    if primary is None or conflicting is None:
        raise InboxMutationError(
            "PROPOSAL_NOT_FOUND",
            "one or both proposals not found in this tenant",
        )

    # Find the proposal_conflict row
    conflict_row = await db.execute(
        text(
            """
            SELECT id FROM proposal_conflict
            WHERE (primary_proposal_id = CAST(:a AS uuid)
                   AND conflicting_proposal_id = CAST(:b AS uuid))
               OR (primary_proposal_id = CAST(:b AS uuid)
                   AND conflicting_proposal_id = CAST(:a AS uuid))
            LIMIT 1
            """
        ),
        {"a": str(primary_proposal_id), "b": str(conflicting_proposal_id)},
    )
    conflict_id_row = conflict_row.scalar_one_or_none()
    if conflict_id_row is None:
        raise InboxMutationError(
            "NO_CONFLICT_FOUND",
            "no proposal_conflict row links these two proposals",
        )
    conflict_id = uuid.UUID(str(conflict_id_row))

    # Apply both
    for pid in (primary_proposal_id, conflicting_proposal_id):
        await db.execute(
            text(
                """
                UPDATE action_event
                SET status = 'applied', applied_at = now(),
                    approved_by = CAST(:reviewer AS uuid),
                    decided_by_user_id = CAST(:reviewer AS uuid),
                    snoozed_until = NULL
                WHERE event_id = CAST(:id AS uuid) AND status = 'proposed'
                """
            ),
            {"id": str(pid), "reviewer": str(reviewer_id)},
        )

    # Mark conflict resolved
    await db.execute(
        text(
            """
            UPDATE proposal_conflict
            SET resolution = 'keep_both',
                resolved_by_user_id = CAST(:reviewer AS uuid),
                resolved_at = now()
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": str(conflict_id), "reviewer": str(reviewer_id)},
    )

    # Write one inbox_decision tagged 'keep_both' against the primary
    decision_id = await _write_inbox_decision(
        db=db,
        tenant_id=tenant_id,
        proposal_id=primary_proposal_id,
        cluster_id=None,
        reviewer_id=reviewer_id,
        decision="keep_both",
        tier_assigned=tier_assigned,
        time_to_decide_sec=time_to_decide_sec,
        keyboard_path=False,
        typed_phrase_used=False,
        field_choices={
            "conflicting_proposal_id": str(conflicting_proposal_id),
            "source_tags": {str(k): v for k, v in source_tags.items()},
        },
        agent_confidence=None,
    )

    return {
        "primary_applied": True,
        "conflicting_applied": True,
        "conflict_id": conflict_id,
        "inbox_decision_id": decision_id,
    }


# ---- helpers ----


def _compute_tier(row: dict[str, Any]) -> int:
    """Server-side tier selection. Mirrors UI prompt's selectTier() logic."""
    is_hipaa = bool(row["tenant_hipaa_mode"])
    event_type: str = row["event_type"] or ""
    is_delete = event_type in {
        "contact.delete", "tag.remove", "edge.remove",
        "person.delete", "organization.delete",
    }
    is_dedup = event_type.startswith("dedup.")
    is_edge = event_type.startswith("relationship.") or event_type.startswith("edge.")
    is_cross_tenant = row.get("target_tenant_id") is not None
    bulk_count = _bulk_count_of(row)
    confidence = float(row["confidence"] or 0.0)
    decision_payload = row.get("decision_payload") or {}
    payload_after = decision_payload.get("payload_after") or row.get("payload") or {}
    payload_before = decision_payload.get("payload_before")
    fields_changed = len(set((payload_before or {}).keys()) ^ set(payload_after.keys()))
    reversibility = row.get("reversibility_class") or "reversible"

    if is_hipaa or is_cross_tenant or is_delete or bulk_count > 10:
        return 4
    if is_dedup or fields_changed >= 3 or is_edge:
        return 3
    if confidence < 0.85 or reversibility in {"soft_delete", "irreversible"}:
        return 2
    if confidence < 0.97:
        return 1
    return 1  # default — never auto-apply from MCP path (T0 is agent-side only)


def _bulk_count_of(row: dict[str, Any]) -> int:
    decision_payload = row.get("decision_payload") or {}
    payload_after = decision_payload.get("payload_after") or row.get("payload") or {}
    targets = payload_after.get("target_ids") if isinstance(payload_after, dict) else None
    if isinstance(targets, list) and targets:
        return len(targets)
    return 1


__all__ = [
    "approve_proposal",
    "bulk_approve",
    "bulk_reject",
    "InboxMutationError",
    "REVERT_WINDOW",
    "reject_proposal",
    "resolve_conflict_keep_both",
    "revert_auto_applied",
    "snooze_proposal",
    "UNDO_WINDOW",
]
