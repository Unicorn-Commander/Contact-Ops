"""WebDAV/CardDAV XML response builders.

We build every XML payload with :mod:`lxml.etree` rather than string
concatenation — vCard URIs occasionally contain ``&`` and ``<`` (think
custom labels that survive an iOS Notes paste), and string concat
silently produces invalid XML.

Two distinct response shapes live here:

* PROPFIND responses (a ``DAV:multistatus`` listing of resources +
  matched property sets), used by ``PROPFIND`` and the
  ``addressbook-query`` / ``addressbook-multiget`` REPORTs.

* Property-error responses, used for resources that don't exist or
  whose properties we don't recognize — those still arrive in the
  multistatus under a ``404`` / ``403`` propstat.

Namespace handling: lxml dispatches by Clark notation (``{ns}name``).
We expose two prefixes — ``D:`` for ``DAV:`` and ``CR:`` for
``urn:ietf:params:xml:ns:carddav`` — to keep the wire output readable
when an operator pipes a response through ``xmllint --format``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from lxml import etree

DAV_NS = "DAV:"
CARDDAV_NS = "urn:ietf:params:xml:ns:carddav"
CS_NS = "http://calendarserver.org/ns/"

NAMESPACES: dict[str, str] = {
    "D": DAV_NS,
    "CR": CARDDAV_NS,
    "CS": CS_NS,
}


def D(tag: str) -> str:
    return f"{{{DAV_NS}}}{tag}"


def CR(tag: str) -> str:
    return f"{{{CARDDAV_NS}}}{tag}"


def CS(tag: str) -> str:
    return f"{{{CS_NS}}}{tag}"


# ---------- top-level response shapes ----------


def render_multistatus(root: etree._Element) -> bytes:
    """Serialize a built multistatus root element to wire bytes.

    Includes the XML declaration and the standard pretty-print indent
    so curl/litmus output is readable. UTF-8 enforced.
    """

    return etree.tostring(
        root,
        xml_declaration=True,
        encoding="utf-8",
        pretty_print=True,
        standalone=True,
    )


def new_multistatus() -> etree._Element:
    """Create an empty ``<D:multistatus>`` ready to receive responses."""

    return etree.Element(D("multistatus"), nsmap=NAMESPACES)


def add_response(
    multistatus: etree._Element,
    *,
    href: str,
) -> etree._Element:
    """Append a ``<D:response>`` element and return it for further population."""

    resp = etree.SubElement(multistatus, D("response"))
    etree.SubElement(resp, D("href")).text = href
    return resp


def add_propstat(
    response: etree._Element,
    *,
    status_code: int,
    status_phrase: str,
) -> etree._Element:
    """Append a propstat with the given HTTP-status line; returns ``prop``."""

    propstat = etree.SubElement(response, D("propstat"))
    prop = etree.SubElement(propstat, D("prop"))
    etree.SubElement(propstat, D("status")).text = (
        f"HTTP/1.1 {status_code} {status_phrase}"
    )
    return prop


# ---------- property emitters ----------


@dataclass
class CollectionInfo:
    """Static description of a CardDAV collection for PROPFIND."""

    href: str
    display_name: str
    description: str
    sync_token: str
    etag: str
    max_resource_size: int = 256 * 1024  # 256 KB default
    is_addressbook: bool = True
    principal_href: str | None = None


@dataclass
class ResourceInfo:
    """A single vCard resource referenced in a PROPFIND / REPORT response."""

    href: str
    etag: str
    last_modified: datetime
    content_length: int
    body: bytes | None = None


# Which DAV/CardDAV properties this server can return. Anything outside
# this set is reported with a ``HTTP/1.1 404 Not Found`` propstat per
# RFC 4918 §9.1.
SUPPORTED_COLLECTION_PROPS: frozenset[str] = frozenset(
    {
        D("displayname"),
        D("resourcetype"),
        D("getcontenttype"),
        D("getetag"),
        D("sync-token"),
        D("current-user-principal"),
        D("principal-URL"),
        D("owner"),
        D("supported-report-set"),
        D("supportedlock"),
        CR("supported-address-data"),
        CR("addressbook-description"),
        CR("max-resource-size"),
        CR("addressbook-home-set"),
        CS("getctag"),
    }
)


SUPPORTED_RESOURCE_PROPS: frozenset[str] = frozenset(
    {
        D("getetag"),
        D("getcontenttype"),
        D("getlastmodified"),
        D("resourcetype"),
        D("getcontentlength"),
        CR("address-data"),
    }
)


def populate_collection_props(
    prop: etree._Element,
    *,
    info: CollectionInfo,
    requested: set[str] | None = None,
) -> None:
    """Populate a ``<D:prop>`` element with collection-level properties.

    ``requested`` is the set of Clark-notation property names the client
    asked for. Pass ``None`` to mean "everything we know about" (a
    bare ``<allprop/>``).
    """

    requested = requested or SUPPORTED_COLLECTION_PROPS

    if D("displayname") in requested:
        etree.SubElement(prop, D("displayname")).text = info.display_name

    if D("resourcetype") in requested:
        rt = etree.SubElement(prop, D("resourcetype"))
        etree.SubElement(rt, D("collection"))
        if info.is_addressbook:
            etree.SubElement(rt, CR("addressbook"))

    if D("getcontenttype") in requested:
        etree.SubElement(prop, D("getcontenttype")).text = (
            "text/vcard; charset=utf-8" if info.is_addressbook else "httpd/unix-directory"
        )

    if D("getetag") in requested and info.etag:
        etree.SubElement(prop, D("getetag")).text = f'"{info.etag}"'

    if D("sync-token") in requested and info.sync_token:
        etree.SubElement(prop, D("sync-token")).text = info.sync_token

    if D("current-user-principal") in requested and info.principal_href:
        cup = etree.SubElement(prop, D("current-user-principal"))
        etree.SubElement(cup, D("href")).text = info.principal_href

    if D("principal-URL") in requested and info.principal_href:
        pu = etree.SubElement(prop, D("principal-URL"))
        etree.SubElement(pu, D("href")).text = info.principal_href

    if D("owner") in requested and info.principal_href:
        owner = etree.SubElement(prop, D("owner"))
        etree.SubElement(owner, D("href")).text = info.principal_href

    if D("supported-report-set") in requested:
        srs = etree.SubElement(prop, D("supported-report-set"))
        for report in ("addressbook-query", "addressbook-multiget", "sync-collection"):
            sr = etree.SubElement(srs, D("supported-report"))
            r = etree.SubElement(sr, D("report"))
            if report == "sync-collection":
                etree.SubElement(r, D("sync-collection"))
            else:
                etree.SubElement(r, CR(report))

    if D("supportedlock") in requested:
        etree.SubElement(prop, D("supportedlock"))

    if CR("supported-address-data") in requested and info.is_addressbook:
        sad = etree.SubElement(prop, CR("supported-address-data"))
        for media_type, version in (
            ("text/vcard", "4.0"),
            ("text/vcard", "3.0"),
        ):
            ct = etree.SubElement(sad, CR("address-data-type"))
            ct.set("content-type", media_type)
            ct.set("version", version)

    if CR("addressbook-description") in requested and info.is_addressbook:
        etree.SubElement(prop, CR("addressbook-description")).text = info.description

    if CR("max-resource-size") in requested and info.is_addressbook:
        etree.SubElement(prop, CR("max-resource-size")).text = str(info.max_resource_size)

    if CR("addressbook-home-set") in requested and info.principal_href:
        # The home-set is the parent of any addressbook collection we expose.
        ahs = etree.SubElement(prop, CR("addressbook-home-set"))
        etree.SubElement(ahs, D("href")).text = info.principal_href

    if CS("getctag") in requested and info.etag:
        etree.SubElement(prop, CS("getctag")).text = info.etag


def populate_resource_props(
    prop: etree._Element,
    *,
    info: ResourceInfo,
    requested: set[str] | None = None,
    include_address_data: bool = False,
) -> None:
    """Populate ``<D:prop>`` with vCard-resource-level properties."""

    requested = requested or SUPPORTED_RESOURCE_PROPS

    if D("getetag") in requested and info.etag:
        etree.SubElement(prop, D("getetag")).text = f'"{info.etag}"'

    if D("getcontenttype") in requested:
        etree.SubElement(prop, D("getcontenttype")).text = "text/vcard; charset=utf-8"

    if D("getlastmodified") in requested:
        etree.SubElement(prop, D("getlastmodified")).text = (
            info.last_modified.strftime("%a, %d %b %Y %H:%M:%S GMT")
        )

    if D("resourcetype") in requested:
        # Empty resourcetype element marks this as a plain (non-collection) resource.
        etree.SubElement(prop, D("resourcetype"))

    if D("getcontentlength") in requested:
        etree.SubElement(prop, D("getcontentlength")).text = str(info.content_length)

    if (
        (CR("address-data") in requested or include_address_data)
        and info.body is not None
    ):
        etree.SubElement(prop, CR("address-data")).text = info.body.decode("utf-8")


def render_missing_properties_propstat(
    response: etree._Element,
    *,
    missing: Iterable[str],
) -> None:
    """Add a ``404 Not Found`` propstat for properties we don't know about."""

    missing_list = sorted(set(missing))
    if not missing_list:
        return
    propstat = etree.SubElement(response, D("propstat"))
    prop = etree.SubElement(propstat, D("prop"))
    for name in missing_list:
        etree.SubElement(prop, name)
    etree.SubElement(propstat, D("status")).text = "HTTP/1.1 404 Not Found"


