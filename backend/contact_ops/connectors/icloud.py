"""iCloud CardDAV connector."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from contact_ops.connectors.base import Connector
from contact_ops.importers.base import CanonicalImportRecord
from contact_ops.importers.icloud_carddav import (
    DEFAULT_ICLOUD_DISCOVERY_URL,
    ICloudCardDAVImporter,
)

if TYPE_CHECKING:
    from contact_ops.mcp.registry import MCPContext


class ICloudConnector(Connector):
    provider = "icloud"

    async def pull(self, ctx: MCPContext, since: object | None) -> list[CanonicalImportRecord]:
        importer = ICloudCardDAVImporter(
            apple_id=str(self.payload["apple_id"]),
            app_password=str(self.payload["app_password"]),
        )
        return await importer.records()


async def validate_icloud_credentials(apple_id: str, app_password: str) -> None:
    # Apple's CardDAV root contacts.icloud.com/ returns 404 to PROPFIND.
    # The .well-known/carddav URL 301s to the user's shard; httpx needs
    # follow_redirects=True to follow non-GET (PROPFIND) redirects.
    async with httpx.AsyncClient(
        auth=(apple_id, app_password),
        timeout=15,
        follow_redirects=True,
    ) as client:
        response = await client.request(
            "PROPFIND", DEFAULT_ICLOUD_DISCOVERY_URL, headers={"Depth": "0"}
        )
    if response.status_code == 401:
        raise ValueError(
            "Apple says credentials invalid — generate a fresh app-specific password "
            "at appleid.apple.com"
        )
    # 207 Multi-Status is the standard CardDAV PROPFIND success; 200 is also
    # accepted by some legacy paths. Anything else after redirects are
    # resolved is an unexpected failure mode — surface the body so future
    # failures are diagnosable.
    if response.status_code not in {200, 207}:
        raise ValueError(
            f"Unexpected iCloud response (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        )
