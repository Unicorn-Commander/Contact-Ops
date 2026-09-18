"""HTTP boundary regressions using real routes/tools and an isolated fake data session."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from contact_ops.api import people
from contact_ops.models import Email, Person, PersonOrgRole, Phone, PostalAddress

from .test_legacy_etag_guard import _address, _email, _employment, _FakeDb, _person, _phone
from .test_people_api_entitlement import _install_fakes, _make_request, _settings

CASES = [
    ("emails", Email, _email),
    ("phones", Phone, _phone),
    ("addresses", PostalAddress, _address),
    ("employment", PersonOrgRole, _employment),
]
SCOPES = "email:write phone:write address:write org:write person:write"


@pytest.mark.asyncio
@pytest.mark.parametrize("path,model,factory", CASES)
@pytest.mark.parametrize(
    "scenario,expected", [("stale", 409), ("wrong-person", 404), ("client", 403), ("no-scope", 403)]
)
async def test_patch_http_boundary(monkeypatch, path, model, factory, scenario, expected):
    person, row = _person(), factory()
    row.person_id = person.id

    class Db(_FakeDb):
        async def get(self, *args):
            if scenario in {"client", "no-scope"}:
                raise AssertionError("unauthorized caller reached resource lookup")
            return await super().get(*args)

    _install_fakes(monkeypatch, db=Db({model: row, Person: person}), settings=_settings())
    state = _make_request(
        role="CLIENT" if scenario == "client" else "STAFF",
        scopes="" if scenario == "no-scope" else SCOPES,
    ).state
    app = FastAPI()

    @app.middleware("http")
    async def synthetic_identity(request, call_next):
        request.state.jwt_claims = state.jwt_claims
        return await call_next(request)

    app.include_router(people.router)
    person_id = uuid.uuid4() if scenario == "wrong-person" else person.id
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            f"/api/people/{person_id}/{path}/{row.id}", json={"etag": "stale"}
        )
    assert response.status_code == expected, response.text
    if scenario == "stale":
        assert "stale" in response.json()["detail"]
