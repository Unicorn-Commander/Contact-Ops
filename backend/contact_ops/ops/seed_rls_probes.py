"""Create or find the two strict RLS probe tenants.

Operator command (admin / BYPASSRLS URL, after the schema exists)::

    python -m contact_ops.ops.seed_rls_probes

Stdout is only the comma-separated tenant ids and the env line to set.
Running it twice does not change existing rows. Slugs are ``rls-probe-a`` and
``rls-probe-b``. A previous self-host install that used ``selfhost-probe-a`` /
``selfhost-probe-b`` is found and left in place.

Each new tenant is ``personal``, ``isolation_mode=strict``, locked, owned by
``SEED_OWNER_USER_ID`` (default ``00000000-0000-0000-0000-000000000001``), with
``qdrant_namespace`` ``contact_ops__<slug>`` and ``garage_bucket_prefix``
``contact-ops-<slug>``. A tenant with no person gets one placeholder. The
placeholder insert sets the tenant GUC (``TENANT_GUC_NAME``, default
``app.tenant_id``) for the duration of that statement's transaction.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from contact_ops.ops.rls_probe_preflight import asyncpg_dsn

DEFAULT_OWNER_USER_ID = "00000000-0000-0000-0000-000000000001"
_GUC_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_SCHEMA_GAP_SQLSTATES = frozenset({"42P01", "42703"})


class SeedError(RuntimeError):
    """Operator-facing failure. The message must not contain a DSN or secret."""


@dataclass(frozen=True)
class ProbeSpec:
    slug: str
    legacy_slugs: tuple[str, ...]
    preferred_id: str
    display_name: str
    person_display_name: str


PROBE_SPECS: tuple[ProbeSpec, ...] = (
    ProbeSpec(
        slug="rls-probe-a",
        legacy_slugs=("selfhost-probe-a",),
        preferred_id="11111111-1111-4111-8111-111111111111",
        display_name="RLS probe A",
        person_display_name="RLS probe A person",
    ),
    ProbeSpec(
        slug="rls-probe-b",
        legacy_slugs=("selfhost-probe-b",),
        preferred_id="22222222-2222-4222-8222-222222222222",
        display_name="RLS probe B",
        person_display_name="RLS probe B person",
    ),
)


@dataclass(frozen=True)
class FoundTenant:
    tenant_id: str
    slug: str
    isolation_mode: str


@dataclass(frozen=True)
class NewTenant:
    tenant_id: str
    slug: str
    display_name: str
    owner_user_id: str
    qdrant_namespace: str
    garage_bucket_prefix: str
    graph_name: str


class ProbeDirectory(Protocol):
    async def role_is_privileged(self) -> bool: ...

    async def tenant_by_slug(self, slug: str) -> FoundTenant | None: ...

    async def tenant_by_id(self, tenant_id: str) -> FoundTenant | None: ...

    async def insert_tenant(self, tenant: NewTenant) -> str | None: ...

    async def person_count(self, tenant_id: str) -> int: ...

    async def insert_person(
        self,
        *,
        tenant_id: str,
        display_name: str,
        vcard_uid: str,
        guc_name: str,
    ) -> None: ...


def format_seed_output(tenant_ids: list[str]) -> str:
    if len(tenant_ids) != 2 or len(set(tenant_ids)) != 2:
        raise SeedError("internal: seed did not resolve two distinct probe tenants")
    canonical: list[str] = []
    for tenant_id in tenant_ids:
        try:
            canonical.append(str(UUID(tenant_id)))
        except ValueError as exc:
            raise SeedError("internal: probe id is not a UUID") from exc
    joined = ",".join(canonical)
    return f"{joined}\nRLS_SELFTEST_TENANT_IDS={joined}\n"


def resolve_migration_dsn() -> str:
    raw = os.environ.get("MIGRATION_DATABASE_URL", "").strip()
    if not raw:
        raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        raise SeedError(
            "Set MIGRATION_DATABASE_URL to the admin/BYPASSRLS database URL"
        )
    return raw


def raise_for_schema_gap(exc: BaseException) -> None:
    if getattr(exc, "sqlstate", None) in _SCHEMA_GAP_SQLSTATES:
        raise SeedError(
            "schema is missing tenants, persons, or isolation_mode; "
            "run alembic upgrade head first"
        ) from None


def _safe_error_text(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    if "://" in text or "@" in text:
        return type(exc).__name__
    return f"{type(exc).__name__}: {text}"


def _require_strict(tenant: FoundTenant) -> None:
    if tenant.isolation_mode != "strict":
        raise SeedError(
            f"tenant slug {tenant.slug} exists but isolation_mode is "
            f"{tenant.isolation_mode}; refusing to change it"
        )


async def _find_existing(directory: ProbeDirectory, spec: ProbeSpec) -> FoundTenant | None:
    found = await directory.tenant_by_slug(spec.slug)
    if found is not None:
        return found
    for legacy in spec.legacy_slugs:
        found = await directory.tenant_by_slug(legacy)
        if found is not None:
            return found
    return None


async def _insert_or_adopt(
    directory: ProbeDirectory, spec: ProbeSpec, owner_user_id: str
) -> str:
    inserted = await directory.insert_tenant(
        NewTenant(
            tenant_id=spec.preferred_id,
            slug=spec.slug,
            display_name=spec.display_name,
            owner_user_id=owner_user_id,
            qdrant_namespace=f"contact_ops__{spec.slug}",
            garage_bucket_prefix=f"contact-ops-{spec.slug}",
            graph_name=f"contact_ops__{spec.slug.replace('-', '_')}",
        )
    )
    if inserted is not None:
        return inserted

    allowed = {spec.slug, *spec.legacy_slugs}
    adopted = await directory.tenant_by_id(spec.preferred_id)
    if adopted is not None and adopted.slug in allowed:
        _require_strict(adopted)
        return adopted.tenant_id

    raced = await _find_existing(directory, spec)
    if raced is not None:
        _require_strict(raced)
        return raced.tenant_id
    raise SeedError(
        f"could not create probe tenant {spec.slug}; "
        "its preferred id or slug is already used"
    )


async def seed_probes(
    directory: ProbeDirectory,
    *,
    owner_user_id: str,
    guc_name: str,
) -> list[str]:
    """Find or create both probes. Existing strict tenants are not updated."""
    if not _GUC_NAME.fullmatch(guc_name):
        raise SeedError("TENANT_GUC_NAME is not a valid GUC name")
    try:
        owner = str(UUID(owner_user_id))
    except ValueError as exc:
        raise SeedError("SEED_OWNER_USER_ID must be a UUID") from exc
    if not await directory.role_is_privileged():
        raise SeedError(
            "seed_rls_probes requires MIGRATION_DATABASE_URL as a "
            "superuser or BYPASSRLS role"
        )

    # Reject a non-strict slug before any insert, so one bad row writes nothing.
    existing: list[FoundTenant | None] = []
    for spec in PROBE_SPECS:
        found = await _find_existing(directory, spec)
        if found is not None:
            _require_strict(found)
        existing.append(found)

    tenant_ids: list[str] = []
    for spec, found in zip(PROBE_SPECS, existing, strict=True):
        tenant_id = (
            found.tenant_id
            if found is not None
            else await _insert_or_adopt(directory, spec, owner)
        )
        tenant_ids.append(tenant_id)
        if await directory.person_count(tenant_id) >= 1:
            continue
        await directory.insert_person(
            tenant_id=tenant_id,
            display_name=spec.person_display_name,
            vcard_uid=f"rls-probe-{tenant_id}",
            guc_name=guc_name,
        )
        if await directory.person_count(tenant_id) < 1:
            raise SeedError(
                f"probe tenant {spec.slug} has no person after the placeholder insert"
            )
    return tenant_ids


_INSERT_TENANT = """
INSERT INTO tenants (
    id, slug, kind, display_name, owner_user_id,
    isolation_mode, isolation_mode_locked,
    qdrant_namespace, garage_bucket_prefix, graph_name
) VALUES (
    $1::uuid, $2, $3::tenant_kind, $4, $5::uuid,
    'strict', true,
    $6, $7, $8
)
ON CONFLICT (id) DO NOTHING
RETURNING id::text
"""


class AsyncpgProbeDirectory:
    """Admin connection.

    ``insert_tenant`` uses a savepoint so a unique clash does not abort the
    seed transaction.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def role_is_privileged(self) -> bool:
        value = await self._conn.fetchval(
            """
            SELECT COALESCE(
                (
                    SELECT rolsuper OR rolbypassrls
                    FROM pg_roles
                    WHERE rolname = current_user
                ),
                false
            )
            """
        )
        return bool(value)

    async def tenant_by_slug(self, slug: str) -> FoundTenant | None:
        row = await self._conn.fetchrow(
            """
            SELECT id::text AS tenant_id, slug, isolation_mode
            FROM tenants
            WHERE slug = $1
            """,
            slug,
        )
        return _found(row)

    async def tenant_by_id(self, tenant_id: str) -> FoundTenant | None:
        row = await self._conn.fetchrow(
            """
            SELECT id::text AS tenant_id, slug, isolation_mode
            FROM tenants
            WHERE id = $1::uuid
            """,
            tenant_id,
        )
        return _found(row)

    async def insert_tenant(self, tenant: NewTenant) -> str | None:
        try:
            async with self._conn.transaction():
                inserted = await self._conn.fetchval(
                    _INSERT_TENANT,
                    tenant.tenant_id,
                    tenant.slug,
                    "personal",
                    tenant.display_name,
                    tenant.owner_user_id,
                    tenant.qdrant_namespace,
                    tenant.garage_bucket_prefix,
                    tenant.graph_name,
                )
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == "23505":
                return None
            raise
        if inserted is None:
            return None
        return str(inserted)

    async def person_count(self, tenant_id: str) -> int:
        value = await self._conn.fetchval(
            """
            SELECT count(*)
            FROM persons
            WHERE canonical_owner_tenant_id = $1::uuid
            """,
            tenant_id,
        )
        return int(value or 0)

    async def insert_person(
        self,
        *,
        tenant_id: str,
        display_name: str,
        vcard_uid: str,
        guc_name: str,
    ) -> None:
        # Local to the seed transaction. The migration role bypasses RLS; the
        # GUC is still set because person-insert triggers and policies read it.
        await self._conn.execute("SELECT set_config($1, $2, true)", guc_name, tenant_id)
        await self._conn.execute(
            """
            INSERT INTO persons (display_name, canonical_owner_tenant_id, vcard_uid)
            SELECT $1, $2::uuid, $3
            WHERE NOT EXISTS (
                SELECT 1 FROM persons
                WHERE canonical_owner_tenant_id = $2::uuid
            )
            ON CONFLICT (vcard_uid) DO NOTHING
            """,
            display_name,
            tenant_id,
            vcard_uid,
        )


