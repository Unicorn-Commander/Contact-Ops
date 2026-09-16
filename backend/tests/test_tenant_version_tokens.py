"""Real PostgreSQL insert, legacy migration, ABA and concurrent-writer proof."""

from __future__ import annotations

import asyncio
import importlib.util
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from contact_ops.mcp.errors import ToolError
from contact_ops.mcp.registry import MCPContext
from contact_ops.mcp.tools import tenancy
from contact_ops.models import Tenant
from contact_ops.models.enums import TenantKind


def context(db, tenant_id):
    return MCPContext(
        tenant_id=tenant_id,
        user_id=str(uuid.uuid4()),
        actor_chain={},
        human_authority=str(tenant_id),
        db=db,
        audit_db=db,
        request_id="tenant-version-test",
        claims={"realm_access": {"roles": ["ADMIN"]}, "scope": "tenant:write tenant:read"},
    )


def test_provision_initializes_token_and_preserves_it_on_replay(postgres_container):
    engine = create_engine(postgres_container.replace("postgresql+asyncpg://", "postgresql://"))
    with engine.begin() as c:
        uc = str(uuid.uuid4())
        tid = c.scalar(
            text("SELECT provision_personal_workspace(:uc, :name, :email)"),
            {"uc": uc, "name": "Synthetic Tenant", "email": "REMOVED"},
        )
        token = c.scalar(text("SELECT etag FROM tenants WHERE id=:tid"), {"tid": tid})
        assert len(token) == 64
        assert (
            c.scalar(
                text("SELECT provision_personal_workspace(:uc, :name, :email)"),
                {"uc": uc, "name": "Ignored", "email": "REMOVED"},
            )
            == tid
        )
        assert c.scalar(text("SELECT etag FROM tenants WHERE id=:tid"), {"tid": tid}) == token
    engine.dispose()


def test_migration_repairs_only_empty_and_downgrade_preserves_tokens(postgres_container):
    engine = create_engine(postgres_container.replace("postgresql+asyncpg://", "postgresql://"))
    path = Path(__file__).parents[1] / "alembic/versions/0048_tenant_version_tokens.py"
    spec = importlib.util.spec_from_file_location("tenant_versions", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.connect() as c:
        transaction = c.begin()
        try:
            with Operations.context(MigrationContext.configure(c)):
                migration.downgrade()
                ids = []
                for token in ("", "existing-token"):
                    tid = uuid.uuid4()
                    ids.append(tid)
                    c.execute(
                        text("""INSERT INTO tenants(id,slug,kind,display_name,owner_user_id,
                        qdrant_namespace,garage_bucket_prefix,etag)
                        VALUES (:id,:slug,'personal','Synthetic',:owner,'test','test',:token)"""),
                        {
                            "id": tid,
                            "slug": "test-" + tid.hex,
                            "owner": uuid.uuid4(),
                            "token": token,
                        },
                    )
                migration.upgrade()
                repaired = c.scalar(text("SELECT etag FROM tenants WHERE id=:id"), {"id": ids[0]})
                assert len(repaired) == 64
                assert (
                    c.scalar(text("SELECT etag FROM tenants WHERE id=:id"), {"id": ids[1]})
                    == "existing-token"
                )
                migration.downgrade()
                assert (
                    c.scalar(text("SELECT etag FROM tenants WHERE id=:id"), {"id": ids[0]})
                    == repaired
                )
        finally:
            transaction.rollback()
    engine.dispose()


@pytest.mark.asyncio
async def test_orm_insert_reads_server_token_and_noop_keeps_it(db_engine, monkeypatch):
    emit = AsyncMock(return_value=uuid.uuid4())
    monkeypatch.setattr(tenancy, "emit_action_event", emit)
    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        tenant = Tenant(
            id=uuid.uuid4(),
            slug="test-" + uuid.uuid4().hex,
            kind=TenantKind.personal,
            display_name="Synthetic",
            owner_user_id=uuid.uuid4(),
            qdrant_namespace="test",
            garage_bucket_prefix="test",
        )
        db.add(tenant)
        await db.flush()
        assert len(tenant.etag) == 64
        before = tenant.etag
        result = await tenancy.update_tenant_settings(
            context(db, tenant.id),
            tenancy.UpdateTenantSettingsInput(
                tenant_id=tenant.id, etag=before, display_name="Synthetic"
            ),
        )
        assert result.etag == before and result.changed_fields == []
        emit.assert_not_awaited()
        await db.rollback()


@pytest.mark.asyncio
async def test_two_cached_writers_serialize_and_aba_never_reuses_token(db_engine, monkeypatch):
    emit = AsyncMock(return_value=uuid.uuid4())
    monkeypatch.setattr(tenancy, "emit_action_event", emit)
    tid = uuid.uuid4()
    async with AsyncSession(db_engine, expire_on_commit=False) as seed:
        tenant = Tenant(
            id=tid,
            slug="test-" + tid.hex,
            kind=TenantKind.personal,
            display_name="Original",
            owner_user_id=uuid.uuid4(),
            qdrant_namespace="test",
            garage_bucket_prefix="test",
        )
        seed.add(tenant)
        await seed.commit()
        original = tenant.etag
    async with (
        AsyncSession(db_engine, expire_on_commit=False) as first,
        AsyncSession(db_engine, expire_on_commit=False) as second,
    ):
        # Both sessions have an identity cached BEFORE the winner commits.
        assert (await first.get(Tenant, tid)).etag == original
        assert (await second.get(Tenant, tid)).etag == original
        result = await tenancy.update_tenant_settings(
            context(first, tid),
            tenancy.UpdateTenantSettingsInput(tenant_id=tid, etag=original, display_name="Winner"),
        )
        assert result.etag != original
        assert emit.call_args.kwargs["payload_after"]["etag"] == result.etag
        loser = asyncio.create_task(
            tenancy.update_tenant_settings(
                context(second, tid),
                tenancy.UpdateTenantSettingsInput(
                    tenant_id=tid, etag=original, display_name="Loser"
                ),
            )
        )
        await asyncio.sleep(0.05)
        assert not loser.done(), "second writer must wait for transaction lock"
        await first.commit()
        with pytest.raises(ToolError, match="stale tenant etag"):
            await asyncio.wait_for(loser, timeout=3)
        await second.rollback()
        restored = await tenancy.update_tenant_settings(
            context(first, tid),
            tenancy.UpdateTenantSettingsInput(
                tenant_id=tid, etag=result.etag, display_name="Original"
            ),
        )
        assert restored.etag not in (original, result.etag), "ABA must not reuse old version"
        await first.commit()
        row = await second.scalar(select(Tenant).where(Tenant.id == tid))
        assert row.display_name == "Original" and row.etag == restored.etag
        await second.rollback()


def test_empty_token_remains_rejected():
    with pytest.raises(ValueError):
        tenancy.UpdateTenantSettingsInput(tenant_id=uuid.uuid4(), etag="", display_name="Bad")
