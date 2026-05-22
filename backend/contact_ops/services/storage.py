"""Object storage service for Contact-Ops media (photos, voice samples, etc.).

Two interchangeable backends:

* ``LocalFilesystemBackend`` — writes objects under ``$STORAGE_LOCAL_ROOT``
  (default ``/tmp/contact-ops-storage``) and returns ``file://`` URLs.
  Used by tests and STANDALONE_MODE dev environments. Has no third-party
  dependencies, so the dev loop does not require Garage to be running.

* ``GarageS3Backend`` — talks to a Garage (S3-compatible) endpoint over HTTP
  using a hand-rolled AWS SigV4 signer. Avoids pulling in boto3 / aioboto3 so
  the existing pip-tools lockfile stays unchanged.

Both backends expose the same async API:

    presigned_put_url(bucket, key, content_type, expires) -> str
    presigned_get_url(bucket, key, expires)               -> str
    head(bucket, key)                                     -> dict | None
    delete(bucket, key)                                   -> None
    put_bytes(bucket, key, body, content_type)            -> None  # tests/dev

``put_bytes`` exists so tests and the local-fs backend can simulate the
client-side upload that would otherwise PUT to the presigned URL.

Bucket-name convention follows Codex 2's per-tenant prefix scheme:
``contact-ops-photos-<tenant_slug>`` and
``contact-ops-voice-samples-<tenant_slug>``. Codex 2 owns lifecycle
provisioning; this service only references the names.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import hmac
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlparse

import httpx

from contact_ops.core.config import Settings, get_settings


class StorageBackend(Protocol):
    async def presigned_put_url(
        self, *, bucket: str, key: str, content_type: str, expires_seconds: int
    ) -> str: ...

    async def presigned_get_url(
        self, *, bucket: str, key: str, expires_seconds: int
    ) -> str: ...

    async def head(self, *, bucket: str, key: str) -> dict | None: ...

    async def delete(self, *, bucket: str, key: str) -> None: ...

    async def put_bytes(
        self, *, bucket: str, key: str, body: bytes, content_type: str
    ) -> None: ...


# ---------------------------------------------------------------------------
# Local-filesystem backend (tests, STANDALONE_MODE)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalFilesystemBackend:
    """Local backend that stores objects on disk under a root directory.

    Presigned URLs are ``file://`` URLs pointing at the on-disk path. The
    ``expires_seconds`` argument is accepted but ignored; the URL is valid
    until the file is deleted. This is acceptable because the local backend
    is for tests and developer-only dev mode.
    """

    root: Path

    def _path(self, bucket: str, key: str) -> Path:
        # Defence in depth: refuse keys that try to escape the bucket dir.
        if ".." in Path(key).parts:
            raise ValueError(f"invalid object key: {key!r}")
        return self.root / bucket / key

    async def presigned_put_url(
        self, *, bucket: str, key: str, content_type: str, expires_seconds: int
    ) -> str:
        path = self._path(bucket, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.resolve().as_uri()

    async def presigned_get_url(
        self, *, bucket: str, key: str, expires_seconds: int
    ) -> str:
        return self._path(bucket, key).resolve().as_uri()

    async def head(self, *, bucket: str, key: str) -> dict | None:
        path = self._path(bucket, key)
        if not path.exists():
            return None
        stat = path.stat()
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        return {
            "size": stat.st_size,
            "etag": sha,
            "content_type": "application/octet-stream",
            "last_modified": _dt.datetime.fromtimestamp(
                stat.st_mtime, tz=_dt.UTC
            ),
        }

    async def delete(self, *, bucket: str, key: str) -> None:
        path = self._path(bucket, key)
        if path.exists():
            path.unlink()

    async def put_bytes(
        self, *, bucket: str, key: str, body: bytes, content_type: str
    ) -> None:
        path = self._path(bucket, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)


# ---------------------------------------------------------------------------
# Garage / S3 backend (production)
# ---------------------------------------------------------------------------


_SIGV4_ALGORITHM = "AWS4-HMAC-SHA256"
_UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, datestamp: str, region: str, service: str) -> bytes:
    k_date = _sign(("AWS4" + secret).encode("utf-8"), datestamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    return _sign(k_service, "aws4_request")


@dataclass(frozen=True)
class GarageS3Backend:
    """Async S3-compatible backend that talks to Garage over HTTPS.

    Uses path-style URLs (``<endpoint>/<bucket>/<key>``) because Garage's
    default deployment is single-DNS-name and does not provide per-bucket
    subdomains. This matches the existing ``unicorn-garage`` configuration
    documented in ``feedback_garage_object_storage.md``.
    """

    endpoint: str
    access_key: str
    secret_key: str
    region: str = "us-east-1"
    service: str = "s3"

    def _presign(
        self,
        *,
        method: str,
        bucket: str,
        key: str,
        expires_seconds: int,
        signed_headers: dict[str, str] | None = None,
    ) -> str:
        host = urlparse(self.endpoint).netloc
        now = _dt.datetime.now(tz=_dt.UTC)
        datestamp = now.strftime("%Y%m%d")
        amzdate = now.strftime("%Y%m%dT%H%M%SZ")
        credential_scope = f"{datestamp}/{self.region}/{self.service}/aws4_request"

        headers = dict(signed_headers or {})
        headers.setdefault("host", host)
        signed_header_names = ";".join(sorted(headers))
        canonical_headers = (
            "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
        )

        query = {
            "X-Amz-Algorithm": _SIGV4_ALGORITHM,
            "X-Amz-Credential": f"{self.access_key}/{credential_scope}",
            "X-Amz-Date": amzdate,
            "X-Amz-Expires": str(expires_seconds),
            "X-Amz-SignedHeaders": signed_header_names,
        }
        canonical_query = "&".join(
            f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}"
            for k, v in sorted(query.items())
        )

        canonical_uri = f"/{quote(bucket, safe='')}/{quote(key, safe='/')}"
        canonical_request = "\n".join(
            [
                method.upper(),
                canonical_uri,
                canonical_query,
                canonical_headers,
                signed_header_names,
                _UNSIGNED_PAYLOAD,
            ]
        )

        string_to_sign = "\n".join(
            [
                _SIGV4_ALGORITHM,
                amzdate,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        key_bytes = _signing_key(self.secret_key, datestamp, self.region, self.service)
        signature = hmac.new(
            key_bytes, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        return (
            f"{self.endpoint.rstrip('/')}{canonical_uri}"
            f"?{canonical_query}&X-Amz-Signature={signature}"
        )

    async def presigned_put_url(
        self, *, bucket: str, key: str, content_type: str, expires_seconds: int
    ) -> str:
        return self._presign(
            method="PUT",
            bucket=bucket,
            key=key,
            expires_seconds=expires_seconds,
        )

    async def presigned_get_url(
        self, *, bucket: str, key: str, expires_seconds: int
    ) -> str:
        return self._presign(
            method="GET",
            bucket=bucket,
            key=key,
            expires_seconds=expires_seconds,
        )

    async def head(self, *, bucket: str, key: str) -> dict | None:
        url = self._presign(
            method="HEAD", bucket=bucket, key=key, expires_seconds=60
        )
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.head(url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        last_modified_raw = response.headers.get("last-modified")
        last_modified: _dt.datetime | None
        if last_modified_raw is None:
            last_modified = None
        else:
            try:
                last_modified = _dt.datetime.strptime(
                    last_modified_raw, "%a, %d %b %Y %H:%M:%S %Z"
                ).replace(tzinfo=_dt.UTC)
            except ValueError:
                last_modified = None
        return {
            "size": int(response.headers.get("content-length", 0)),
            "etag": response.headers.get("etag", "").strip('"'),
            "content_type": response.headers.get(
                "content-type", "application/octet-stream"
            ),
            "last_modified": last_modified,
        }

    async def delete(self, *, bucket: str, key: str) -> None:
        url = self._presign(
            method="DELETE", bucket=bucket, key=key, expires_seconds=60
        )
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.delete(url)
        if response.status_code not in (200, 204, 404):
            response.raise_for_status()

    async def put_bytes(
        self, *, bucket: str, key: str, body: bytes, content_type: str
    ) -> None:
        url = self._presign(
            method="PUT", bucket=bucket, key=key, expires_seconds=60
        )
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.put(
                url, content=body, headers={"content-type": content_type}
            )
        response.raise_for_status()


# ---------------------------------------------------------------------------
# Public service facade
# ---------------------------------------------------------------------------


class StorageService:
    """Bucket-aware storage facade.

    Tools always go through this class rather than backends directly so that
    bucket naming, tenant scoping, and backend selection stay in one place.
    """

    PHOTO_BUCKET_TEMPLATE = "contact-ops-photos-{tenant_slug}"
    VOICE_BUCKET_TEMPLATE = "contact-ops-voice-samples-{tenant_slug}"
    DEFAULT_PUT_TTL_SECONDS = 900
    DEFAULT_GET_TTL_SECONDS = 900

    def __init__(self, *, backend: StorageBackend) -> None:
        self._backend = backend

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> StorageService:
        settings = settings or get_settings()
        use_local = (
            settings.STANDALONE_MODE
            or settings.ENV in ("test", "dev")
            or not (settings.GARAGE_ACCESS_KEY and settings.GARAGE_SECRET_KEY)
        )
        if use_local:
            default_root = str(Path(tempfile.gettempdir()) / "contact-ops-storage")
            root = Path(os.environ.get("STORAGE_LOCAL_ROOT", default_root))
            root.mkdir(parents=True, exist_ok=True)
            return cls(backend=LocalFilesystemBackend(root=root))
        return cls(
            backend=GarageS3Backend(
                endpoint=settings.GARAGE_ENDPOINT,
                access_key=settings.GARAGE_ACCESS_KEY,
                secret_key=settings.GARAGE_SECRET_KEY,
            )
        )

    # bucket helpers ---------------------------------------------------

    def photo_bucket(self, tenant_slug: str) -> str:
        return self.PHOTO_BUCKET_TEMPLATE.format(tenant_slug=tenant_slug)

    def voice_bucket(self, tenant_slug: str) -> str:
        return self.VOICE_BUCKET_TEMPLATE.format(tenant_slug=tenant_slug)

    # forwarded operations --------------------------------------------

    async def presigned_put_url(
        self,
        *,
        bucket: str,
        key: str,
        content_type: str,
        expires_seconds: int | None = None,
    ) -> str:
        return await self._backend.presigned_put_url(
            bucket=bucket,
            key=key,
            content_type=content_type,
            expires_seconds=expires_seconds or self.DEFAULT_PUT_TTL_SECONDS,
        )

    async def presigned_get_url(
        self,
        *,
        bucket: str,
        key: str,
        expires_seconds: int | None = None,
    ) -> str:
        return await self._backend.presigned_get_url(
            bucket=bucket,
            key=key,
            expires_seconds=expires_seconds or self.DEFAULT_GET_TTL_SECONDS,
        )

    async def head(self, *, bucket: str, key: str) -> dict | None:
        return await self._backend.head(bucket=bucket, key=key)

    async def delete(self, *, bucket: str, key: str) -> None:
        await self._backend.delete(bucket=bucket, key=key)

    async def put_bytes(
        self, *, bucket: str, key: str, body: bytes, content_type: str
    ) -> None:
        await self._backend.put_bytes(
            bucket=bucket, key=key, body=body, content_type=content_type
        )


_SERVICE_SINGLETON: StorageService | None = None


def get_storage_service() -> StorageService:
    """Return the process-wide StorageService (cached after first call)."""
    global _SERVICE_SINGLETON
    if _SERVICE_SINGLETON is None:
        _SERVICE_SINGLETON = StorageService.from_settings()
    return _SERVICE_SINGLETON


def reset_storage_service() -> None:
    """Clear the cached service (used by tests and config-reload paths)."""
    global _SERVICE_SINGLETON
    _SERVICE_SINGLETON = None


def reset_local_storage_root() -> Path:
    """Wipe and recreate the local-fs root; returns the path. Tests only."""
    default_root = str(Path(tempfile.gettempdir()) / "contact-ops-storage")
    root = Path(os.environ.get("STORAGE_LOCAL_ROOT", default_root))
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    return root


__all__ = [
    "GarageS3Backend",
    "LocalFilesystemBackend",
    "StorageBackend",
    "StorageService",
    "get_storage_service",
    "reset_local_storage_root",
    "reset_storage_service",
]


# Keep asyncio in module-level imports for backwards-compatibility with code
# that may later spawn tasks here; suppress unused-import noise.
_ = asyncio
