"""iCloud CardDAV importer."""

from __future__ import annotations

import os

import httpx

from contact_ops.core.config import get_settings
from contact_ops.importers.base import CanonicalImportRecord, Importer, SourceKind
from contact_ops.importers.nextcloud_carddav import _chunks, _extract_address_data, _extract_hrefs
from contact_ops.importers.vcard import record_from_vcard_text
from contact_ops.security.ssrf import validate_outbound_url

DEFAULT_ICLOUD_URL = "https://contacts.icloud.com/"
# RFC 6764 service discovery — Apple returns 404 on PROPFIND to the bare root
# but 301s from .well-known/carddav to the user's shard (p0X-contacts.icloud.com).
DEFAULT_ICLOUD_DISCOVERY_URL = "https://contacts.icloud.com/.well-known/carddav"


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
        # SSRF guard on the (overridable) iCloud URL. Dormant/shadow by default.
        await validate_outbound_url(self.url, get_settings())
        # follow_redirects=True is required: iCloud CardDAV discovery starts at
        # contacts.icloud.com and 301s (even on PROPFIND) to a shard URL before
        # serving the address-book response. Without follow_redirects=True,
        # httpx returns the 301 directly and PROPFIND never reaches the shard.
        async with httpx.AsyncClient(
            auth=(self.apple_id, self.app_password),
            timeout=30,
            follow_redirects=True,
        ) as client:
            addressbooks = await _discover_addressbooks(client, DEFAULT_ICLOUD_DISCOVERY_URL)
            records: list[CanonicalImportRecord] = []
            for addressbook_url in addressbooks:
                hrefs = await _list_vcard_hrefs(client, addressbook_url)
                for batch in _chunks(hrefs, self.batch_size):
                    for href, body in await _multiget(client, addressbook_url, batch):
                        records.append(record_from_vcard_text(body, source_record_id=href))
            return records


async def _discover_addressbooks(client: httpx.AsyncClient, root_url: str) -> list[str]:
    """Apple's iCloud CardDAV discovery is a 3-step PROPFIND chain.

    1. PROPFIND root for `current-user-principal` → principal URL
    2. PROPFIND principal for `addressbook-home-set` → home URL
    3. PROPFIND home with Depth:1 → individual address-book collections

    The previous one-shot PROPFIND for both properties at the root works for
    some servers (Nextcloud, Radicale) but iCloud returns the principal alone
    at the root, so we have to do the full chain. Logging at each step so
    failures surface in the connector_runs error message.
    """
    import logging
    log = logging.getLogger(__name__)

    # Step 1 — current-user-principal
    body1 = """<?xml version="1.0" encoding="utf-8" ?>
<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal /></d:prop></d:propfind>"""
    r1 = await client.request("PROPFIND", root_url, content=body1, headers={"Depth": "0"})
    log.warning(
        "icloud_discovery step1 url=%s status=%d body=%s",
        str(r1.request.url), r1.status_code, repr(r1.text[:600])
    )
    r1.raise_for_status()
    principal_href = _extract_principal(r1.text)
    if not principal_href:
        log.warning("icloud_discovery step1 NO principal found")
        return [root_url]
    principal_url = _absolute(str(r1.request.url), principal_href)

    # Step 2 — addressbook-home-set
    body2 = """<?xml version="1.0" encoding="utf-8" ?>
<d:propfind xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
  <d:prop><card:addressbook-home-set /></d:prop>
</d:propfind>"""
    r2 = await client.request("PROPFIND", principal_url, content=body2, headers={"Depth": "0"})
    log.warning(
        "icloud_discovery step2 url=%s status=%d body=%s",
        str(r2.request.url), r2.status_code, repr(r2.text[:600])
    )
    r2.raise_for_status()
    home_href = _extract_addressbook_home(r2.text)
    if not home_href:
        log.warning("icloud_discovery step2 NO addressbook-home-set found, falling back to principal_url")
        return [principal_url]
    home_url = _absolute(str(r2.request.url), home_href)

    # Step 3 — enumerate address books inside the home
    body3 = """<?xml version="1.0" encoding="utf-8" ?>
<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype /><d:displayname /></d:prop></d:propfind>"""
    r3 = await client.request("PROPFIND", home_url, content=body3, headers={"Depth": "1"})
    log.warning(
        "icloud_discovery step3 url=%s status=%d body=%s",
        str(r3.request.url), r3.status_code, repr(r3.text[:800])
    )
    r3.raise_for_status()
    addressbook_hrefs = _extract_addressbook_collections(r3.text)
    if not addressbook_hrefs:
        log.warning("icloud_discovery step3 NO addressbook collections found, returning home_url alone")
        return [home_url]
    addressbook_urls = [_absolute(str(r3.request.url), h) for h in addressbook_hrefs]
    log.warning("icloud_discovery DONE — %d addressbook(s): %s", len(addressbook_urls), addressbook_urls)
    return addressbook_urls


def _extract_principal(xml_text: str) -> str | None:
    """Return the first <current-user-principal><href>…</href></…> value."""
    import re
    m = re.search(
        r"<[^>]*current-user-principal[^>]*>.*?<[^>]*href[^>]*>([^<]+)</",
        xml_text, re.DOTALL | re.IGNORECASE,
    )
    return m.group(1).strip() if m else None


def _extract_addressbook_home(xml_text: str) -> str | None:
    """Return the first <addressbook-home-set><href>…</href></…> value."""
    import re
    m = re.search(
        r"<[^>]*addressbook-home-set[^>]*>.*?<[^>]*href[^>]*>([^<]+)</",
        xml_text, re.DOTALL | re.IGNORECASE,
    )
    return m.group(1).strip() if m else None


def _extract_addressbook_collections(xml_text: str) -> list[str]:
    """Return href values for <response> blocks whose resourcetype contains <addressbook/>."""
    import re
    hrefs: list[str] = []
    for response_block in re.finditer(
        r"<[^>]*response[^>]*>(.*?)</[^>]*response[^>]*>",
        xml_text, re.DOTALL | re.IGNORECASE,
    ):
        block = response_block.group(1)
        if not re.search(r"<[^>]*addressbook[^>]*/?>", block, re.IGNORECASE):
            continue
        href_match = re.search(r"<[^>]*href[^>]*>([^<]+)</", block, re.IGNORECASE)
        if href_match:
            hrefs.append(href_match.group(1).strip())
    return hrefs


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