# ---------- REPORT body parsers ----------


def parse_propfind_body(body: bytes) -> set[str] | None:
    """Return the set of requested properties from a PROPFIND body.

    Returns ``None`` for ``<D:allprop/>`` (caller emits the default set)
    or when the body is empty. Otherwise returns the Clark-notation set
    of element names inside ``<D:prop>``.
    """

    if not body:
        return None
    try:
        root = etree.fromstring(body)
    except etree.XMLSyntaxError:
        return None
    if root.tag != D("propfind"):
        return None
    if root.find(D("allprop")) is not None or root.find(D("propname")) is not None:
        return None
    prop = root.find(D("prop"))
    if prop is None:
        return None
    return {child.tag for child in prop if isinstance(child.tag, str)}


def parse_multiget_body(body: bytes) -> tuple[set[str] | None, list[str]]:
    """Parse a ``<CR:addressbook-multiget>`` body.

    Returns ``(requested_props_or_None, list_of_hrefs)``.
    """

    try:
        root = etree.fromstring(body)
    except etree.XMLSyntaxError:
        return None, []
    if root.tag != CR("addressbook-multiget"):
        return None, []

    requested: set[str] | None = None
    prop_el = root.find(D("prop"))
    if prop_el is not None:
        requested = {child.tag for child in prop_el if isinstance(child.tag, str)}

    hrefs = [
        el.text.strip()
        for el in root.findall(D("href"))
        if el.text and el.text.strip()
    ]
    return requested, hrefs


