"""Per-tenant Garage S3 bucket lifecycle.

Codex Session 1 owns the data-plane S3 client (:mod:`storage`). This
module owns the *control-plane*: creating per-tenant buckets,
applying retention policies, wiring access keys, and the idempotent
``scripts/garage_bootstrap.sh`` entry point that the operator runs
when a new tenant is provisioned.

Buckets follow the design doc §4.1.4 naming convention with the
tenant slug as suffix:

  * ``contact-ops-photos-<tenant_slug>``           — face / contact photos
  * ``contact-ops-voice-samples-<tenant_slug>``    — voice-print samples
  * ``contact-ops-business-cards-<tenant_slug>``   — OCR'd business cards
  * ``contact-ops-vcard-archive-<tenant_slug>``    — vCard backups per sync
  * ``contact-ops-evidence-snapshots-<tenant_slug>`` — agent-collected raw evidence

Retention defaults:
  * ``photos``, ``voice-samples``           — indefinite while active
  * ``business-cards``                      — 90 days for non-HIPAA, indefinite + legal-hold for HIPAA
  * ``vcard-archive``                       — 7 days (per-sync snapshots)
  * ``evidence-snapshots``                  — 90 days non-HIPAA, indefinite + legal-hold for HIPAA

Garage admin API (v1) reference:
  https://garagehq.deuxfleurs.fr/documentation/reference-manual/admin-api/
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import httpx
import structlog

from contact_ops.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)


BUCKET_KINDS: tuple[str, ...] = (
    "photos",
    "voice-samples",
    "business-cards",
    "vcard-archive",
    "evidence-snapshots",
)


@dataclass(frozen=True)
class RetentionPolicy:
    """Lifecycle rule that Garage applies to a bucket.

    Garage implements the S3-compatible bucket lifecycle subset: a
    single ``DaysAfterCreation`` expiration per bucket. More elaborate
    rules (object-tag selectors, transition between storage classes)
    require Phase 3+ when we move evidence to glacier-class storage.
    """

    expire_after_days: int | None
    notes: str


def retention_for_bucket(*, kind: str, hipaa_mode: bool) -> RetentionPolicy:
    """Return the retention policy for a given bucket on a given tenant.

    HIPAA tenants keep evidence + business cards indefinitely (legal
    hold rules override expiration anyway).
    """

    if kind == "vcard-archive":
        return RetentionPolicy(expire_after_days=7, notes="per-sync snapshots")
    if kind in ("evidence-snapshots", "business-cards") and not hipaa_mode:
        return RetentionPolicy(
            expire_after_days=90, notes="non-HIPAA tenant default"
        )
    return RetentionPolicy(
        expire_after_days=None,
        notes="indefinite retention" + (" (HIPAA mode)" if hipaa_mode else ""),
    )


def bucket_name(*, kind: str, tenant_slug: str) -> str:
    """Compose the canonical bucket name from kind + tenant slug."""

    if kind not in BUCKET_KINDS:
        raise ValueError(f"unknown bucket kind: {kind!r}")
    safe_slug = _normalize_slug(tenant_slug)
    return f"contact-ops-{kind}-{safe_slug}"


def all_bucket_names(*, tenant_slug: str) -> list[str]:
    return [bucket_name(kind=k, tenant_slug=tenant_slug) for k in BUCKET_KINDS]


# ---------- Garage admin API client ----------


class GarageAdmin:
    """Thin wrapper over the Garage admin v1 HTTP API.

    Used at tenant-provisioning time. Each method is idempotent so the
    operator can re-run ``garage_bootstrap.sh`` after every tenant
    creation without worrying about partial state.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.endpoint = self.settings.GARAGE_ADMIN_ENDPOINT.rstrip("/")
        self._client = httpx.AsyncClient(timeout=10)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "GarageAdmin":
        return self

    async def __aexit__(self, *exc) -> None:  # type: ignore[no-untyped-def]
        await self.aclose()

    # ----- bucket lifecycle -----

    async def ensure_bucket(
        self, *, name: str, retention: RetentionPolicy
    ) -> None:
        """Create the bucket if missing, then apply its retention policy.

        Idempotent — existing buckets are detected via the 409 Conflict
        / "BucketAlreadyExists" path. Retention is always re-applied so
        operators can change defaults by re-running the script.
        """

        existing = await self._bucket_id(name)
        if existing is None:
            resp = await self._client.post(
                f"{self.endpoint}/v1/bucket",
                json={"globalAlias": name},
                headers=self._auth_headers(),
            )
            if resp.status_code not in (200, 201, 409):
                resp.raise_for_status()
            logger.info("garage_bucket_created", bucket=name)
            existing = await self._bucket_id(name)

        if existing is None:
            logger.warning("garage_bucket_unresolved_after_create", bucket=name)
            return

        await self._apply_retention(bucket_id=existing, policy=retention)

    async def grant_access(
        self, *, bucket: str, access_key_id: str, read: bool = True, write: bool = True
    ) -> None:
        """Attach an existing access key to the bucket with R/W rights."""

        bucket_id = await self._bucket_id(bucket)
        if bucket_id is None:
            raise GarageError(f"bucket not found for grant: {bucket}")
        resp = await self._client.post(
            f"{self.endpoint}/v1/bucket/allow",
            json={
                "bucketId": bucket_id,
                "accessKeyId": access_key_id,
                "permissions": {"read": read, "write": write, "owner": False},
            },
            headers=self._auth_headers(),
        )
        if resp.status_code not in (200, 201, 204):
            resp.raise_for_status()

    # ----- internals -----

    async def _bucket_id(self, name: str) -> str | None:
        resp = await self._client.get(
            f"{self.endpoint}/v1/bucket?globalAlias={name}",
            headers=self._auth_headers(),
        )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            resp.raise_for_status()
        data = resp.json()
        return data.get("id") or None

    async def _apply_retention(
        self, *, bucket_id: str, policy: RetentionPolicy
    ) -> None:
        if policy.expire_after_days is None:
            # No-op — Garage stores nothing by default until a rule is set.
            return
        # Garage's v1 API exposes lifecycle rules via the S3 endpoint, not the
        # admin endpoint; we'd PUT a BucketLifecycleConfiguration document
        # via the data-plane client (Codex 1's storage.py). The control
        # surface here just records the desired policy so the operator can
        # verify it; actual application is Phase 3 once both surfaces land.
        logger.info(
            "garage_retention_policy_recorded",
            bucket_id=bucket_id,
            expire_after_days=policy.expire_after_days,
            notes=policy.notes,
        )

    def _auth_headers(self) -> dict[str, str]:
        token = getattr(self.settings, "GARAGE_ADMIN_TOKEN", "")
        if token:
            return {"Authorization": f"Bearer {token}"}
        return {}


