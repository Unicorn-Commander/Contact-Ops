"""Microsoft 365 OAuth and Graph contacts connector."""

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

CONTACTS_URL = "https://graph.microsoft.com/v1.0/me/contacts"


def _token_url() -> str:
    """Tenant-specific token endpoint; /common only works for multi-tenant apps."""
    return (
        f"https://login.microsoftonline.com/{get_settings().M365_TENANT_ID}"
        "/oauth2/v2.0/token"
    )


class M365Connector(Connector):
    provider = "m365"

    async def ensure_fresh_token(self) -> bool:
        if not token_needs_refresh(self.payload):
            return False
        return await self.refresh_access_token()

    async def refresh_access_token(self) -> bool:
        refresh_token = self.payload.get("refresh_token")
        if not refresh_token:
            return False
        apply_refreshed_token(self.payload, await refresh_m365_token(str(refresh_token)))
        return True

    async def pull(self, ctx: MCPContext, since: object | None) -> list[CanonicalImportRecord]:
        access_token = str(self.payload["access_token"])
        records: list[CanonicalImportRecord] = []
        url: str | None = CONTACTS_URL
        async with httpx.AsyncClient(timeout=30) as client:
            while url:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                response.raise_for_status()
                body = response.json()
                for item in body.get("value", []):
                    if isinstance(item, dict):
                        records.append(_record_from_graph_contact(item))
                next_link = body.get("@odata.nextLink")
                url = next_link if isinstance(next_link, str) else None
        return records


async def exchange_m365_code(code: str, code_verifier: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            _token_url(),
            data={
                "client_id": settings.M365_CLIENT_ID,
                "client_secret": settings.M365_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.M365_REDIRECT_URI,
                "code_verifier": code_verifier,
            },
        )
    response.raise_for_status()
    return _token_payload(response.json())


async def refresh_m365_token(refresh_token: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            _token_url(),
            data={
                "client_id": settings.M365_CLIENT_ID,
                "client_secret": settings.M365_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "redirect_uri": settings.M365_REDIRECT_URI,
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


def _record_from_graph_contact(item: dict[str, Any]) -> CanonicalImportRecord:
    emails = [
        ImportEmail(address=str(email["address"]).lower(), type="work")
        for email in item.get("emailAddresses", [])
        if isinstance(email, dict) and email.get("address")
    ]
    phones = [
        ImportPhone(e164=str(phone), type="work")
        for phone in item.get("businessPhones", [])
        if phone
    ]
    if item.get("mobilePhone"):
        phones.append(ImportPhone(e164=str(item["mobilePhone"]), type="mobile", is_primary=True))
    addresses = []
    business_address = item.get("businessAddress")
    if isinstance(business_address, dict):
        addresses.append(
            ImportAddress(
                type="work",
                street_address=business_address.get("street"),
                locality=business_address.get("city"),
                region=business_address.get("state"),
                postal_code=business_address.get("postalCode"),
                country_name=business_address.get("countryOrRegion"),
            )
        )
    return CanonicalImportRecord(
        source_record_id=str(item.get("id") or item.get("changeKey") or item.get("displayName")),
        display_name=str(item.get("displayName") or "Unnamed Contact"),
        given_name=item.get("givenName"),
        family_name=item.get("surname"),
        emails=emails,
        phones=phones,
        addresses=addresses,
        employments=[
            ImportEmployment(
                company=str(item["companyName"]),
                title=item.get("jobTitle"),
            )
        ]
        if item.get("companyName")
        else [],
        birthday=item.get("birthday"),
    )
