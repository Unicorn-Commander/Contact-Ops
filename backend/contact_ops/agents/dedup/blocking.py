from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text

from contact_ops.agents.dedup.hipaa_fence import PersonRef, crosses_hipaa_fence

CONTACT_OPS_PERSON_NAME_COLLECTION = "contact_ops_person_name"

# Free webmail providers carry no coworker signal: "both use gmail" is noise, not
# evidence of a shared employer. A personal address book heavy in one provider
# would otherwise collapse into a single enormous Key-4 (email-domain) block.
# Excluding these improves precision and bounds block size.
FREE_EMAIL_DOMAINS: tuple[str, ...] = (
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "icloud.com", "me.com", "mac.com",
    "aol.com", "proton.me", "protonmail.com", "gmx.com", "mail.com", "zoho.com",
    "comcast.net", "verizon.net", "sbcglobal.net",
)


@dataclass(frozen=True)
class CandidatePair:
    person_a_id: UUID
    person_b_id: UUID
    blocking_key: str
    tenant_id: UUID


@dataclass
class BlockingResult:
    candidates: list[CandidatePair]
    stage1_count: int = 0
    stage2_count: int = 0
    union_count: int = 0
    hipaa_blocked_count: int = 0


async def stage1_deterministic_blocking(
    *,
    tenant_id: UUID,
    db_session,
) -> list[CandidatePair]:
    """Stage 1: Generate candidate pairs via deterministic SQL blocking keys.

    Four blocking keys:
    1. Normalized email exact match (via JOIN on emails)
    2. E.164 phone exact match (via JOIN on phones)
    3. Double Metaphone(last_name) + first_initial
    4. Email domain + Soundex(last_name) for coworker pairs

    Uses parameterized SQL via SQLAlchemy text().
    Filters by tenant_id and ensures person_a_id < person_b_id.
    """
    candidates: list[CandidatePair] = []

    # Key 1: Normalized email exact match
    sql_email = text("""
        SELECT p1.id AS p1_id, p2.id AS p2_id
        FROM persons p1
        JOIN emails e1 ON e1.person_id = p1.id
        JOIN emails e2 ON lower(e2.address) = lower(e1.address) AND e2.person_id != p1.id
        JOIN persons p2 ON p2.id = e2.person_id
        WHERE p1.id < p2.id
          AND p1.canonical_owner_tenant_id = :tenant
          AND p2.canonical_owner_tenant_id = :tenant
    """)
    result = await db_session.execute(sql_email, {"tenant": tenant_id})
    for row in result:
        candidates.append(CandidatePair(
            person_a_id=row.p1_id,
            person_b_id=row.p2_id,
            blocking_key="email_exact",
            tenant_id=tenant_id,
        ))

    # Key 2: E.164 phone exact match
    sql_phone = text("""
        SELECT p1.id AS p1_id, p2.id AS p2_id
        FROM persons p1
        JOIN phones ph1 ON ph1.person_id = p1.id
        JOIN phones ph2 ON ph2.e164 = ph1.e164 AND ph2.person_id != p1.id
        JOIN persons p2 ON p2.id = ph2.person_id
        WHERE p1.id < p2.id
          AND p1.canonical_owner_tenant_id = :tenant
          AND p2.canonical_owner_tenant_id = :tenant
    """)
    result = await db_session.execute(sql_phone, {"tenant": tenant_id})
    for row in result:
        candidates.append(CandidatePair(
            person_a_id=row.p1_id,
            person_b_id=row.p2_id,
            blocking_key="phone_exact",
            tenant_id=tenant_id,
        ))

    # Key 3: Double Metaphone(family_name) + first initial of given_name.
    # The phonetic equality is pushed into SQL (served by persons_dmeta_block_idx,
    # migration 0045) so this is an index-driven join, NOT a per-tenant Cartesian
    # product scored row-by-row in Python. dmetaphone is IMMUTABLE; it produces
    # SM0 for both "Smith" and "Smythe" exactly like the prior jellyfish.metaphone,
    # and the blocking_key label is unchanged.
    sql_name_meta = text("""
        SELECT p1.id AS p1_id, p2.id AS p2_id
        FROM persons p1
        JOIN persons p2
          ON p1.id < p2.id
         AND dmetaphone(p1.family_name) = dmetaphone(p2.family_name)
         AND upper(left(p1.given_name, 1)) = upper(left(p2.given_name, 1))
        WHERE p1.family_name IS NOT NULL
          AND p2.family_name IS NOT NULL
          AND p1.given_name IS NOT NULL
          AND p2.given_name IS NOT NULL
          AND p1.canonical_owner_tenant_id = :tenant
          AND p2.canonical_owner_tenant_id = :tenant
    """)
    result = await db_session.execute(sql_name_meta, {"tenant": tenant_id})
    for row in result:
        candidates.append(CandidatePair(
            person_a_id=row.p1_id,
            person_b_id=row.p2_id,
            blocking_key="dmetaphone_lastname_first_initial",
            tenant_id=tenant_id,
        ))

    # Key 4: shared email domain + Soundex(family_name) for coworker pairs.
    # Both equalities are pushed into SQL: soundex via persons_soundex_block_idx
    # and the email domain via emails_domain_idx (migration 0045). This replaces a
    # non-equi emails self-join (e2.person_id != p1.id) that fanned out to a near
    # Cartesian product before the Python soundex/domain filter. soundex and
    # split_part are IMMUTABLE. DISTINCT collapses multiple shared-domain emails on
    # the same person pair.
    sql_email_domain = text("""
        SELECT DISTINCT p1.id AS p1_id, p2.id AS p2_id
        FROM persons p1
        JOIN emails e1 ON e1.person_id = p1.id
        JOIN emails e2
          ON split_part(lower(e2.address::text), '@', 2)
           = split_part(lower(e1.address::text), '@', 2)
         AND e2.person_id <> p1.id
        JOIN persons p2 ON p2.id = e2.person_id AND p1.id < p2.id
        WHERE p1.family_name IS NOT NULL
          AND p2.family_name IS NOT NULL
          AND soundex(p1.family_name) = soundex(p2.family_name)
          AND split_part(lower(e1.address::text), '@', 2) <> ''
          AND split_part(lower(e1.address::text), '@', 2)
              <> ALL(CAST(:free_domains AS text[]))
          AND p1.canonical_owner_tenant_id = :tenant
          AND p2.canonical_owner_tenant_id = :tenant
    """)
    result = await db_session.execute(
        sql_email_domain,
        {"tenant": tenant_id, "free_domains": list(FREE_EMAIL_DOMAINS)},
    )
    for row in result:
        candidates.append(CandidatePair(
            person_a_id=row.p1_id,
            person_b_id=row.p2_id,
            blocking_key="email_domain_soundex_lastname",
            tenant_id=tenant_id,
        ))

    return candidates


