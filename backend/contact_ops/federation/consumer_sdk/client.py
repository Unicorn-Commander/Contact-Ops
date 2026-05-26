"""Async Contact-Ops MCP client for ecosystem consumer apps."""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from contact_ops.federation.consumer_sdk.cache import ContactCache
from contact_ops.federation.consumer_sdk.models import JsonRpcError, OrgSummary, PersonSummary

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"  # noqa: S105


class ContactOpsConsumerClient:
    """Holds RFC 8693 OBO token exchange and JSON-RPC MCP calls."""

    def __init__(
        self,
        *,
        token_endpoint: str,
        client_id: str,
        client_secret: str,
        mcp_url: str,
        audience: str = "contact-ops-mcp",
        scope: str = "contactops:contacts.read",
        http: httpx.AsyncClient | None = None,
        cache: ContactCache | None = None,
    ) -> None:
        self._token_endpoint = token_endpoint
        self._client_id = client_id
        self._client_secret = client_secret
        self._mcp_url = mcp_url
        self._audience = audience
        self._scope = scope
        self._http = http or httpx.AsyncClient(timeout=10.0)
        self._cache = cache
        self._access_token: str | None = None
        self._expires_at = 0.0
        self._subject_token: str | None = None

    async def close(self) -> None:
        await self._http.aclose()

    async def exchange_subject_token(self, subject_token: str) -> str:
        if self._access_token is not None and self._subject_token == subject_token:
            if time.time() < self._expires_at - 30:
                return self._access_token

        response = await self._http.post(
            self._token_endpoint,
            data={
                "grant_type": TOKEN_EXCHANGE_GRANT,
                "subject_token": subject_token,
                "audience": self._audience,
                "scope": self._scope,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("token endpoint returned a non-object JSON payload")
        token = str(payload["access_token"])
        expires_in = int(payload.get("expires_in", 300))
        self._access_token = token
        self._subject_token = subject_token
        self._expires_at = time.time() + expires_in
        return token

    async def call_tool(
        self,
        *,
        subject_token: str,
        tool: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        access_token = await self.exchange_subject_token(subject_token)
        request_id = str(uuid.uuid4())
        response = await self._http.post(
            self._mcp_url,
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("MCP endpoint returned a non-object JSON payload")
        if "error" in payload:
            error = payload["error"]
            if isinstance(error, dict):
                raise JsonRpcError(error.get("code", "ERROR"), str(error.get("message", "")), error)
            raise JsonRpcError("ERROR", str(error))
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("MCP endpoint returned a non-object result")
        return result

    async def get_person(self, *, subject_token: str, person_id: uuid.UUID) -> PersonSummary:
        cache_key = f"person:{person_id}"
        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return PersonSummary.model_validate(cached)
        result = await self.call_tool(
            subject_token=subject_token,
            tool="get_person",
            arguments={"person_id": str(person_id)},
        )
        person = _person_summary_from_result(result)
        if self._cache is not None:
            self._cache.set(cache_key, person.model_dump(mode="json"))
        return person

    async def find_person_by_identifier(
        self,
        *,
        subject_token: str,
        namespace: str,
        value: str,
    ) -> PersonSummary | None:
        result = await self.call_tool(
            subject_token=subject_token,
            tool="find_person_by_identifier",
            arguments={"identifiers": [{"namespace": namespace, "value": value}]},
        )
        matches = result.get("matches")
        if not matches:
            return None
        first = matches[0]
        if not isinstance(first, dict):
            raise RuntimeError("find_person_by_identifier returned a malformed match")
        return _person_summary_from_result(first)

    async def get_org(self, *, subject_token: str, org_id: uuid.UUID) -> OrgSummary:
        cache_key = f"org:{org_id}"
        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return OrgSummary.model_validate(cached)
        result = await self.call_tool(
            subject_token=subject_token,
            tool="get_org",
            arguments={"org_id": str(org_id)},
        )
        org = OrgSummary.model_validate(result)
        if self._cache is not None:
            self._cache.set(cache_key, org.model_dump(mode="json"))
        return org


def _person_summary_from_result(result: dict[str, Any]) -> PersonSummary:
    if "primary_email" in result:
        return PersonSummary.model_validate(result)
    emails = result.get("emails")
    primary_email = None
    if isinstance(emails, list):
        for email in emails:
            if isinstance(email, dict) and email.get("is_primary") is True:
                primary_email = str(email.get("address", ""))
                break
        if primary_email is None and emails and isinstance(emails[0], dict):
            primary_email = str(emails[0].get("address", ""))
    normalized = {**result, "primary_email": primary_email}
    return PersonSummary.model_validate(normalized)