@dataclass
class QueryFilter:
    """Parsed ``<CR:filter>`` element from an ``addressbook-query``.

    Phase 2 supports the property-filter form used by iOS to match a
    ``FN`` or ``EMAIL`` substring. Composite filters are accepted but
    only the first text-match is honored — full filter resolution
    lands in Phase 3 alongside sync-collection.
    """

    text_match: str | None = None
    test: str = "anyof"  # anyof | allof


def parse_query_body(body: bytes) -> tuple[set[str] | None, QueryFilter]:
    """Parse a ``<CR:addressbook-query>`` REPORT body."""

    try:
        root = etree.fromstring(body)
    except etree.XMLSyntaxError:
        return None, QueryFilter()
    if root.tag != CR("addressbook-query"):
        return None, QueryFilter()

    requested: set[str] | None = None
    prop_el = root.find(D("prop"))
    if prop_el is not None:
        requested = {child.tag for child in prop_el if isinstance(child.tag, str)}

    query = QueryFilter()
    filter_el = root.find(CR("filter"))
    if filter_el is not None:
        test = filter_el.get("test", "anyof").lower()
        if test in ("anyof", "allof"):
            query.test = test
        text_match = filter_el.find(f".//{CR('text-match')}")
        if text_match is not None and text_match.text:
            query.text_match = text_match.text.strip()

    return requested, query


# ---------- mapping helpers ----------


def vcard_href(
    *,
    base: str,
    user_id: str,
    tenant_slug: str,
    vcard_uid: str,
) -> str:
    """Build the absolute path for a vCard resource.

    UIDs are emitted verbatim (URI-safe via the canonical ``urn:uuid:``
    form) so iOS Contacts.app's ``addressbook-multiget`` hrefs survive
    round-trip.
    """

    safe_uid = vcard_uid.replace("/", "%2F")
    return f"{base.rstrip('/')}/{user_id}/{tenant_slug}/{safe_uid}.vcf"


def addressbook_href(*, base: str, user_id: str, tenant_slug: str) -> str:
    return f"{base.rstrip('/')}/{user_id}/{tenant_slug}/"


def principal_href(*, base: str, user_id: str) -> str:
    return f"{base.rstrip('/')}/{user_id}/"


def render_properties_with_status(
    response: etree._Element,
    *,
    requested: set[str],
    known: Mapping[str, etree._Element],
) -> None:
    """Split requested properties into 200 (known) and 404 (unknown) groups.

    ``known`` is a map of Clark-name -> already-populated ``<D:prop>``
    child elements that the caller built. The function adds two
    propstat blocks, one with status 200 listing the known props and
    one with status 404 listing the missing ones.
    """

    ok_names = [name for name in requested if name in known]
    missing = [name for name in requested if name not in known]

    if ok_names:
        propstat = etree.SubElement(response, D("propstat"))
        prop = etree.SubElement(propstat, D("prop"))
        for name in ok_names:
            prop.append(known[name])
        etree.SubElement(propstat, D("status")).text = "HTTP/1.1 200 OK"

    if missing:
        render_missing_properties_propstat(response, missing=missing)
