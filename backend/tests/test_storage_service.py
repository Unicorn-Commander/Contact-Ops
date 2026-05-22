"""Storage service unit tests (local-filesystem backend).

The Garage backend is exercised by integration tests against a real Garage,
not by this module. Here we lock down the local fallback that tests and dev
loops depend on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from contact_ops.services.storage import (
    LocalFilesystemBackend,
    StorageService,
)


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    return tmp_path / "storage-root"


@pytest.mark.asyncio
async def test_local_backend_put_and_get(tmp_root: Path) -> None:
    backend = LocalFilesystemBackend(root=tmp_root)
    await backend.put_bytes(
        bucket="bucket-a",
        key="folder/file.bin",
        body=b"hello",
        content_type="application/octet-stream",
    )
    head = await backend.head(bucket="bucket-a", key="folder/file.bin")
    assert head is not None
    assert head["size"] == 5
    assert len(head["etag"]) == 64  # sha256 hex


@pytest.mark.asyncio
async def test_local_backend_head_missing(tmp_root: Path) -> None:
    backend = LocalFilesystemBackend(root=tmp_root)
    assert await backend.head(bucket="nope", key="missing") is None


@pytest.mark.asyncio
async def test_local_backend_rejects_path_escape(tmp_root: Path) -> None:
    backend = LocalFilesystemBackend(root=tmp_root)
    with pytest.raises(ValueError):
        await backend.put_bytes(
            bucket="b", key="../escape", body=b"x", content_type="x"
        )


@pytest.mark.asyncio
async def test_local_backend_presigned_urls_are_file_urls(tmp_root: Path) -> None:
    backend = LocalFilesystemBackend(root=tmp_root)
    put_url = await backend.presigned_put_url(
        bucket="b", key="k", content_type="image/jpeg", expires_seconds=60
    )
    assert put_url.startswith("file://")
    get_url = await backend.presigned_get_url(
        bucket="b", key="k", expires_seconds=60
    )
    assert get_url.startswith("file://")


@pytest.mark.asyncio
async def test_storage_service_bucket_naming(tmp_root: Path) -> None:
    svc = StorageService(backend=LocalFilesystemBackend(root=tmp_root))
    assert svc.photo_bucket("aaron-personal") == "contact-ops-photos-aaron-personal"
    assert svc.voice_bucket("magic-unicorn") == "contact-ops-voice-samples-magic-unicorn"


@pytest.mark.asyncio
async def test_storage_service_delete(tmp_root: Path) -> None:
    backend = LocalFilesystemBackend(root=tmp_root)
    svc = StorageService(backend=backend)
    await svc.put_bytes(
        bucket="b", key="k", body=b"hi", content_type="text/plain"
    )
    assert await svc.head(bucket="b", key="k") is not None
    await svc.delete(bucket="b", key="k")
    assert await svc.head(bucket="b", key="k") is None
