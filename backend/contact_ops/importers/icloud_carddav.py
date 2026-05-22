"""iCloud CardDAV importer."""

from __future__ import annotations

import os

import httpx

from contact_ops.importers.base import CanonicalImportRecord, Importer, SourceKind
from contact_ops.importers.nextcloud_carddav import _chunks, _extract_address_data, _extract_hrefs
from contact_ops.importers.vcard import record_from_vcard_text

DEFAULT_ICLOUD_URL = "https://contacts.icloud.com/"


class ICloudCardDAVImporter(Importer):
    def __init__(
        self,
        *,
        url: str | None = None,
        batch_size: int = 50,
        apple_id: str | None = None,
        app_password: str | None = None,
    ) -> None:
        self.url = (url or DEFAULT_ICLOUD_URL).rstrip("/") + "/"
        self.apple_id = apple_id or os.environ.get("ICLOUD_APPLE_ID")
        self.app_password = app_password or os.environ.get("ICLOUD_APP_PASSWORD")
        super().__init__(source_uri=self.url, batch_size=batch_size)

    @property
    def source_kind(self) -> SourceKind:
        return "icloud"

    async def records(self) -> list[CanonicalImportRecord]:
        if not self.apple_id or not self.app_password:
            raise RuntimeError("ICLOUD_APPLE_ID and ICLOUD_APP_PASSWORD are required")
        async with httpx.AsyncClient(auth=(self.apple_id, self.app_password), timeout=30) as client:
            addressbooks = await _discover_addressbooks(client, self.url)
            records: list[CanonicalImportRecord] = []
            for addressbook_url in addressbooks:
                hrefs = await _list_vcard_hrefs(client, addressbook_url)
                for batch in _chunks(hrefs, self.batch_size):
                    for href, body in await _multiget(client, addressbook_url, batch):
                        records.append(record_from_vcard_text(body, source_record_id=href))
            return records


async def _discover_addressbooks(client: httpx.AsyncClient, root_url: str) -> list[str]:
    body = """<?xml version="1.0" encoding="utf-8" ?>
<d:propfind xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
  <d:prop><d:current-user-principal /><card:addressbook-home-set /></d:prop>
</d:propfind>"""
    response = await client.request("PROPFIND", root_url, content=body, headers={"Depth": "0"})
    response.raise_for_status()
    hrefs = _extract_hrefs_any(response.text)
    candidates = [
        href for href in hrefs if "addressbook" in href.lower() or "contacts" in href.lower()
    ]
    if candidates:
        return [_absolute(root_url, href) for href in candidates]
    return [root_url]


async def _list_vcard_hrefs(client: httpx.AsyncClient, url: str) -> list[str]:
    body = """<?xml version="1.0" encoding="utf-8" ?>
<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype /></d:prop></d:propfind>"""
    response = await client.request("PROPFIND", url, content=body, headers={"Depth": "1"})
    response.raise_for_status()
    return _extract_hrefs(response.text)


async def _multiget(
    client: httpx.AsyncClient, base_url: str, hrefs: list[str]
) -> list[tuple[str, str]]:
    href_xml = "".join(f"<d:href>{href}</d:href>" for href in hrefs)
    body = f"""<?xml version="1.0" encoding="utf-8" ?>
<card:addressbook-multiget xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
  <d:prop><d:getetag /><card:address-data /></d:prop>{href_xml}
</card:addressbook-multiget>"""
    response = await client.request("REPORT", base_url, content=body, headers={"Depth": "1"})
    response.raise_for_status()
    return _extract_address_data(response.text)


def _extract_hrefs_any(xml: str) -> list[str]:
    import xml.etree.ElementTree as ElementTree

    root = ElementTree.fromstring(xml)  # noqa: S314
    return [item.text or "" for item in root.findall(".//{DAV:}href") if item.text]


def _absolute(root_url: str, href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return root_url.rstrip("/") + "/" + href.lstrip("/")
