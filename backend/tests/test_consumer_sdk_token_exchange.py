# ruff: noqa: S105,S106
from __future__ import annotations

import json
import uuid
from urllib.parse import parse_qs

import httpx
import pytest

from contact_ops.federation.consumer_sdk.client import (
    TOKEN_EXCHANGE_GRANT,
    ContactOpsConsumerClient,
)


@pytest.mark.asyncio
async def test_token_exchange_is_rfc8693_and_mcp_call_uses_obo_token() -> None:
    calls: list[httpx.Request] = []
    person_id = uuid.UUID("00000000-0000-0000-0000-000000000111")

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/token":
            form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
            assert form["grant_type"] == TOKEN_EXCHANGE_GRANT
            assert form["subject_token"] == "user-jwt"
            assert form["audience"] == "contact-ops-mcp"
            return httpx.Response(200, json={"access_token": "obo-token", "expires_in": 300})
        assert request.headers["authorization"] == "Bearer obo-token"
        payload = json.loads(request.content.decode())
        assert payload["method"] == "tools/call"
        assert payload["params"]["name"] == "get_person"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "person_id": str(person_id),
                    "display_name": "Daniel Tran",
                    "etag": "v1",
                    "emails": [{"address": "REMOVED", "is_primary": True}],
                },
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ContactOpsConsumerClient(
        token_endpoint="https://auth.example.test/token",
        client_id="listing-ops-contactops-consumer",
        client_secret="client-test-value",
        mcp_url="https://mcp.example.test/json-rpc",
        http=http,
    )

    person = await client.get_person(subject_token="user-jwt", person_id=person_id)

    assert person.person_id == person_id
    assert person.display_name == "Daniel Tran"
    assert person.primary_email == "REMOVED"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_find_person_by_identifier_normalizes_matches() -> None:
    person_id = uuid.UUID("00000000-0000-0000-0000-000000000112")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "obo-token", "expires_in": 300})
        payload = json.loads(request.content.decode())
        assert payload["params"]["arguments"] == {
            "identifiers": [{"namespace": "listing-ops:user", "value": str(person_id)}]
        }
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "matches": [
                        {
                            "person_id": str(person_id),
                            "display_name": "Aaron Stransky",
                            "primary_email": "REMOVED",
                        }
                    ],
                    "ambiguous": False,
                },
            },
        )

    client = ContactOpsConsumerClient(
        token_endpoint="https://auth.example.test/token",
        client_id="listing-ops-contactops-consumer",
        client_secret="client-test-value",
        mcp_url="https://mcp.example.test/json-rpc",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    person = await client.find_person_by_identifier(
        subject_token="user-jwt",
        namespace="listing-ops:user",
        value=str(person_id),
    )

    assert person is not None
    assert person.person_id == person_id
