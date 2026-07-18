"""End-to-end coverage for the GDPR compliance engine (P-00075).

``compliance_export.py`` and ``compliance_erasure.py`` shipped DORMANT and had
zero tests — they had never actually run end to end. This exercises them against
a real Postgres (the session-scoped testcontainer) plus fakes for the three
external data planes (Qdrant / Garage / FalkorDB), proving:

* :func:`compliance_export.export_subject_data` produces the documented Art.15
  bundle (schema stamp, person, related tables, related_counts, action_events).
* :func:`compliance_erasure.purge_person` genuinely removes a data subject from
  Postgres (person row + CASCADE children + no-cascade orphans) AND fans the
  purge out to Qdrant + Garage + FalkorDB, while anonymizing the hash-chained
  ledger IN PLACE (PII redacted, ``content_hash`` untouched).

The external planes are faked (there is no live Qdrant/Garage/FalkorDB in CI);
the service lazy-imports each getter, so patching the module attribute proves the
real call path reaches each store with the right person id / DETACH DELETE.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from contact_ops.core.config import get_settings
from contact_ops.services import compliance_erasure, compliance_export

pytestmark = pytest.mark.asyncio

_HASH = b"\x11" * 32  # deterministic content_hash so we can prove it is untouched


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _seed_person(db, person_id: uuid.UUID, tenant_id: uuid.UUID, name: str) -> None:
    await db.execute(
        text(
            "INSERT INTO persons (id, canonical_owner_tenant_id, merge_status, "
            "display_name, given_name, created_at, updated_at) "
            "VALUES (:id, :tid, 'canonical', :name, :given, now(), now())"
        ),
        {"id": str(person_id), "tid": str(tenant_id), "name": name, "given": name.split()[0]},
    )


async def _seed_email(db, person_id: uuid.UUID, address: str) -> None:
    await db.execute(
        text("INSERT INTO emails (person_id, address, is_primary) VALUES (:p, :a, true)"),
        {"p": str(person_id), "a": address},
    )


async def _seed_phone(db, person_id: uuid.UUID, e164: str) -> None:
    await db.execute(
        text("INSERT INTO phones (person_id, e164, is_primary) VALUES (:p, :e, true)"),
        {"p": str(person_id), "e": e164},
    )


async def _seed_note(db, person_id: uuid.UUID, tenant_id: uuid.UUID, body: str) -> None:
    await db.execute(
        text(
            "INSERT INTO notes (tenant_id, target_type, target_id, author_type, "
            "author_id, text) VALUES (:tid, 'person', :pid, 'user', 'seed', :body)"
        ),
        {"tid": str(tenant_id), "pid": str(person_id), "body": body},
    )


async def _seed_ledger_event(
    db, person_id: uuid.UUID, tenant_id: uuid.UUID, display_name: str
) -> uuid.UUID:
    """Append one action_event carrying PII in actor + payload, keyed to person."""
    event_id = uuid.uuid4()
    await db.execute(
        text(
            """
            INSERT INTO action_event (
                event_id, event_type, tenant_id, aggregate_type, aggregate_id,
                affected_ids, payload, actor, actor_type, status, content_hash
            ) VALUES (
                :eid, 'person.update', :tid, 'person', :pid,
                ARRAY[:pid]::uuid[],
                CAST(:payload AS jsonb), CAST(:actor AS jsonb),
                'external_system', 'applied', :hash
            )
            """
        ),
        {
            "eid": event_id,
            "tid": str(tenant_id),
            "pid": str(person_id),
            "payload": (
                '{"before": {"display_name": "' + display_name + '"}, '
                '"after": {"display_name": "' + display_name + ' (edited)"}}'
            ),
            "actor": '{"sub": "user-123", "display_name": "' + display_name + '"}',
            "hash": _HASH,
        },
    )
    return event_id


# ---------------------------------------------------------------------------
# External-plane fakes (record the calls the purge fans out)
# ---------------------------------------------------------------------------


class _FakeVoiceService:
    def __init__(self) -> None:
        self.deleted_person_ids: list[uuid.UUID] = []

    async def delete_by_person_id(self, *, person_id: uuid.UUID) -> int:
        self.deleted_person_ids.append(person_id)
        return 3  # pretend three voice points existed


class _FakeStorageService:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []

    def photo_bucket(self, tenant_slug: str) -> str:
        return f"contact-ops-photos-{tenant_slug}"

    async def delete(self, *, bucket: str, key: str) -> None:
        self.deleted.append((bucket, key))


class _FakeFalkorClient:
    calls: list[tuple[str, dict]] = []
    closed: bool = False

    def __init__(self, *args, **kwargs) -> None:  # noqa: D401 - test double
        pass

    async def query(self, graph, cypher, params, **kwargs):
        type(self).calls.append((cypher, params))
        return None

    async def close(self) -> None:
        type(self).closed = True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_export_subject_data_produces_documented_bundle(db_session, seeded_tenants):
    """export_subject_data emits the Art.15 bundle with person + related + counts."""
    tid = seeded_tenants["non_hipaa"]
    pid = uuid.uuid4()
    await _seed_person(db_session, pid, tid, "Jane Exportable")
    await _seed_email(db_session, pid, "REMOVED")
    await _seed_phone(db_session, pid, "+15550009999")
    await _seed_note(db_session, pid, tid, "met at conference")
    await _seed_ledger_event(db_session, pid, tid, "Jane Exportable")
    await db_session.commit()

    result = await compliance_export.export_subject_data(
        db_session, tid, pid, settings=get_settings()
    )

    # ENV=test => local-fs storage backend => the service inlines the doc.
    assert "inline" in result, result
    doc = result["inline"]

    assert doc["schema"] == compliance_export.EXPORT_SCHEMA_VERSION
    assert doc["export_kind"] == "subject"
    assert doc["subject"] == {"person_id": str(pid), "not_found": False}
    assert doc["person"]["display_name"] == "Jane Exportable"

    related = doc["related"]
    assert [e["address"] for e in related["emails"]] == ["REMOVED"]
    assert [p["e164"] for p in related["phones"]] == ["+15550009999"]
    assert [n["text"] for n in related["notes"]] == ["met at conference"]
    assert len(related["action_events"]) == 1

    counts = doc["summary"]["related_counts"]
    assert counts["emails"] == 1
    assert counts["phones"] == 1
    assert counts["notes"] == 1
    assert counts["action_events"] == 1
    assert "GDPR Art.15" in doc["legal_basis"]


async def test_export_subject_missing_person_flags_not_found(db_session, seeded_tenants):
    """A subject with no row still yields a well-formed bundle flagged not_found."""
    tid = seeded_tenants["non_hipaa"]
    ghost = uuid.uuid4()

    result = await compliance_export.export_subject_data(
        db_session, tid, ghost, settings=get_settings()
    )
    doc = result["inline"]
    assert doc["subject"] == {"person_id": str(ghost), "not_found": True}
    assert doc["person"] is None


async def test_purge_person_end_to_end_all_stores(db_session, seeded_tenants, monkeypatch):
    """purge_person removes the subject from Postgres + Qdrant + Garage + FalkorDB."""
    tid = seeded_tenants["non_hipaa"]
    pid = uuid.uuid4()
    await _seed_person(db_session, pid, tid, "John Erasable")
    await _seed_email(db_session, pid, "REMOVED")
    await _seed_phone(db_session, pid, "+15551110000")
    await _seed_note(db_session, pid, tid, "sensitive detail")
    event_id = await _seed_ledger_event(db_session, pid, tid, "John Erasable")
    await db_session.commit()

    # Wire fakes into the three external planes the purge lazy-imports.
    fake_voice = _FakeVoiceService()
    fake_storage = _FakeStorageService()
    _FakeFalkorClient.calls = []
    _FakeFalkorClient.closed = False
    monkeypatch.setattr(
        "contact_ops.services.qdrant_voice.get_voice_service", lambda: fake_voice
    )
    monkeypatch.setattr(
        "contact_ops.services.storage.get_storage_service", lambda: fake_storage
    )
    monkeypatch.setattr(
        "contact_ops.agents.graph_sync.falkordb_client.FalkorDBGraphClient",
        _FakeFalkorClient,
    )

    summary = await compliance_erasure.purge_person(db_session, str(tid), str(pid))

    # --- Postgres: the subject and its children are gone ---------------------
    assert await db_session.scalar(
        text("SELECT count(*) FROM persons WHERE id = :p"), {"p": str(pid)}
    ) == 0
    assert await db_session.scalar(
        text("SELECT count(*) FROM emails WHERE person_id = :p"), {"p": str(pid)}
    ) == 0
    assert await db_session.scalar(
        text("SELECT count(*) FROM phones WHERE person_id = :p"), {"p": str(pid)}
    ) == 0
    assert await db_session.scalar(
        text("SELECT count(*) FROM notes WHERE target_type='person' AND target_id=:p"),
        {"p": str(pid)},
    ) == 0
    assert summary["cascade_summary"]["persons"] == 1
    assert summary["cascade_summary"]["emails"] == 1
    assert summary["cascade_summary"]["notes"] == 1

    # --- Ledger: anonymized IN PLACE, content_hash untouched -----------------
    assert summary["ledger_anonymized"] == 1
    row = (
        await db_session.execute(
            text(
                "SELECT actor, payload, content_hash FROM action_event "
                "WHERE event_id = :e"
            ),
            {"e": str(event_id)},
        )
    ).first()
    assert row is not None, "ledger row must survive erasure (never deleted)"
    actor, payload, content_hash = row
    pseudonym = compliance_erasure._pseudonym(str(pid))
    assert actor["display_name"] == pseudonym
    assert actor["sub"] == "user-123"  # non-PII key preserved
    assert payload["before"]["display_name"] == pseudonym
    assert payload["after"]["display_name"] == pseudonym
    assert "John Erasable" not in str(actor) + str(payload)
    assert bytes(content_hash) == _HASH  # hash-chain link deliberately preserved

    # --- Qdrant / Garage / FalkorDB: the purge reached each plane ------------
    assert fake_voice.deleted_person_ids == [pid]
    assert summary["external"]["qdrant"] == {"deleted_points": 3}

    photo_bucket = f"contact-ops-photos-{'non-hipaa-org'}"
    assert (photo_bucket, f"{pid}.jpg") in fake_storage.deleted
    assert summary["external"]["garage"]["buckets_touched"] == [photo_bucket]

    assert _FakeFalkorClient.closed is True
    assert len(_FakeFalkorClient.calls) == 1
    cypher, params = _FakeFalkorClient.calls[0]
    assert "DETACH DELETE" in cypher
    assert params == {"pid": str(pid)}
    assert summary["external"]["falkordb"] == {"node_purged": True}

    # A fresh, un-redacted erasure event was appended to the ledger.
    assert await db_session.scalar(
        text(
            "SELECT count(*) FROM action_event "
            "WHERE aggregate_id = :p AND event_type = 'person.erase'"
        ),
        {"p": str(pid)},
    ) == 1
