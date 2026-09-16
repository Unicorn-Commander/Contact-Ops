"""Real PostgreSQL + real isolated FalkorDB projection and replay contracts."""

import asyncio
import time
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from contact_ops.agents.graph_sync.current_projection import PROJECTION, drain_current_projection
from contact_ops.agents.graph_sync.falkordb_client import FalkorDBGraphClient, TenantGraph


@pytest.fixture(scope="session")
def falkor_url():
    import redis
    from testcontainers.core.container import DockerContainer

    with DockerContainer("falkordb/falkordb:latest").with_exposed_ports(6379) as container:
        url = f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"
        client = redis.Redis.from_url(url)
        for _ in range(80):
            try:
                if client.ping():
                    break
            except redis.RedisError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("isolated FalkorDB did not start")
        client.close()
        yield url


def client_for(url):
    return FalkorDBGraphClient(settings=SimpleNamespace(FALKORDB_URL=url))


async def seed(db):
    tid, foreign, person, org, foreign_org, role = [uuid.uuid4() for _ in range(6)]
    for tenant in [tid, foreign]:
        await db.execute(
            text("""INSERT INTO tenants(id,slug,kind,display_name,owner_user_id,
          qdrant_namespace,garage_bucket_prefix,graph_name)
          VALUES (:id,:slug,'personal','Synthetic',:id,:slug,:slug,:graph)"""),
            {"id": tenant, "slug": "test-" + tenant.hex, "graph": "contact_ops__" + tenant.hex},
        )
    for oid, tenant in [(org, tid), (foreign_org, foreign)]:
        await db.execute(
            text("""INSERT INTO organizations(id,legal_name,display_name,canonical_owner_tenant_id)
          VALUES (:id,'Synthetic employer','Synthetic employer',:tid)"""),
            {"id": oid, "tid": tenant},
        )
    await db.execute(
        text("""INSERT INTO persons(id,display_name,canonical_owner_tenant_id)
      VALUES (:id,'Synthetic person',:tid)"""),
        {"id": person, "tid": tid},
    )
    await db.execute(
        text("""INSERT INTO person_org_role(id,person_id,organization_id,role_type,title)
      VALUES (:id,:pid,:oid,'employee','Synthetic role')"""),
        {"id": role, "pid": person, "oid": org},
    )
    await db.commit()
    return SimpleNamespace(
        tid=tid,
        foreign=foreign,
        person=person,
        org=org,
        foreign_org=foreign_org,
        role=role,
        target=TenantGraph(tid, "contact_ops__" + tid.hex),
    )


async def drain(sessions, client):
    async with sessions() as db:
        return await drain_current_projection(db=db, client=client, batch_size=1000)


