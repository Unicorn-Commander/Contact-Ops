from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from contact_ops.agents.dedup.blocking import stage1_deterministic_blocking

pytestmark = pytest.mark.asyncio


async def _seed_person(
    db_session,
    person_id: uuid.UUID,
    tenant_id: uuid.UUID,
    **kwargs,
) -> None:
    params: dict[str, str] = {
        "id": str(person_id),
        "tenant_id": str(tenant_id),
        "merge_status": "canonical",
        "display_name": kwargs.get("display_name", ""),
    }
    cols = ["id", "canonical_owner_tenant_id", "merge_status", "display_name"]
    phs = [":id", ":tenant_id", ":merge_status", ":display_name"]

    for key in ("given_name", "family_name", "company", "occupation_title", "address"):
        if key in kwargs:
            params[key] = kwargs[key]
            cols.append(key)
            phs.append(f":{key}")

    await db_session.execute(
        text(
            f"INSERT INTO persons ({', '.join(cols)}) VALUES ({', '.join(phs)})"
        ),
        params,
    )


async def _seed_email(
    db_session,
    person_id: uuid.UUID,
    address: str,
) -> None:
    await db_session.execute(
        text(
            "INSERT INTO emails (person_id, address, is_primary) "
            "VALUES (:pid, :addr, true)"
        ),
        {"pid": str(person_id), "addr": address},
    )


async def _seed_phone(
    db_session,
    person_id: uuid.UUID,
    e164: str,
) -> None:
    await db_session.execute(
        text(
            "INSERT INTO phones (person_id, e164, is_primary) "
            "VALUES (:pid, :e164, true)"
        ),
        {"pid": str(person_id), "e164": e164},
    )


async def test_stage1_deterministic_blocking(db_session, seeded_tenants):
    hipaa = seeded_tenants["hipaa"]
    non_hipaa = seeded_tenants["non_hipaa"]

    # Tenant A (non_hipaa) — 5 persons, some with shared emails
    ta = non_hipaa
    a1, a2, a3, a4, a5 = [uuid.uuid4() for _ in range(5)]
    await _seed_person(db_session, a1, ta, display_name="Alice", given_name="Alice", family_name="Smith")
    await _seed_person(db_session, a2, ta, display_name="Bob", given_name="Bob", family_name="Jones")
    await _seed_person(db_session, a3, ta, display_name="Carol", given_name="Carol", family_name="Lee")
    await _seed_person(db_session, a4, ta, display_name="Dave", given_name="Dave", family_name="Brown")
    await _seed_person(db_session, a5, ta, display_name="Eve", given_name="Eve", family_name="Wilson")
    await _seed_email(db_session, a1, "REMOVED")
    await _seed_email(db_session, a2, "REMOVED")
    await _seed_email(db_session, a3, "REMOVED")

    # Tenant B (hipaa) — 5 persons, some with shared phones
    tb = hipaa
    b1, b2, b3, b4, b5 = [uuid.uuid4() for _ in range(6)][:5]
    await _seed_person(db_session, b1, tb, display_name="Frank", given_name="Frank", family_name="Garcia")
    await _seed_person(db_session, b2, tb, display_name="Grace", given_name="Grace", family_name="Martinez")
    await _seed_person(db_session, b3, tb, display_name="Hank", given_name="Hank", family_name="Lopez")
    await _seed_person(db_session, b4, tb, display_name="Ivy", given_name="Ivy", family_name="Gonzalez")
    await _seed_person(db_session, b5, tb, display_name="Jack", given_name="Jack", family_name="Perez")
    await _seed_phone(db_session, b1, "+12025551234")
    await _seed_phone(db_session, b2, "+12025551234")
    await _seed_phone(db_session, b3, "+12025555678")

    # Cross-tenant duplicate (same email across tenants — should NOT be paired)
    cross_a = uuid.uuid4()
    cross_b = uuid.uuid4()
    await _seed_person(db_session, cross_a, ta, display_name="CrossA")
    await _seed_person(db_session, cross_b, tb, display_name="CrossB")
    await _seed_email(db_session, cross_a, "REMOVED")
    await _seed_email(db_session, cross_b, "REMOVED")

    await db_session.commit()

    # --- Assertions for non_hipaa tenant ---
    pairs_ta = await stage1_deterministic_blocking(tenant_id=ta, db_session=db_session)
    pair_sets_ta = {frozenset({p.person_a_id, p.person_b_id}) for p in pairs_ta}

    assert frozenset({a1, a2}) in pair_sets_ta, "email block should pair a1-a2"

    # No cross-tenant pairs should appear in either tenant's result
    for p in pairs_ta:
        assert p.tenant_id == ta

    for p in pairs_ta:
        assert p.tenant_id == ta, "all pairs must belong to the queried tenant"

    # --- Assertions for hipaa tenant ---
    pairs_tb = await stage1_deterministic_blocking(tenant_id=tb, db_session=db_session)
    pair_sets_tb = {frozenset({p.person_a_id, p.person_b_id}) for p in pairs_tb}

    assert frozenset({b1, b2}) in pair_sets_tb, "phone block should pair b1-b2"

    for p in pairs_tb:
        assert p.tenant_id == tb

    # Cross-tenant pair (cross_a, cross_b) must NOT appear in either tenant
    cross_pair = frozenset({cross_a, cross_b})
    assert cross_pair not in pair_sets_ta
    assert cross_pair not in pair_sets_tb


