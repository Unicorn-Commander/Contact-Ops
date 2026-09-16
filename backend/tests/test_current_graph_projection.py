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


@pytest.mark.asyncio
@pytest.mark.parametrize("collision", ["person", "organization", "relationship"])
async def test_same_id_unowned_collision_preserves_existing_graph(
    postgres_container, falkor_url, collision
):
    from contact_ops.agents.graph_sync.current_projection import (
        ProjectionOwnershipError,
        project_current,
    )

    engine = create_async_engine(postgres_container, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    client = client_for(falkor_url)
    try:
        async with sessions() as db:
            data = await seed(db)
        await drain(sessions, client)
        # An obsolete owned edge must not be pruned when any desired object collides.
        await client.query(
            data.target,
            "MATCH (p:Person)-[r:WORKS_AT]->(o) "
            "CREATE (p)-[:WORKS_AT {id:'obsolete',tenant_id:$t,projection:$m}]->(o)",
            {"t": str(data.tid), "m": PROJECTION},
        )
        if collision == "relationship":
            await client.query(
                data.target,
                "MATCH ()-[r:WORKS_AT {id:$id}]->() "
                "REMOVE r.projection SET r.title='Legacy untouched'",
                {"id": str(data.role)},
            )
        else:
            entity_id = data.person if collision == "person" else data.org
            await client.query(
                data.target,
                "MATCH (n {id:$id}) REMOVE n.projection SET n.legacy='untouched'",
                {"id": str(entity_id)},
            )
        before = (
            await client.query(
                data.target, "MATCH (a)-[r]->(b) RETURN a.id,r.id,b.id,r.projection ORDER BY r.id"
            )
        ).rows
        async with sessions() as db:
            row = SimpleNamespace(
                tenant_id=data.tid,
                graph_name=data.target.graph_name,
                entity_kind="person",
                entity_id=data.person,
            )
            with pytest.raises(ProjectionOwnershipError):
                await project_current(db, client, row)
        assert (
            await client.query(
                data.target, "MATCH (a)-[r]->(b) RETURN a.id,r.id,b.id,r.projection ORDER BY r.id"
            )
        ).rows == before
        if collision != "relationship":
            assert (
                await client.query(
                    data.target,
                    "MATCH(n {id:$id}) RETURN n.projection,n.legacy",
                    {"id": str(entity_id)},
                )
            ).rows == [[None, "untouched"]]
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_owned_node_with_unowned_incident_edges_refuses_deletion(
    postgres_container, falkor_url
):
    from contact_ops.agents.graph_sync.current_projection import (
        ProjectionOwnershipError,
        project_current,
    )

    engine = create_async_engine(postgres_container, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    client = client_for(falkor_url)
    try:
        async with sessions() as db:
            data = await seed(db)
        await drain(sessions, client)
        await client.query(
            data.target,
            "MATCH(p:Person {id:$p}) "
            "CREATE (a:Legacy {id:'legacy-in'})-[:KNOWS {id:'legacy-in-edge'}]->(p) "
            "CREATE (p)-[:KNOWS {id:'legacy-out-edge'}]->(b:Legacy {id:'legacy-out'})",
            {"p": str(data.person)},
        )
        async with sessions() as db:
            await db.execute(
                text("UPDATE persons SET merge_status='archived' WHERE id=:p"), {"p": data.person}
            )
            await db.commit()
            row = SimpleNamespace(
                tenant_id=data.tid,
                graph_name=data.target.graph_name,
                entity_kind="person",
                entity_id=data.person,
            )
            with pytest.raises(ProjectionOwnershipError):
                await project_current(db, client, row)
        # Drain must record a retry, never claim success for the refused source.
        await drain(sessions, client)
        async with sessions() as db:
            result = (
                await db.execute(
                    text(
                        "SELECT status,attempts FROM graph_sync_outbox "
                        "WHERE entity_id=:p AND status='pending'"
                    ),
                    {"p": data.person},
                )
            ).one()
            assert result == ("pending", 1)
        assert (
            await client.query(
                data.target,
                "MATCH(p:Person {id:$p})-[r]-() RETURN r.id ORDER BY r.id",
                {"p": str(data.person)},
            )
        ).rows == sorted([[str(data.role)], ["legacy-in-edge"], ["legacy-out-edge"]])
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_role_reassignment_reconciles_exact_endpoint(postgres_container, falkor_url):
    engine = create_async_engine(postgres_container, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    client = client_for(falkor_url)
    try:
        async with sessions() as db:
            data = await seed(db)
        await drain(sessions, client)
        new_org = uuid.uuid4()
        async with sessions() as db:
            await db.execute(
                text(
                    "INSERT INTO organizations(id,legal_name,display_name,"
                    "canonical_owner_tenant_id) "
                    "VALUES(:id,'New synthetic','New synthetic',:t)"
                ),
                {"id": new_org, "t": data.tid},
            )
            await db.execute(
                text("UPDATE person_org_role SET organization_id=:o WHERE id=:r"),
                {"o": new_org, "r": data.role},
            )
            await db.commit()
        await drain(sessions, client)
        assert (
            await client.query(
                data.target,
                "MATCH(p)-[r:WORKS_AT {id:$id}]->(o) RETURN p.id,o.id",
                {"id": str(data.role)},
            )
        ).rows == [[str(data.person), str(new_org)]]
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_atomic_write_guards_refuse_unowned_objects_without_preflight(
    postgres_container, falkor_url
):
    from contact_ops.agents.graph_sync.current_projection import DELETE_NODE, LINK, PERSON

    engine = create_async_engine(postgres_container, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    client = client_for(falkor_url)
    try:
        async with sessions() as db:
            data = await seed(db)
        params = {
            "id": str(data.person),
            "tenant_id": str(data.tid),
            "projection": PROJECTION,
            "name": "Must not replace",
        }
        await client.query(data.target, "CREATE (:Legacy {id:$id,name:'Existing'})", params)
        assert not (await client.query(data.target, PERSON, params)).rows
        assert (
            await client.query(data.target, "MATCH(n) RETURN labels(n),n.name,n.projection")
        ).rows == [["[Legacy]", "Existing", None]]
        await client.query(data.target, "MATCH(n:Legacy) DELETE n")
        await drain(sessions, client)
        await client.query(data.target, "MATCH(p:Person)-[r:WORKS_AT]->(o) REMOVE r.projection")
        link_params = {
            **params,
            "id": str(data.role),
            "person_id": str(data.person),
            "organization_id": str(data.org),
            "title": "Must not replace",
            "role_type": "employee",
            "since": None,
            "confidence": 1,
        }
        assert not (await client.query(data.target, LINK, link_params)).rows
        # Atomic delete itself also refuses the unrelated incident edge.
        assert not (await client.query(data.target, DELETE_NODE, params)).rows
        assert (
            await client.query(data.target, "MATCH(p)-[r]->(o) RETURN r.title,r.projection")
        ).rows == [["Synthetic role", None]]
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_shared_carddav_write_fence_and_membership_archive_with_invoker_queue(
    postgres_container,
):
    from contact_ops.carddav.addressbook import (
        CrossTenantWriteRefused,
        apply_vcard_text_to_db,
        soft_delete_person_for_tenant,
    )

    engine = create_async_engine(postgres_container, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            data = await seed(db)
            await db.execute(
                text("INSERT INTO person_tenant_membership(person_id,tenant_id) VALUES(:p,:t)"),
                {"p": data.person, "t": data.foreign},
            )
            await db.commit()
            await db.execute(text("SET LOCAL ROLE contact_ops_app"))
            await db.execute(
                text("SELECT set_config('app.tenant_id',:t,true)"), {"t": str(data.foreign)}
            )
            with pytest.raises(CrossTenantWriteRefused):
                await apply_vcard_text_to_db(
                    db,
                    vcard_text=("BEGIN:VCARD\r\nVERSION:3.0\r\nUID:shared-test\r\n"
                        "FN:Must not replace\r\nEND:VCARD\r\n"),
                    principal_tenant_id=data.foreign,
                    principal_user_id=str(data.foreign),
                    target_vcard_uid="shared-test",
                    existing_person_id=data.person,
                )
            # Supported shared-tenant archive changes membership only and succeeds.
            assert await soft_delete_person_for_tenant(
                db, person_id=data.person, tenant_id=data.foreign
            )
            await db.commit()
            assert (
                await db.scalar(
                    text(
                        "SELECT visibility FROM person_tenant_membership "
                        "WHERE person_id=:p AND tenant_id=:t"
                    ),
                    {"p": data.person, "t": data.foreign},
                )
                == "archived"
            )
            assert (
                await db.execute(
                    text("SELECT display_name,merge_status FROM persons WHERE id=:p"),
                    {"p": data.person},
                )
            ).one() == ("Synthetic person", "canonical")
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM graph_sync_outbox WHERE entity_id=:p AND tenant_id=:t"
                    ),
                    {"p": data.person, "t": data.foreign},
                )
                == 0
            )
    finally:
        await engine.dispose()
