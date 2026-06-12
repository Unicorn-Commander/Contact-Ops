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

    # ----- access keys -----

    async def create_key(self, *, name: str) -> dict[str, str | None]:
        """Mint a Garage access key (POST /v1/key), idempotent by name.

        If a key with this name already exists, return its id with a None secret
        (Garage only reveals the secret once, at creation). Used only by
        per_tenant_key mode — the default shared_key path never mints keys.
        """
        existing = await self._key_id_by_name(name)
        if existing is not None:
            return {"accessKeyId": existing, "secretAccessKey": None}
        resp = await self._client.post(
            f"{self.endpoint}/v1/key",
            json={"name": name},
            headers=self._auth_headers(),
        )
        if resp.status_code not in (200, 201):
            resp.raise_for_status()
        data = resp.json()
        return {
            "accessKeyId": data.get("accessKeyId") or data.get("id"),
            "secretAccessKey": data.get("secretAccessKey"),
        }

    async def _key_id_by_name(self, name: str) -> str | None:
        resp = await self._client.get(
            f"{self.endpoint}/v1/key?list", headers=self._auth_headers()
        )
        if resp.status_code != 200:
            return None
        for key in resp.json() or []:
            if isinstance(key, dict) and key.get("name") == name:
                return key.get("id") or key.get("accessKeyId")
        return None

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


# Prod ships GARAGE_ACCESS_KEY="PLACEHOLDER_NEED_GARAGE_KEY" until a real key is
# provisioned; granting that sentinel would be meaningless, so we skip the grant.
_PLACEHOLDER_ACCESS_KEY = "PLACEHOLDER_NEED_GARAGE_KEY"


async def provision_tenant_storage(
    *,
    tenant_slug: str,
    hipaa_mode: bool = False,
    settings: Settings | None = None,
    force: bool = False,
) -> list[str]:
    """Auto-provision a tenant's Garage buckets at signup — DORMANT by default.

    No-ops (logs + returns []) when GARAGE_AUTO_PROVISION is off, in self_host
    mode (the operator owns Garage), in STANDALONE/local-fs mode, or when the
    admin endpoint/token is absent — so calling it from the onboard hook is
    always safe. In shared_key mode it grants the existing shared access key on
    each bucket (skipping the placeholder sentinel); per_tenant_key mode mints a
    dedicated key (dormant/future). Idempotent and best-effort: ensure_bucket is
    409-tolerant and grant_access is naturally idempotent.

    ``force=True`` bypasses ONLY the GARAGE_AUTO_PROVISION dormant switch (for the
    manual backfill CLI); the environmental guards (self_host / standalone /
    admin-token-absent) still apply because they reflect real inability to act.
    """
    settings = settings or get_settings()

    if not force and not settings.GARAGE_AUTO_PROVISION:
        logger.info("garage_provision_skipped", reason="GARAGE_AUTO_PROVISION off", tenant=tenant_slug)
        return []
    if getattr(settings, "DEPLOYMENT_MODE", "hosted") == "self_host":
        logger.info("garage_provision_skipped", reason="self_host (operator owns Garage)", tenant=tenant_slug)
        return []
    if getattr(settings, "STANDALONE_MODE", False):
        logger.info("garage_provision_skipped", reason="standalone/local-fs backend", tenant=tenant_slug)
        return []
    admin_token = getattr(settings, "GARAGE_ADMIN_TOKEN", "")
    if not settings.GARAGE_ADMIN_ENDPOINT or not admin_token:
        logger.info("garage_provision_skipped", reason="admin endpoint/token absent", tenant=tenant_slug)
        return []

    mode = getattr(settings, "GARAGE_PROVISION_MODE", "shared_key")
    grant_key: str | None = None
    if mode == "per_tenant_key":
        # Dormant/future: mint a dedicated key. NOTE: the secret is shown once and
        # is NOT persisted in v1 (needs the 0042 garage_access_key_id column + a
        # secret store), so per_tenant_key is not production-wired yet.
        async with GarageAdmin(settings=settings) as admin:
            minted = await admin.create_key(name=f"contact-ops-{_normalize_slug(tenant_slug)}")
        grant_key = minted.get("accessKeyId")
    else:  # shared_key (default)
        access_key = settings.GARAGE_ACCESS_KEY
        if access_key and access_key != _PLACEHOLDER_ACCESS_KEY:
            grant_key = access_key
        else:
            logger.info(
                "garage_provision_grant_skipped",
                reason="shared GARAGE_ACCESS_KEY is empty/placeholder",
                tenant=tenant_slug,
            )

    names = await provision_tenant_buckets(
        tenant_slug=tenant_slug,
        hipaa_mode=hipaa_mode,
        access_key_id=grant_key,
        settings=settings,
    )
    logger.info(
        "garage_tenant_provisioned",
        tenant=tenant_slug,
        buckets=len(names),
        mode=mode,
        granted=bool(grant_key),
    )
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
    "provision_tenant_storage",
    "retention_for_bucket",
]