def _found(row: Any) -> FoundTenant | None:
    if row is None:
        return None
    return FoundTenant(
        tenant_id=str(row["tenant_id"]),
        slug=str(row["slug"]),
        isolation_mode=str(row["isolation_mode"]),
    )


async def seed_on_connection(
    conn: Any,
    *,
    owner_user_id: str,
    guc_name: str,
) -> list[str]:
    """One transaction. A failure rolls the probe writes back."""
    directory = AsyncpgProbeDirectory(conn)
    async with conn.transaction():
        return await seed_probes(
            directory, owner_user_id=owner_user_id, guc_name=guc_name
        )


async def _run() -> list[str]:
    import asyncpg

    dsn = resolve_migration_dsn()
    owner = os.environ.get("SEED_OWNER_USER_ID", DEFAULT_OWNER_USER_ID).strip()
    guc_name = os.environ.get("TENANT_GUC_NAME", "app.tenant_id").strip() or "app.tenant_id"
    try:
        conn = await asyncpg.connect(asyncpg_dsn(dsn))
        try:
            return await seed_on_connection(
                conn, owner_user_id=owner, guc_name=guc_name
            )
        finally:
            await conn.close()
    except SeedError:
        raise
    except Exception as exc:
        raise_for_schema_gap(exc)
        raise


def main() -> None:
    try:
        tenant_ids = asyncio.run(_run())
    except SeedError as exc:
        sys.stderr.write(f"{exc}\n")
        raise SystemExit(1) from None
    except Exception as exc:
        sys.stderr.write(f"seed_rls_probes failed ({_safe_error_text(exc)})\n")
        raise SystemExit(1) from None
    sys.stdout.write(format_seed_output(tenant_ids))


if __name__ == "__main__":
    main()