@pytest.mark.asyncio
async def test_atomic_queue_and_pending_coalescing(postgres_container):
    engine = create_async_engine(postgres_container, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            data = await seed(db)
            count = await db.scalar(
                text(
                    "SELECT count(*) FROM graph_sync_outbox WHERE tenant_id=:t AND "
                    "entity_kind='person' AND entity_id=:p AND status='pending'"
                ),
                {"t": data.tid, "p": data.person},
            )
            assert (
                count == 1
            )  # Person insert + employment insert coalesce in the source transaction.
            pid = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO persons(id,display_name,canonical_owner_tenant_id) "
                    "VALUES (:p,'Rolled back',:t)"
                ),
                {"p": pid, "t": data.tid},
            )
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM graph_sync_outbox WHERE entity_id=:p"), {"p": pid}
                )
                == 1
            )
            await db.rollback()
            assert (
                await db.scalar(text("SELECT count(*) FROM persons WHERE id=:p"), {"p": pid}) == 0
            )
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM graph_sync_outbox WHERE entity_id=:p"), {"p": pid}
                )
                == 0
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_graph_replay_updates_end_delete_and_foreign_fence(
    postgres_container, falkor_url
):
    engine = create_async_engine(postgres_container, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    client = client_for(falkor_url)
    try:
        async with sessions() as db:
            data = await seed(db)
        assert await drain(sessions, client) > 0
        graph = await client.query(
            data.target, "MATCH (p:Person)-[r:WORKS_AT]->(o:Organization) RETURN p.id,r.id,o.id"
        )
        assert graph.rows == [[str(data.person), str(data.role), str(data.org)]]
        async with sessions() as db:
            # Replay the exact references, with malicious stale payload metadata ignored.
            await db.execute(
                text("SELECT enqueue_current_graph(:t,'person',:p)"),
                {"t": data.tid, "p": data.person},
            )
            await db.execute(
                text(
                    "UPDATE graph_sync_outbox SET payload=payload || "
                    "jsonb_build_object('display_name','Stale fake') WHERE "
                    "tenant_id=:t AND status='pending'"
                ),
                {"t": data.tid},
            )
            await db.commit()
        await drain(sessions, client)
        assert (
            await client.query(
                data.target, "MATCH (p:Person)-[r:WORKS_AT]->() RETURN p.display_name,count(r)"
            )
        ).rows == [["Synthetic person", 1]]
        async with sessions() as db:
            await db.execute(
                text("UPDATE persons SET display_name='Updated synthetic' WHERE id=:p"),
                {"p": data.person},
            )
            await db.execute(
                text("UPDATE person_org_role SET ended_at=CURRENT_DATE WHERE id=:r"),
                {"r": data.role},
            )
            # Corrupt cross-tenant reference cannot leak even under admin DSN.
            await db.execute(
                text(
                    "INSERT INTO person_org_role(person_id,organization_id,role_type)"
                    " VALUES (:p,:o,'employee')"
                ),
                {"p": data.person, "o": data.foreign_org},
            )
            await db.commit()
        await drain(sessions, client)
        assert (await client.query(data.target, "MATCH (p:Person) RETURN p.display_name")).rows == [
            ["Updated synthetic"]
        ]
        assert (
            await client.query(data.target, "MATCH ()-[r:WORKS_AT]->() RETURN count(r)")
        ).rows == [[0]]
        assert (
            await client.query(
                data.target, "MATCH (n {id:$id}) RETURN count(n)", {"id": str(data.foreign_org)}
            )
        ).rows == [[0]]
        async with sessions() as db:
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM person_org_role WHERE id=:r AND ended_at IS NOT NULL"
                    ),
                    {"r": data.role},
                )
                == 1
            )
            await db.execute(
                text("UPDATE person_org_role SET ended_at=NULL WHERE id=:r"), {"r": data.role}
            )
            await db.commit()
        await drain(sessions, client)
        assert (
            await client.query(data.target, "MATCH ()-[r:WORKS_AT]->() RETURN count(r)")
        ).rows == [[1]]
        async with sessions() as db:
            await db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": data.org})
            await db.commit()
        await drain(sessions, client)
        assert (
            await client.query(data.target, "MATCH ()-[r:WORKS_AT]->() RETURN count(r)")
        ).rows == [[0]]
        assert (
            await client.query(
                data.target,
                "MATCH (o:Organization {id:$id}) RETURN count(o)",
                {"id": str(data.org)},
            )
        ).rows == [[0]]
        # Unowned foreign node survives deletion cleanup within the target graph.
        await client.query(
            data.target,
            "CREATE (:Person {id:'unowned',tenant_id:'foreign',display_name:'Unowned synthetic'})",
        )
        async with sessions() as db:
            await db.execute(text("DELETE FROM persons WHERE id=:p"), {"p": data.person})
            await db.commit()
        await drain(sessions, client)
        assert (await client.query(data.target, "MATCH (p:Person) RETURN p.id")).rows == [
            ["unowned"]
        ]
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_drainers_skip_and_source_update_is_not_lost(
    postgres_container, falkor_url
):
    engine = create_async_engine(postgres_container, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    real = client_for(falkor_url)
    entered, release = asyncio.Event(), asyncio.Event()

    class PausingClient:
        async def query(self, target, cypher, params=None):
            if target.tenant_id == data.tid and not entered.is_set():
                entered.set()
                await release.wait()
            return await real.query(target, cypher, params)

    try:
        async with sessions() as db:
            data = await seed(db)
        first = asyncio.create_task(drain(sessions, PausingClient()))
        await asyncio.wait_for(entered.wait(), 10)
        assert await asyncio.wait_for(drain(sessions, real), 5) == 0

        async def update():
            async with sessions() as db:
                await db.execute(
                    text("UPDATE persons SET display_name='Concurrent latest' WHERE id=:p"),
                    {"p": data.person},
                )
                await db.commit()

        updating = asyncio.create_task(update())
        await asyncio.sleep(0.1)
        release.set()
        await asyncio.wait_for(first, 20)
        await asyncio.wait_for(updating, 20)
        await drain(sessions, real)
        assert (
            await real.query(
                data.target,
                "MATCH (p:Person {id:$id}) RETURN p.display_name",
                {"id": str(data.person)},
            )
        ).rows == [["Concurrent latest"]]
        assert (
            await real.query(data.target, "MATCH ()-[r:WORKS_AT]->() RETURN count(r)")
        ).rows == [[1]]
    finally:
        release.set()
        await real.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_application_role_trigger_and_explicit_backfill_guards(postgres_container):
    from contact_ops.ops.current_graph_backfill import queue_backfill

    engine = create_async_engine(postgres_container, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            data = await seed(db)
            await db.execute(
                text("DELETE FROM graph_sync_outbox WHERE tenant_id IN (:t,:f)"),
                {"t": data.tid, "f": data.foreign},
            )
            await db.commit()
            preview = await queue_backfill(
                db, tenant_id=data.tid, expected_owner=data.tid, expected_people=1, expected_orgs=1
            )
            assert preview["apply"] is False
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM graph_sync_outbox WHERE tenant_id=:t"),
                    {"t": data.tid},
                )
                == 0
            )
            with pytest.raises(ValueError):
                await queue_backfill(
                    db,
                    tenant_id=data.tid,
                    expected_owner=data.foreign,
                    expected_people=1,
                    expected_orgs=1,
                    apply=True,
                )
            with pytest.raises(ValueError):
                await queue_backfill(
                    db,
                    tenant_id=data.tid,
                    expected_owner=data.tid,
                    expected_people=99,
                    expected_orgs=1,
                    apply=True,
                )
            for _ in range(2):
                await queue_backfill(
                    db,
                    tenant_id=data.tid,
                    expected_owner=data.tid,
                    expected_people=1,
                    expected_orgs=1,
                    apply=True,
                )
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM graph_sync_outbox WHERE tenant_id=:t"),
                    {"t": data.tid},
                )
                == 2
            )
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM graph_sync_outbox WHERE tenant_id=:t"),
                    {"t": data.foreign},
                )
                == 0
            )
            await db.rollback()
            await db.execute(text("SET LOCAL ROLE contact_ops_app"))
            await db.execute(
                text("SELECT set_config('app.tenant_id',:t,true)"), {"t": str(data.tid)}
            )
            pid = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO persons(id,display_name,canonical_owner_tenant_id) "
                    "VALUES (:p,'App role synthetic',:t)"
                ),
                {"p": pid, "t": data.tid},
            )
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM graph_sync_outbox WHERE entity_id=:p"), {"p": pid}
                )
                == 1
            )
            # Foreign tenant is hidden from the invoker function, never queued.
            await db.execute(
                text("SELECT enqueue_current_graph(:t,'organization',:o)"),
                {"t": data.foreign, "o": data.foreign_org},
            )
            await db.execute(text("RESET ROLE"))
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM graph_sync_outbox WHERE tenant_id=:t"),
                    {"t": data.foreign},
                )
                == 0
            )
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_graph_write_remains_retryable_without_source_details(postgres_container):
    engine = create_async_engine(postgres_container, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    class FailingClient:
        async def query(self, *args, **kwargs):
            raise RuntimeError("PRIVATE_SOURCE_STRING_MUST_NOT_BE_LOGGED")

    try:
        async with sessions() as db:
            data = await seed(db)
        assert await drain(sessions, FailingClient()) == 0
        async with sessions() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT status,attempts,last_error FROM graph_sync_outbox WHERE "
                        "tenant_id=:t AND status='pending'"
                    ),
                    {"t": data.tid},
                )
            ).all()
            assert rows and all(r.status == "pending" and r.attempts == 1 for r in rows)
            assert all("PRIVATE_SOURCE_STRING" not in r.last_error for r in rows)
            await db.execute(
                text("DELETE FROM graph_sync_outbox WHERE tenant_id IN (:t,:f)"),
                {"t": data.tid, "f": data.foreign},
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_graph_route_refuses_all_writes(postgres_container):
    engine = create_async_engine(postgres_container, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    class NoWriteClient:
        calls = 0

        async def query(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("graph routing collision must fail before query")

    client = NoWriteClient()
    try:
        async with sessions() as db:
            data = await seed(db)
            await db.execute(
                text("UPDATE tenants SET graph_name=:graph WHERE id=:tid"),
                {"graph": data.target.graph_name, "tid": data.foreign},
            )
            await db.commit()
        assert await drain(sessions, client) == 0
        assert client.calls == 0
        async with sessions() as db:
            await db.execute(
                text("UPDATE tenants SET graph_name=:graph WHERE id=:tid"),
                {"graph": "contact_ops__" + data.foreign.hex, "tid": data.foreign},
            )
            await db.execute(
                text("DELETE FROM graph_sync_outbox WHERE tenant_id IN (:t,:f)"),
                {"t": data.tid, "f": data.foreign},
            )
            await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_round_trip_preserves_sources_and_legacy_queue(postgres_container):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).parents[1] / "alembic/versions/0049_current_graph_projection.py"
    spec = importlib.util.spec_from_file_location("current_graph_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_async_engine(postgres_container, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    legacy = uuid.uuid4()
    try:
        async with sessions() as db:
            data = await seed(db)
            await db.execute(
                text(
                    "INSERT INTO "
                    "graph_sync_outbox(id,entity_kind,entity_id,op,payload,tenant_id,graph_name,status)"
                    " VALUES (:id,'person',:p,'upsert','{}'::jsonb,:t,:g,'sent')"
                ),
                {"id": legacy, "p": data.person, "t": data.tid, "g": data.target.graph_name},
            )
            await db.commit()
        async with engine.begin() as connection:

            def round_trip(conn):
                with Operations.context(MigrationContext.configure(conn)):
                    migration.downgrade()
                    assert (
                        conn.scalar(
                            text("SELECT count(*) FROM persons WHERE id=:p"), {"p": data.person}
                        )
                        == 1
                    )
                    assert (
                        conn.scalar(
                            text("SELECT count(*) FROM graph_sync_outbox WHERE id=:id"),
                            {"id": legacy},
                        )
                        == 1
                    )
                    assert (
                        conn.scalar(
                            text(
                                "SELECT count(*) FROM graph_sync_outbox WHERE "
                                "payload->>'projection'=:v"
                            ),
                            {"v": PROJECTION},
                        )
                        == 0
                    )
                    migration.upgrade()
                    assert (
                        conn.scalar(
                            text(
                                "SELECT count(*) FROM graph_sync_outbox WHERE "
                                "payload->>'projection'=:v"
                            ),
                            {"v": PROJECTION},
                        )
                        == 0
                    )

            await connection.run_sync(round_trip)
    finally:
        await engine.dispose()
