"""Regression test for the dedup candidate-DataFrame loader's id binding.

``_build_candidates_dataframe`` loads persons / emails / phones for the
candidate ids with ``WHERE id = ANY(CAST(:ids AS uuid[]))``. The original code
bound a Postgres text array-literal string with a ``:ids::uuid[]`` postfix cast,
which works under psycopg2 but the asyncpg driver (the agent runs under it)
rejects with ``syntax error at or near ":"``. The dedup agent never ran under
asyncpg in CI (its end-to-end test errors earlier on optional heavy deps), so
this guards the loader directly: it must build a row under asyncpg without a
binding error.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from contact_ops.agents.dedup.agent import _build_candidates_dataframe
from contact_ops.agents.dedup.blocking import CandidatePair


async def _seed_person(
    db,
    pid: uuid.UUID,
    tid: uuid.UUID,
    given: str,
    family: str,
    birthday: dict | None = None,
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO persons (id, canonical_owner_tenant_id, merge_status,
                                 display_name, given_name, family_name, birthday,
                                 created_at, updated_at)
            VALUES (:id, :tid, 'canonical', :dn, :gn, :fn,
                    CAST(:bd AS jsonb), now(), now())
            """
        ),
        {
            "id": pid,
            "tid": tid,
            "dn": f"{given} {family}",
            "gn": given,
            "fn": family,
            "bd": json.dumps(birthday) if birthday else None,
        },
    )


async def _seed_email(db, pid: uuid.UUID, address: str) -> None:
    await db.execute(
        text("INSERT INTO emails (person_id, address, is_primary) VALUES (:p, :a, true)"),
        {"p": pid, "a": address},
    )


async def _seed_address(db, pid: uuid.UUID, formatted: str) -> None:
    await db.execute(
        text(
            "INSERT INTO postal_addresses (person_id, formatted, is_primary) "
            "VALUES (:p, :f, true)"
        ),
        {"p": pid, "f": formatted},
    )


async def _seed_company(db, pid: uuid.UUID, tid: uuid.UUID, name: str) -> None:
    org_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO organizations (id, legal_name, display_name, "
            "canonical_owner_tenant_id) VALUES (:o, :n, :n, :t)"
        ),
        {"o": org_id, "n": name, "t": tid},
    )
    await db.execute(
        text(
            "INSERT INTO person_org_role (person_id, organization_id, role_type, "
            "is_primary) VALUES (:p, :o, 'employee', true)"
        ),
        {"p": pid, "o": org_id},
    )


@pytest.mark.asyncio
async def test_build_candidates_dataframe_binds_ids_under_asyncpg(
    db_session, seeded_tenants
):
    tid = seeded_tenants["non_hipaa"]
    a, b = uuid.uuid4(), uuid.uuid4()
    # birthday is a JSONB partial-date {year,month,day}; the loader must extract
    # dob_year/month/day via the JSONB operators, not EXTRACT() (it is not a date).
    await _seed_person(db_session, a, tid, "Alice", "Smith", birthday={"year": 1990, "month": 5, "day": 12})
    await _seed_person(db_session, b, tid, "Alicia", "Smith")
    await _seed_email(db_session, a, "REMOVED")
    await _seed_email(db_session, b, "REMOVED")
    # address (postal_addresses) + company (person_org_role -> organizations) are
    # sourced from their own tables, not persons columns.
    await _seed_address(db_session, a, "1 Main St, Springfield")
    await _seed_company(db_session, a, tid, "Acme Corp")
    await db_session.commit()

    pair = CandidatePair(person_a_id=a, person_b_id=b, blocking_key="email", tenant_id=tid)

    # The asyncpg id binding is what regressed; this must not raise.
    df = await _build_candidates_dataframe(candidates=[pair], tenant_id=tid, db=db_session)

    assert len(df) == 1
    row = df.iloc[0]
    assert row["person_id_l"] == str(a)
    assert row["person_id_r"] == str(b)
    # The emails query (also id-bound) resolved the shared primary address.
    assert row["email_l"] == "REMOVED"
    assert row["email_r"] == "REMOVED"
    # first_name comparison columns were assembled from the loaded person rows.
    assert row["first_name_l"] == "Alice"
    assert row["first_name_r"] == "Alicia"
    # dob_year came from the JSONB birthday extraction (the EXTRACT() regression).
    assert int(row["dob_year_l"]) == 1990
    assert int(row["dob_month_l"]) == 5
    # address + company are sourced from postal_addresses / person_org_role.
    assert row["address_l"] == "1 Main St, Springfield"
    assert row["company_l"] == "Acme Corp"


@pytest.mark.asyncio
async def test_build_candidates_dataframe_empty_is_noop(db_session, seeded_tenants):
    df = await _build_candidates_dataframe(
        candidates=[], tenant_id=seeded_tenants["non_hipaa"], db=db_session
    )
    assert df.empty


@pytest.mark.asyncio
async def test_load_person_summary_real_schema(db_session, seeded_tenants):
    """_load_person_summary must select real columns + keep the evidence keys.

    Regression for the same speculative-schema bug as the candidate loader:
    nickname comes from nicknames[], and company/address/government_id are NULL
    placeholders (not persons columns). The dict keys stay present for the
    evidence-pack builder.
    """
    from contact_ops.agents.dedup.agent import _load_person_summary

    tid = seeded_tenants["non_hipaa"]
    pid = uuid.uuid4()
    await _seed_person(db_session, pid, tid, "Bob", "Jones", birthday={"year": 1980, "month": 1, "day": 2})
    await db_session.execute(
        text("UPDATE persons SET nicknames = ARRAY['Bobby'] WHERE id = :id"),
        {"id": pid},
    )
    await _seed_email(db_session, pid, "REMOVED")
    await _seed_address(db_session, pid, "2 Oak Ave, Shelbyville")
    await _seed_company(db_session, pid, tid, "Globex")
    await db_session.commit()

    summary = await _load_person_summary(pid, db=db_session)

    assert summary["given_name"] == "Bob"
    assert summary["nickname"] == "Bobby"
    assert summary["email"] == "REMOVED"
    # address + company sourced from their own tables; government_id stays a NULL
    # placeholder (no identifiers data source on this deployment).
    assert summary["address"] == "2 Oak Ave, Shelbyville"
    assert summary["company"] == "Globex"
    assert summary["government_id"] is None
