"""Nextcloud CardDAV importer."""

from __future__ import annotations

import os

import httpx

from contact_ops.core.config import get_settings
from contact_ops.importers.base import CanonicalImportRecord, Importer, SourceKind
from contact_ops.importers.vcard import record_from_vcard_text
from contact_ops.security.ssrf import validate_outbound_url

DEFAULT_NEXTCLOUD_URL = "https://cloud.magicunicorn.dev/remote.php/dav/addressbooks/users/aaron/contacts/"


class NextcloudCardDAVImporter(Importer):
    def __init__(
        self,
        *,
        url: str | None = None,
        batch_size: int = 50,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        configured_url = url or os.environ.get("NEXTCLOUD_CARDDAV_URL") or DEFAULT_NEXTCLOUD_URL
        self.url = configured_url.rstrip("/") + "/"
        self.username = username or os.environ.get("NEXTCLOUD_CARDDAV_USER")
        self.password = password or os.environ.get("NEXTCLOUD_CARDDAV_PASSWORD")
        super().__init__(source_uri=self.url, batch_size=batch_size)

    @property
    def source_kind(self) -> SourceKind:
        return "nextcloud"

    async def records(self) -> list[CanonicalImportRecord]:
        if not self.username or not self.password:
            raise RuntimeError("NEXTCLOUD_CARDDAV_USER and NEXTCLOUD_CARDDAV_PASSWORD are required")
        # SSRF guard on the user-supplied CardDAV URL. Dormant/shadow by default
        # (returns the url + logs); raises SSRFBlocked only once enforced.
        await validate_outbound_url(self.url, get_settings())
        async with httpx.AsyncClient(auth=(self.username, self.password), timeout=30) as client:
            hrefs = await _list_vcard_hrefs(client, self.url)
            records: list[CanonicalImportRecord] = []
            for batch in _chunks(hrefs, self.batch_size):
                for href, body in await _multiget(client, self.url, batch):
                    records.append(record_from_vcard_text(body, source_record_id=href))
            return records


async def _list_vcard_hrefs(client: httpx.AsyncClient, url: str) -> list[str]:
    body = """<?xml version="1.0" encoding="utf-8" ?>
<d:propfind xmlns:d="DAV:"><d:prop><d:getcontenttype /></d:prop></d:propfind>"""
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


def _extract_hrefs(xml: str) -> list[str]:
    import xml.etree.ElementTree as ElementTree

    root = ElementTree.fromstring(xml)  # noqa: S314
    hrefs = [item.text or "" for item in root.findall(".//{DAV:}href")]
    return [href for href in hrefs if href.lower().endswith(".vcf")]


def _extract_address_data(xml: str) -> list[tuple[str, str]]:
    import xml.etree.ElementTree as ElementTree

    root = ElementTree.fromstring(xml)  # noqa: S314
    rows: list[tuple[str, str]] = []
    for response in root.findall(".//{DAV:}response"):
        href = response.findtext("{DAV:}href") or ""
        data = response.findtext(".//{urn:ietf:params:xml:ns:carddav}address-data")
        if href and data:
            rows.append((href, data))
    return rows


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]
