"""Tests for the WebDAV/CardDAV XML response builders."""

from __future__ import annotations

from datetime import datetime, timezone

from lxml import etree

from contact_ops.carddav.reports import (
    CARDDAV_NS,
    CR,
    CollectionInfo,
    D,
    DAV_NS,
    NAMESPACES,
    ResourceInfo,
    add_propstat,
    add_response,
    new_multistatus,
    parse_multiget_body,
    parse_propfind_body,
    parse_query_body,
    populate_collection_props,
    populate_resource_props,
    render_multistatus,
    vcard_href,
)


def test_namespaces_constants_match_rfc() -> None:
    assert DAV_NS == "DAV:"
    assert CARDDAV_NS == "urn:ietf:params:xml:ns:carddav"
    assert NAMESPACES["D"] == DAV_NS
    assert NAMESPACES["CR"] == CARDDAV_NS


def test_new_multistatus_uses_correct_namespace() -> None:
    ms = new_multistatus()
    assert ms.tag == D("multistatus")


def test_add_response_and_propstat_compose_correctly() -> None:
    ms = new_multistatus()
    resp = add_response(ms, href="/carddav/aaron/")
    prop = add_propstat(resp, status_code=200, status_phrase="OK")
    etree.SubElement(prop, D("displayname")).text = "Test"

    rendered = render_multistatus(ms).decode("utf-8")
    assert "<D:multistatus" in rendered
    assert "<D:href>/carddav/aaron/</D:href>" in rendered
    assert "HTTP/1.1 200 OK" in rendered
    assert "<D:displayname>Test</D:displayname>" in rendered


def test_populate_collection_props_emits_addressbook_resourcetype() -> None:
    ms = new_multistatus()
    resp = add_response(ms, href="/carddav/aaron/tenant/")
    prop = add_propstat(resp, status_code=200, status_phrase="OK")
    info = CollectionInfo(
        href="/carddav/aaron/tenant/",
        display_name="My Addressbook",
        description="Test addressbook",
        sync_token="data:,sync-abc",
        etag="abc",
        is_addressbook=True,
        principal_href="/carddav/aaron/",
    )
    populate_collection_props(prop, info=info)
    rendered = render_multistatus(ms).decode("utf-8")
    assert "<CR:addressbook" in rendered
    assert "displayname>My Addressbook" in rendered
    assert "supported-address-data" in rendered
    assert "addressbook-home-set" in rendered


def test_populate_resource_props_emits_address_data_when_requested() -> None:
    ms = new_multistatus()
    resp = add_response(ms, href="/carddav/aaron/tenant/x.vcf")
    prop = add_propstat(resp, status_code=200, status_phrase="OK")
    info = ResourceInfo(
        href="/carddav/aaron/tenant/x.vcf",
        etag="abcdef",
        last_modified=datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc),
        content_length=10,
        body=b"BEGIN:VCARD\r\nVERSION:4.0\r\nEND:VCARD\r\n",
    )
    populate_resource_props(prop, info=info, requested={CR("address-data"), D("getetag")})
    rendered = render_multistatus(ms).decode("utf-8")
    assert "address-data" in rendered
    assert "BEGIN:VCARD" in rendered
    assert '"abcdef"' in rendered


def test_parse_propfind_body_allprop_returns_none() -> None:
    body = (
        b'<?xml version="1.0"?>'
        b'<propfind xmlns="DAV:"><allprop/></propfind>'
    )
    assert parse_propfind_body(body) is None


def test_parse_propfind_body_returns_requested_set() -> None:
    body = (
        b'<?xml version="1.0"?>'
        b'<propfind xmlns="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">'
        b'  <prop><displayname/><c:supported-address-data/></prop>'
        b'</propfind>'
    )
    result = parse_propfind_body(body)
    assert result is not None
    assert D("displayname") in result
    assert CR("supported-address-data") in result


def test_parse_propfind_body_returns_none_for_empty() -> None:
    assert parse_propfind_body(b"") is None
    assert parse_propfind_body(b"<not-xml-at-all") is None


def test_parse_multiget_body_returns_hrefs() -> None:
    body = (
        b'<?xml version="1.0"?>'
        b'<c:addressbook-multiget xmlns:c="urn:ietf:params:xml:ns:carddav" xmlns="DAV:">'
        b'  <prop><getetag/></prop>'
        b'  <href>/carddav/aaron/tenant/a.vcf</href>'
        b'  <href>/carddav/aaron/tenant/b.vcf</href>'
        b'</c:addressbook-multiget>'
    )
    requested, hrefs = parse_multiget_body(body)
    assert requested is not None
    assert D("getetag") in requested
    assert hrefs == [
        "/carddav/aaron/tenant/a.vcf",
        "/carddav/aaron/tenant/b.vcf",
    ]


def test_parse_query_body_extracts_text_match() -> None:
    body = (
        b'<?xml version="1.0"?>'
        b'<c:addressbook-query xmlns:c="urn:ietf:params:xml:ns:carddav" xmlns="DAV:">'
        b'  <prop><getetag/></prop>'
        b'  <c:filter test="anyof">'
        b'    <c:prop-filter name="FN">'
        b'      <c:text-match>Aaron</c:text-match>'
        b'    </c:prop-filter>'
        b'  </c:filter>'
        b'</c:addressbook-query>'
    )
    requested, query = parse_query_body(body)
    assert requested is not None
    assert query.text_match == "Aaron"
    assert query.test == "anyof"


def test_vcard_href_composes_safe_path() -> None:
    href = vcard_href(
        base="/carddav",
        user_id="aaron",
        tenant_slug="magic-unicorn",
        vcard_uid="urn:uuid:abc",
    )
    assert href == "/carddav/aaron/magic-unicorn/urn:uuid:abc.vcf"


def test_vcard_href_escapes_slashes() -> None:
    href = vcard_href(
        base="/carddav",
        user_id="aaron",
        tenant_slug="tenant",
        vcard_uid="weird/uid/with/slashes",
    )
    assert "weird%2Fuid%2Fwith%2Fslashes" in href
