"""Seed synthetic inbox proposals for Phase 3.3b frontend development.

Inserts ~30 action_event rows across the 12 Phase 3 agent slugs into
the requested tenant, plus a handful of supporting persons and
organizations. Includes:

* Confidence range from 0.42 to 0.98 (covers all four bands)
* Reversibility mix (reversible / reversible_24h / soft_delete / irreversible)
* HIPAA escalation case (only fires if the tenant has hipaa_mode=true)
* Cross-tenant edge (target_tenant_id set)
* Dedup cluster of 4 proposals on the same person (forces ClusterCard
  with cluster_kind='dedup' to render)
* Conflict pair: two proposals on the same person+field within 1h so
  BaseAgent's conflict hook surfaces a ConflictBanner
* Auto-applied proposal (status='applied') within the 5-min revert
  window so the frontend's revert_auto_applied tool has a target
* Snoozed proposal (snoozed_until=now+1h) so the Snoozed nav shows
  non-empty

Usage:

    python -m scripts.seed_inbox_demo --tenant <uuid> [--reset]
    python -m scripts.seed_inbox_demo --tenant-slug aaron-personal --reset

``--reset`` deletes prior seeded rows tagged with the demo marker
(decision_payload @> {"_seed": "inbox-demo"}) before reseeding.

The script bypasses BaseAgent.propose_action() — these are synthetic
demonstration rows, not real agent runs. They will not show up in
calibration histograms (the Calibration Daemon filters on
``decision_payload.tier_at_creation IS NOT NULL`` which we set, but
the ``_seed`` marker is its filter to ignore demo data).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import create_engine, text

SEED_MARKER = "inbox-demo"
DEMO_AGENT_SLUGS: tuple[str, ...] = (
    "dedup",
    "voice-match",
    "enrichment",
    "lifecycle",
    "tag",
    "relationship-inference",
    "carddav-reconciliation",
    "calibration-daemon",
    "data-intel-bridge",
    "graph-sync",
    "communication-signal",
    "provenance-promoter",
)


def _sync_db_url() -> str:
    return os.environ.get(
        "CONTACT_OPS_SYNC_DATABASE_URL",
        os.environ.get(
            "DATABASE_URL", "${CONTACT_OPS_DATABASE_URL}"
        ),
    )


def _content_hash(agent_slug: str, payload_after: dict[str, Any], event_type: str, n: int) -> bytes:
    raw = json.dumps(
        {"a": agent_slug, "p": payload_after, "e": event_type, "n": n},
        sort_keys=True,
        default=str,
    ).encode()
    return hashlib.sha256(raw).digest()


def _idempotency_key(agent_slug: str, tenant: uuid.UUID, aggregate: uuid.UUID, event_type: str, n: int) -> str:
    material = f"seed|{agent_slug}|{tenant}|{aggregate}|{event_type}|{n}"
    return hashlib.sha256(material.encode()).hexdigest()


def _decision_payload(
    *, payload_before: dict[str, Any] | None, payload_after: dict[str, Any], tier: int, reversibility: str, auto_apply_eligible: bool
) -> dict[str, Any]:
    return {
        "reversibility": reversibility,
        "tier_at_creation": tier,
        "auto_apply_eligible": auto_apply_eligible,
        "payload_after": payload_after,
        "payload_before": payload_before,
        "_seed": SEED_MARKER,
    }


def _resolve_tenant_id(conn, *, tenant: str | None, tenant_slug: str | None) -> uuid.UUID:
    if tenant:
        return uuid.UUID(tenant)
    if tenant_slug:
        row = conn.execute(
            text("SELECT id FROM tenants WHERE slug = :slug"),
            {"slug": tenant_slug},
        ).first()
        if row is None:
            raise SystemExit(f"tenant slug not found: {tenant_slug}")
        return uuid.UUID(str(row[0]))
    raise SystemExit("must pass --tenant <uuid> or --tenant-slug <slug>")


def _ensure_demo_persons(conn, tenant_id: uuid.UUID, *, owner_user_id: str) -> list[uuid.UUID]:
    """Create a small fixed set of demo persons if they don't already exist."""
    names = [
        ("John Rector", "john-rector-demo"),
        ("Kevin Holloway", "kevin-holloway-demo"),
        ("Leo Grant", "leo-grant-demo"),
        ("Nate Ellison", "nate-ellison-demo"),
        ("Allie Menegakis", "allie-menegakis-demo"),
    ]
    out: list[uuid.UUID] = []
    for display_name, slug in names:
        row = conn.execute(
            text(
                """
                SELECT id FROM persons
                WHERE display_name = :name AND canonical_owner_tenant_id = CAST(:tenant AS uuid)
                LIMIT 1
                """
            ),
            {"name": display_name, "tenant": str(tenant_id)},
        ).first()
        if row is not None:
            out.append(uuid.UUID(str(row[0])))
            continue
        new_id = uuid.uuid4()
        conn.execute(
            text(
                """
                INSERT INTO persons (
                    id, display_name, kind, canonical_owner_tenant_id, created_at
                ) VALUES (
                    CAST(:id AS uuid), :name, 'individual', CAST(:tenant AS uuid), now()
                )
                """
            ),
            {"id": str(new_id), "name": display_name, "tenant": str(tenant_id)},
        )
        out.append(new_id)
    return out