async def stage2_embedding_blocking(
    *,
    tenant_id: UUID,
    db_session,
    qdrant_client,
    limit: int = 20,
    score_threshold: float = 0.70,
) -> list[CandidatePair]:
    """Stage 2: Embedding blocking via Qdrant name embedding search.

    For each person with a name_embedding, search top-*limit* cosine similar
    vectors and keep results with score >= *score_threshold*.
    Self-matches and already-seen pairs are skipped.
    """
    from qdrant_client.http import models as qm

    candidates: list[CandidatePair] = []

    sql_emb = text("""
        SELECT id, name_embedding
        FROM persons
        WHERE canonical_owner_tenant_id = :tenant
          AND name_embedding IS NOT NULL
    """)
    result = await db_session.execute(sql_emb, {"tenant": tenant_id})
    person_rows = list(result)

    searched: set[UUID] = set()

    for row in person_rows:
        query_pid: UUID = row.id
        if query_pid in searched:
            continue
        searched.add(query_pid)

        embedding: list[float] = list(row.name_embedding)
        if not embedding:
            continue

        search_result = await qdrant_client.search(
            collection_name=CONTACT_OPS_PERSON_NAME_COLLECTION,
            query_vector=embedding,
            limit=limit,
            query_filter=qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="tenant_id",
                        match=qm.MatchValue(value=str(tenant_id)),
                    ),
                ],
            ),
            score_threshold=score_threshold,
            with_payload=True,
        )

        for point in search_result:
            payload = point.payload or {}
            raw_pid = payload.get("person_id")
            if raw_pid is None:
                continue
            try:
                match_pid = UUID(raw_pid) if isinstance(raw_pid, str) else UUID(bytes=raw_pid)
            except (ValueError, TypeError):
                continue

            if match_pid == query_pid:
                continue

            a, b = (query_pid, match_pid) if query_pid < match_pid else (match_pid, query_pid)
            candidates.append(CandidatePair(
                person_a_id=a,
                person_b_id=b,
                blocking_key="name_embedding",
                tenant_id=tenant_id,
            ))

    return candidates


async def run_blocking_pipeline(
    *,
    tenant_id: UUID,
    db_session,
    qdrant_client,
) -> BlockingResult:
    """Run both stages, then union and deduplicate.

    HIPAA fence pre-check runs on every candidate pair before inclusion.
    Cross-tenant pairs are excluded.
    """
    stage1 = await stage1_deterministic_blocking(
        tenant_id=tenant_id,
        db_session=db_session,
    )
    stage2 = await stage2_embedding_blocking(
        tenant_id=tenant_id,
        db_session=db_session,
        qdrant_client=qdrant_client,
    )

    seen: set[tuple[UUID, UUID]] = set()
    union: list[CandidatePair] = []
    for pair in stage1 + stage2:
        if pair.person_a_id < pair.person_b_id:
            a, b = pair.person_a_id, pair.person_b_id
        else:
            a, b = pair.person_b_id, pair.person_a_id
        key = (a, b)
        if key not in seen:
            seen.add(key)
            union.append(CandidatePair(
                person_a_id=a,
                person_b_id=b,
                blocking_key=pair.blocking_key,
                tenant_id=pair.tenant_id,
            ))

    hipaa_blocked = 0
    final: list[CandidatePair] = []
    for pair in union:
        ref_a = PersonRef(id=pair.person_a_id, tenant_id=pair.tenant_id)
        ref_b = PersonRef(id=pair.person_b_id, tenant_id=pair.tenant_id)
        if await crosses_hipaa_fence(ref_a, ref_b):
            hipaa_blocked += 1
            continue
        final.append(pair)

    return BlockingResult(
        candidates=final,
        stage1_count=len(stage1),
        stage2_count=len(stage2),
        union_count=len(union),
        hipaa_blocked_count=hipaa_blocked,
    )


__all__ = [
    "CONTACT_OPS_PERSON_NAME_COLLECTION",
    "CandidatePair",
    "BlockingResult",
    "stage1_deterministic_blocking",
    "stage2_embedding_blocking",
    "run_blocking_pipeline",
]