async def test_stage1_email_blocking(db_session, seeded_tenants):
    a_id = uuid.uuid4()
    b_id = uuid.uuid4()
    tenant_id = seeded_tenants["non_hipaa"]

    await _seed_person(db_session, a_id, tenant_id, display_name="Alice", given_name="Alice", family_name="Smith")
    await _seed_person(db_session, b_id, tenant_id, display_name="Bob", given_name="Bob", family_name="Jones")
    await _seed_email(db_session, a_id, "REMOVED")
    await _seed_email(db_session, b_id, "REMOVED")
    await db_session.commit()

    result = await stage1_deterministic_blocking(tenant_id=tenant_id, db_session=db_session)

    assert len(result) >= 1
    pair_ids = {pid for p in result for pid in (p.person_a_id, p.person_b_id)}
    assert a_id in pair_ids
    assert b_id in pair_ids


async def test_stage1_phone_blocking(db_session, seeded_tenants):
    a_id = uuid.uuid4()
    b_id = uuid.uuid4()
    tenant_id = seeded_tenants["non_hipaa"]

    await _seed_person(db_session, a_id, tenant_id, display_name="Charlie", given_name="Charlie", family_name="Brown")
    await _seed_person(db_session, b_id, tenant_id, display_name="Diana", given_name="Diana", family_name="Prince")
    await _seed_phone(db_session, a_id, "+14155551234")
    await _seed_phone(db_session, b_id, "+14155551234")
    await db_session.commit()

    result = await stage1_deterministic_blocking(tenant_id=tenant_id, db_session=db_session)

    assert len(result) >= 1
    pair_ids = {pid for p in result for pid in (p.person_a_id, p.person_b_id)}
    assert a_id in pair_ids
    assert b_id in pair_ids


async def test_stage1_name_blocking(db_session, seeded_tenants):
    tenant_id = seeded_tenants["non_hipaa"]

    # "Smith" and "Smythe" both have DM primary = SM0
    a_id = uuid.uuid4()
    b_id = uuid.uuid4()

    await _seed_person(
        db_session, a_id, tenant_id,
        display_name="John Smith",
        given_name="John",
        family_name="Smith",
    )
    await _seed_person(
        db_session, b_id, tenant_id,
        display_name="Jane Smythe",
        given_name="Jane",
        family_name="Smythe",
    )
    await db_session.commit()

    result = await stage1_deterministic_blocking(tenant_id=tenant_id, db_session=db_session)

    pairing = next(
        (p for p in result if {p.person_a_id, p.person_b_id} == {a_id, b_id}),
        None,
    )
    assert pairing is not None, (
        "expected a dmetaphone match for Smith/Smythe with same first initial"
    )
    assert pairing.blocking_key == "dmetaphone_lastname_first_initial"


async def test_rls_isolation(db_session, seeded_tenants):
    hipaa = seeded_tenants["hipaa"]
    non_hipaa = seeded_tenants["non_hipaa"]

    # Same email across tenants — should NOT be paired by either tenant's blocking
    xa = uuid.uuid4()
    xb = uuid.uuid4()
    await _seed_person(db_session, xa, non_hipaa, display_name="Xavier")
    await _seed_person(db_session, xb, hipaa, display_name="Yolanda")
    await _seed_email(db_session, xa, "REMOVED")
    await _seed_email(db_session, xb, "REMOVED")

    # Same phone across tenants
    ya = uuid.uuid4()
    yb = uuid.uuid4()
    await _seed_person(db_session, ya, non_hipaa, display_name="Zack")
    await _seed_person(db_session, yb, hipaa, display_name="Abby")
    await _seed_phone(db_session, ya, "+12025551234")
    await _seed_phone(db_session, yb, "+12025551234")

    await db_session.commit()

    pairs_hipaa = await stage1_deterministic_blocking(tenant_id=hipaa, db_session=db_session)
    pairs_non_hipaa = await stage1_deterministic_blocking(tenant_id=non_hipaa, db_session=db_session)

    all_pairs = pairs_hipaa + pairs_non_hipaa
    for p in all_pairs:
        assert p.tenant_id in (hipaa, non_hipaa)

    pair_ids = {(p.person_a_id, p.person_b_id) for p in all_pairs}
    cross_email_pair = (xa, xb) if xa < xb else (xb, xa)
    cross_phone_pair = (ya, yb) if ya < yb else (yb, ya)
    assert cross_email_pair not in pair_ids
    assert cross_phone_pair not in pair_ids