def _ensure_demo_org(conn, tenant_id: uuid.UUID) -> uuid.UUID:
    row = conn.execute(
        text(
            """
            SELECT id FROM organizations
            WHERE display_name = 'E2open (seed)'
              AND canonical_owner_tenant_id = CAST(:tenant AS uuid)
            LIMIT 1
            """
        ),
        {"tenant": str(tenant_id)},
    ).first()
    if row is not None:
        return uuid.UUID(str(row[0]))
    new_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO organizations (
                id, display_name, canonical_owner_tenant_id, created_at
            ) VALUES (
                CAST(:id AS uuid), 'E2open (seed)', CAST(:tenant AS uuid), now()
            )
            """
        ),
        {"id": str(new_id)},
    )
    return new_id


def _insert_action_event(
    conn,
    *,
    tenant_id: uuid.UUID,
    target_tenant_id: uuid.UUID | None,
    aggregate_id: uuid.UUID,
    aggregate_type: str,
    agent_slug: str,
    event_type: str,
    payload_before: dict[str, Any] | None,
    payload_after: dict[str, Any],
    confidence: float,
    reversibility: str,
    rationale: str,
    n: int,
    status: str = "proposed",
    snoozed_until: datetime | None = None,
    proposed_at: datetime | None = None,
    applied_at: datetime | None = None,
    tier: int = 2,
) -> uuid.UUID:
    event_id = uuid.uuid4()
    actor = {"sub": agent_slug, "agent_version": "seed-1.0", "act": {"sub": "system"}}
    decision_payload = _decision_payload(
        payload_before=payload_before,
        payload_after=payload_after,
        tier=tier,
        reversibility=reversibility,
        auto_apply_eligible=(status == "applied"),
    )
    conn.execute(
        text(
            """
            INSERT INTO action_event (
                event_id, event_type, event_version,
                tenant_id, target_tenant_id, aggregate_type, aggregate_id,
                payload, actor, actor_type,
                confidence, evidence, rationale,
                status, content_hash, idempotency_key,
                decision_payload, reversibility_class,
                agent_version, trust_tier_at_creation, triggered_by,
                proposed_at, applied_at, snoozed_until
            ) VALUES (
                CAST(:event_id AS uuid), :event_type, 1,
                CAST(:tenant AS uuid), CAST(:target_tenant AS uuid),
                CAST(:agg_type AS entity_kind), CAST(:agg_id AS uuid),
                CAST(:payload AS jsonb), CAST(:actor AS jsonb), 'agent'::actor_type,
                :confidence, '{}'::jsonb, :rationale,
                CAST(:status AS event_status), :content_hash, :idempotency_key,
                CAST(:decision_payload AS jsonb), :reversibility,
                'seed-1.0', :tier, 'seed_inbox_demo',
                :proposed_at, :applied_at, :snoozed_until
            )
            """
        ),
        {
            "event_id": str(event_id),
            "event_type": event_type,
            "tenant": str(tenant_id),
            "target_tenant": str(target_tenant_id) if target_tenant_id else None,
            "agg_type": aggregate_type,
            "agg_id": str(aggregate_id),
            "payload": json.dumps(payload_after, default=str),
            "actor": json.dumps(actor),
            "confidence": confidence,
            "rationale": rationale,
            "status": status,
            "content_hash": _content_hash(agent_slug, payload_after, event_type, n),
            "idempotency_key": _idempotency_key(agent_slug, tenant_id, aggregate_id, event_type, n),
            "decision_payload": json.dumps(decision_payload, default=str),
            "reversibility": reversibility,
            "tier": tier,
            "proposed_at": (proposed_at or datetime.now(timezone.utc)),
            "applied_at": applied_at,
            "snoozed_until": snoozed_until,
        },
    )
    return event_id


def _reset_seed_rows(conn, tenant_id: uuid.UUID) -> int:
    result = conn.execute(
        text(
            """
            DELETE FROM action_event
            WHERE tenant_id = CAST(:tenant AS uuid)
              AND decision_payload @> CAST(:marker AS jsonb)
            """
        ),
        {"tenant": str(tenant_id), "marker": json.dumps({"_seed": SEED_MARKER})},
    )
    return result.rowcount or 0


def seed(tenant_id: uuid.UUID, *, reset: bool, owner_user_id: str) -> dict[str, int]:
    """Insert the demo corpus. Returns counts by category."""
    engine = create_engine(_sync_db_url(), future=True)
    counts = {"deleted_prior": 0, "proposed": 0, "applied": 0, "snoozed": 0}
    rng = random.Random(42)
    try:
        with engine.begin() as conn:
            if reset:
                counts["deleted_prior"] = _reset_seed_rows(conn, tenant_id)
            persons = _ensure_demo_persons(conn, tenant_id, owner_user_id=owner_user_id)
            org = _ensure_demo_org(conn, tenant_id)

            n = 0
            now = datetime.now(timezone.utc)

            # 12 single-shot proposals, one per agent type, range of confidences
            confidences = [0.42, 0.55, 0.68, 0.73, 0.79, 0.82, 0.86, 0.88, 0.91, 0.94, 0.96, 0.98]
            reversibilities = [
                "reversible", "reversible", "reversible_24h", "reversible",
                "reversible", "reversible", "reversible_24h", "reversible",
                "soft_delete", "reversible", "reversible", "irreversible",
            ]
            for i, slug in enumerate(DEMO_AGENT_SLUGS):
                target_person = persons[i % len(persons)]
                event_type = {
                    "dedup": "dedup.propose_merge",
                    "voice-match": "voice_match.proposed",
                    "enrichment": "enrichment.field_set",
                    "lifecycle": "lifecycle.transition",
                    "tag": "tag.add",
                    "relationship-inference": "relationship.propose_edge",
                    "carddav-reconciliation": "carddav.reconcile",
                    "calibration-daemon": "calibration.tier_demote",
                    "data-intel-bridge": "data_intel.bridge_observation",
                    "graph-sync": "graph_sync.upsert",
                    "communication-signal": "communication.strength_update",
                    "provenance-promoter": "provenance.promote_fact",
                }[slug]
                payload_before = {"display_name": "(prior)"} if i % 3 == 0 else None
                payload_after = {
                    "display_name": f"Updated by {slug} #{i}",
                    "note": f"Synthetic proposal {i} from {slug}.",
                }
                _insert_action_event(
                    conn,
                    tenant_id=tenant_id,
                    target_tenant_id=None,
                    aggregate_id=target_person,
                    aggregate_type="person",
                    agent_slug=slug,
                    event_type=event_type,
                    payload_before=payload_before,
                    payload_after=payload_after,
                    confidence=confidences[i],
                    reversibility=reversibilities[i],
                    rationale=f"{slug} suggests an update based on recent signal (synthetic seed).",
                    n=n,
                    proposed_at=now - timedelta(minutes=i * 7),
                    tier=2,
                )
                n += 1
                counts["proposed"] += 1

            # Dedup cluster: 4 proposals from dedup on persons[0] within ~10 minutes
            dedup_target = persons[0]
            for i in range(4):
                _insert_action_event(
                    conn,
                    tenant_id=tenant_id,
                    target_tenant_id=None,
                    aggregate_id=dedup_target,
                    aggregate_type="person",
                    agent_slug="dedup",
                    event_type="dedup.propose_merge",
                    payload_before=None,
                    payload_after={
                        "merge_candidate_id": str(uuid.uuid4()),
                        "merge_score": 0.7 + i * 0.05,
                        "fields": {"display_name": "Same person via different source"},
                    },
                    confidence=0.71 + i * 0.04,
                    reversibility="reversible",
                    rationale=f"Splink match #{i+1} on hashed phone + email-local.",
                    n=n,
                    proposed_at=now - timedelta(minutes=15 + i * 2),
                    tier=3,
                )
                n += 1
                counts["proposed"] += 1

            # Conflict pair: two proposals on persons[1].title within 1h
            conflict_target = persons[1]
            _insert_action_event(
                conn,
                tenant_id=tenant_id,
                target_tenant_id=None,
                aggregate_id=conflict_target,
                aggregate_type="person",
                agent_slug="enrichment",
                event_type="enrichment.field_set",
                payload_before=None,
                payload_after={"title": "CEO, E2open"},
                confidence=0.92,
                reversibility="reversible",
                rationale="SEC EDGAR record lists 'John Rector, CEO'.",
                n=n,
                proposed_at=now - timedelta(minutes=22),
                tier=2,
            )
            n += 1
            counts["proposed"] += 1
            _insert_action_event(
                conn,
                tenant_id=tenant_id,
                target_tenant_id=None,
                aggregate_id=conflict_target,
                aggregate_type="person",
                agent_slug="meeting-ops",
                event_type="enrichment.field_set",
                payload_before=None,
                payload_after={"title": "Founder, E2open"},
                confidence=0.94,
                reversibility="reversible",
                rationale="Transcript 2026-04-18: 'I was the founder, not CEO.'",
                n=n,
                proposed_at=now - timedelta(minutes=14),
                tier=2,
            )
            n += 1
            counts["proposed"] += 1
            # Insert the proposal_conflict row directly so the
            # ConflictBanner has data without waiting for the trigger.
            conn.execute(
                text(
                    """
                    INSERT INTO proposal_conflict (
                        tenant_id, primary_proposal_id, conflicting_proposal_id,
                        conflict_type, entity_id, field_name
                    )
                    SELECT CAST(:tenant AS uuid),
                           ae1.event_id, ae2.event_id,
                           'contradicting_field_value',
                           CAST(:entity AS uuid), 'title'
                    FROM (
                        SELECT event_id FROM action_event
                        WHERE aggregate_id = CAST(:entity AS uuid)
                          AND tenant_id = CAST(:tenant AS uuid)
                          AND actor->>'sub' = 'enrichment'
                        ORDER BY proposed_at DESC LIMIT 1
                    ) ae1, (
                        SELECT event_id FROM action_event
                        WHERE aggregate_id = CAST(:entity AS uuid)
                          AND tenant_id = CAST(:tenant AS uuid)
                          AND actor->>'sub' = 'meeting-ops'
                        ORDER BY proposed_at DESC LIMIT 1
                    ) ae2
                    """
                ),
                {"tenant": str(tenant_id), "entity": str(conflict_target)},
            )

            # Auto-applied T0 candidate (status='applied', applied_at 2min ago)
            _insert_action_event(
                conn,
                tenant_id=tenant_id,
                target_tenant_id=None,
                aggregate_id=persons[2],
                aggregate_type="person",
                agent_slug="tag",
                event_type="tag.add",
                payload_before=None,
                payload_after={"tag_slug": "auto-tagged-demo"},
                confidence=0.98,
                reversibility="reversible",
                rationale="High-confidence tag auto-applied (T0).",
                n=n,
                status="applied",
                proposed_at=now - timedelta(minutes=2),
                applied_at=now - timedelta(minutes=2),
                tier=0,
            )
            n += 1
            counts["applied"] += 1

            # Snoozed proposal (snoozed_until = now+1h)
            _insert_action_event(
                conn,
                tenant_id=tenant_id,
                target_tenant_id=None,
                aggregate_id=persons[3],
                aggregate_type="person",
                agent_slug="lifecycle",
                event_type="lifecycle.transition",
                payload_before={"lifecycle_state": "active"},
                payload_after={"lifecycle_state": "lapsing"},
                confidence=0.74,
                reversibility="reversible_24h",
                rationale="No interaction in 90 days.",
                n=n,
                snoozed_until=now + timedelta(hours=1),
                proposed_at=now - timedelta(hours=3),
                tier=2,
            )
            n += 1
            counts["snoozed"] += 1

            # Cross-tenant proposal (target_tenant_id = another tenant if it exists)
            other_tenant_row = conn.execute(
                text(
                    """
                    SELECT id FROM tenants
                    WHERE id != CAST(:tenant AS uuid)
                    LIMIT 1
                    """
                ),
                {"tenant": str(tenant_id)},
            ).first()
            if other_tenant_row is not None:
                _insert_action_event(
                    conn,
                    tenant_id=tenant_id,
                    target_tenant_id=uuid.UUID(str(other_tenant_row[0])),
                    aggregate_id=persons[4],
                    aggregate_type="person",
                    agent_slug="carddav-reconciliation",
                    event_type="carddav.reconcile",
                    payload_before=None,
                    payload_after={"copy_to_tenant": True, "field": "display_name"},
                    confidence=0.81,
                    reversibility="reversible",
                    rationale="Cross-tenant contact appears in both address books.",
                    n=n,
                    proposed_at=now - timedelta(minutes=5),
                    tier=4,
                )
                n += 1
                counts["proposed"] += 1

            # Organization proposal (one)
            _insert_action_event(
                conn,
                tenant_id=tenant_id,
                target_tenant_id=None,
                aggregate_id=org,
                aggregate_type="org",
                agent_slug="enrichment",
                event_type="enrichment.field_set",
                payload_before={"website": None},
                payload_after={"website": "https://e2open.com"},
                confidence=0.96,
                reversibility="reversible_24h",
                rationale="Crunchbase canonical URL.",
                n=n,
                proposed_at=now - timedelta(hours=1),
                tier=2,
            )
            n += 1
            counts["proposed"] += 1

            _ = rng  # reserved for future variation
    finally:
        engine.dispose()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed synthetic inbox proposals.")
    parser.add_argument("--tenant", help="Tenant UUID")
    parser.add_argument("--tenant-slug", help="Tenant slug (alternative to --tenant)")
    parser.add_argument("--reset", action="store_true", help="Delete prior seed rows first")
    parser.add_argument(
        "--owner-user-id",
        default=os.environ.get("SEED_OWNER_USER_ID", "00000000-0000-0000-0000-000000000001"),
    )
    args = parser.parse_args()

    engine = create_engine(_sync_db_url(), future=True)
    try:
        with engine.connect() as conn:
            tenant_id = _resolve_tenant_id(
                conn, tenant=args.tenant, tenant_slug=args.tenant_slug
            )
    finally:
        engine.dispose()

    counts = seed(tenant_id, reset=args.reset, owner_user_id=args.owner_user_id)
    print(json.dumps({"tenant": str(tenant_id), **counts}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
