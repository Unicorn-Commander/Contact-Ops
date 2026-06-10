"""Gmail / Google People API OAuth connector."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from contact_ops.connectors.base import Connector, apply_refreshed_token, token_needs_refresh
from contact_ops.core.config import get_settings
from contact_ops.importers.base import (
    CanonicalImportRecord,
    ImportAddress,
    ImportEmail,
    ImportEmployment,
    ImportPhone,
)

if TYPE_CHECKING:
    from contact_ops.mcp.registry import MCPContext

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105
PEOPLE_URL = "https://people.googleapis.com/v1/people/me/connections"


class GmailConnector(Connector):
    provider = "gmail"

    async def ensure_fresh_token(self) -> bool:
        if not token_needs_refresh(self.payload):
            return False
        return await self.refresh_access_token()

    async def refresh_access_token(self) -> bool:
        refresh_token = self.payload.get("refresh_token")
        if not refresh_token:
            return False
        apply_refreshed_token(self.payload, await refresh_gmail_token(str(refresh_token)))
        return True

    async def pull(self, ctx: MCPContext, since: object | None) -> list[CanonicalImportRecord]:
        access_token = str(self.payload["access_token"])
        records: list[CanonicalImportRecord] = []
        page_token: str | None = None
        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                params: dict[str, str | int] = {
                    "personFields": (
                        "names,emailAddresses,phoneNumbers,addresses,organizations,"
                        "birthdays,biographies"
                    ),
                    "pageSize": 200,
                }
                if page_token:
                    params["pageToken"] = page_token
                response = await client.get(
                    PEOPLE_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                    params=params,
                )
                response.raise_for_status()
                body = response.json()
                for item in body.get("connections", []):
                    if isinstance(item, dict):
                        records.append(_record_from_person(item))
                next_page = body.get("nextPageToken")
                if not isinstance(next_page, str) or not next_page:
                    break
                page_token = next_page
        return records


async def exchange_gmail_code(code: str, code_verifier: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.GMAIL_CLIENT_ID,
                "client_secret": settings.GMAIL_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.GMAIL_REDIRECT_URI,
                "code_verifier": code_verifier,
            },
        )
    response.raise_for_status()
    return _token_payload(response.json())


async def refresh_gmail_token(refresh_token: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.GMAIL_CLIENT_ID,
                "client_secret": settings.GMAIL_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
    response.raise_for_status()
    return _token_payload(response.json())


def _token_payload(body: dict[str, Any]) -> dict[str, Any]:
    expires_in = int(body.get("expires_in") or 3600)
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token"),
        "expires_at": (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat(),
        "scopes": body.get("scope", ""),
    }


def _record_from_person(item: dict[str, Any]) -> CanonicalImportRecord:
    name = _first(item, "names")
    display_name = str(name.get("displayName") or "Unnamed Contact") if name else "Unnamed Contact"
    emails = [
        ImportEmail(address=str(email["value"]).lower(), type="other")
        for email in item.get("emailAddresses", [])
        if isinstance(email, dict) and email.get("value")
    ]
    phones = [
        ImportPhone(e164=str(phone["value"]), type="other")
        for phone in item.get("phoneNumbers", [])
        if isinstance(phone, dict) and phone.get("value")
    ]
    addresses = [
        ImportAddress(
            type="home",
            street_address=addr.get("streetAddress"),
            locality=addr.get("city"),
            region=addr.get("region"),
            postal_code=addr.get("postalCode"),
            country_name=addr.get("country"),
            country_code=addr.get("countryCode"),
        )
        for addr in item.get("addresses", [])
        if isinstance(addr, dict)
    ]
    org = _first(item, "organizations")
    return CanonicalImportRecord(
        source_record_id=str(item.get("resourceName") or display_name),
        display_name=display_name,
        given_name=name.get("givenName") if name else None,
        family_name=name.get("familyName") if name else None,
        emails=emails,
        phones=phones,
        addresses=addresses,
        employments=[
            ImportEmployment(company=str(org["name"]), title=org.get("title"))
        ]
        if org and org.get("name")
        else [],
    )


def _first(item: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = item.get(key)
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None
