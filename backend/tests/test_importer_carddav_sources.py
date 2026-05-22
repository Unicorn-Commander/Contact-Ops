# ruff: noqa: I001

import httpx
import pytest

from contact_ops.importers.icloud_carddav import ICloudCardDAVImporter
from contact_ops.importers.nextcloud_carddav import NextcloudCardDAVImporter


VCARD = """BEGIN:VCARD
VERSION:4.0
UID:remote-1
FN:Remote Person
N:Person;Remote;;;
EMAIL:REMOVED
END:VCARD
"""


@pytest.mark.asyncio
async def test_nextcloud_carddav_fetches_vcards(monkeypatch) -> None:
    importer = NextcloudCardDAVImporter(
        url="https://cloud.example/contacts/",
        username="u",
        password="test-password",  # noqa: S106
    )
    propfind = _multistatus("<d:href>/contacts/remote.vcf</d:href>")
    report = _address_data("/contacts/remote.vcf")
    monkeypatch.setattr(httpx, "AsyncClient", _client({"PROPFIND": propfind, "REPORT": report}))
    records = await importer.records()
    assert records[0].display_name == "Remote Person"


@pytest.mark.asyncio
async def test_icloud_carddav_fetches_vcards_after_discovery(monkeypatch) -> None:
    importer = ICloudCardDAVImporter(
        url="https://contacts.example/",
        apple_id="a",
        app_password="test-password",  # noqa: S106
    )
    discovery = _multistatus("<d:href>/addressbooks/user/default/</d:href>")
    listing = _multistatus("<d:href>/addressbooks/user/default/remote.vcf</d:href>")
    report = _address_data("/addressbooks/user/default/remote.vcf")
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        _client({"PROPFIND": [discovery, listing], "REPORT": report}),
    )
    records = await importer.records()
    assert records[0].emails[0].address == "REMOVED"


def _multistatus(inner: str) -> str:
    return f'<d:multistatus xmlns:d="DAV:"><d:response>{inner}</d:response></d:multistatus>'


def _address_data(href: str) -> str:
    return (
        '<d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>"
        f"<card:address-data>{VCARD}</card:address-data>"
        "</d:prop></d:propstat></d:response></d:multistatus>"
    )


class _FakeAsyncClient:
    def __init__(self, responses: dict[str, str | list[str]], **_: object) -> None:
        self.responses = responses

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def request(self, method: str, *args: object, **__: object) -> httpx.Response:
        response = self.responses[method]
        if isinstance(response, list):
            text = response.pop(0)
        else:
            text = response
        url = str(args[0]) if args else "https://example.invalid/"
        return httpx.Response(207, text=text, request=httpx.Request(method, url))


def _client(responses: dict[str, str | list[str]]):
    def factory(**kwargs: object) -> _FakeAsyncClient:
        return _FakeAsyncClient(responses, **kwargs)

    return factory