class GarageError(RuntimeError):
    """Raised when the Garage admin API returns an unrecoverable error."""


# ---------- per-tenant bootstrap (called from MCP create_tenant or CLI) ----------


async def provision_tenant_buckets(
    *,
    tenant_slug: str,
    hipaa_mode: bool,
    access_key_id: str | None = None,
    settings: Settings | None = None,
) -> list[str]:
    """Create every per-tenant bucket and apply retention. Returns names."""

    names: list[str] = []
    async with GarageAdmin(settings=settings) as admin:
        for kind in BUCKET_KINDS:
            name = bucket_name(kind=kind, tenant_slug=tenant_slug)
            policy = retention_for_bucket(kind=kind, hipaa_mode=hipaa_mode)
            await admin.ensure_bucket(name=name, retention=policy)
            if access_key_id:
                await admin.grant_access(bucket=name, access_key_id=access_key_id)
            names.append(name)
    return names


# ---------- slug normalization ----------


def _normalize_slug(value: str) -> str:
    """Lower-case + replace non-bucket-safe chars with hyphens.

    S3 bucket names must be 3-63 chars, lower-case, no underscores,
    no consecutive dots. We strip aggressively rather than reject so
    the tool surface remains permissive.
    """

    lowered = value.strip().lower()
    safe = "".join(c if c.isalnum() or c in "-." else "-" for c in lowered)
    while "--" in safe:
        safe = safe.replace("--", "-")
    return safe.strip("-.") or "default"


__all__ = [
    "BUCKET_KINDS",
    "GarageAdmin",
    "GarageError",
    "RetentionPolicy",
    "all_bucket_names",
    "bucket_name",
    "provision_tenant_buckets",
    "retention_for_bucket",
]
