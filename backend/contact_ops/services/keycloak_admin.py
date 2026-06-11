"""Keycloak Admin REST helpers for membership management (Phase 4 Team).

A small, read-leaning client over the Keycloak Admin REST API, used by the
workspace member-management MCP tools (``mcp/tools/tenancy.py``) to:

  * resolve an invitee's *email* to their Keycloak ``sub`` (uc_uid) so an admin
    can invite a teammate by email instead of an opaque subject id, and
  * enrich a workspace's membership rows (which store only ``user_uc_uid``) with
    a human label (email / name) for the members UI.

It authenticates with the SAME confidential ``contact-ops-switch`` service
account the workspace-switch / onboarding writes use (client-credentials grant;
that SA holds realm-management ``manage-users`` — see
``scripts/keycloak/setup_contact_ops_switch.sh``). When the switch client secret
is unset the helpers are INERT: lookups return ``None`` (so the caller degrades
to showing raw uc_uids and rejects email invites with a clear, actionable error)
rather than raising — identical posture to the switch endpoint's 503-when-unconfigured.

The client secret is sent only in the token form body; it is never logged. All
failures are swallowed into ``None`` with a structured ``warning`` log so a
flaky IdP never 500s a members read.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
import structlog

from contact_ops.core.config import Settings

logger = structlog.get_logger(__name__)

# Matches the connectors' / switch endpoint's house timeout.
_KC_TIMEOUT_SECONDS = 30


def _token_url(settings: Settings) -> str:
    return f"{settings.KEYCLOAK_ISSUER.rstrip('/')}/protocol/openid-connect/token"


def _admin_realm_base(settings: Settings) -> str | None:
    """Derive ``<base>/admin/realms/<realm>`` from the issuer realm URL.

    The issuer is ``<base>/realms/<realm>``; the Admin REST API for the same
    realm lives at ``<base>/admin/realms/<realm>``. Returns ``None`` for a
    malformed (non-realm) issuer rather than building a bad URL.
    """
    issuer = settings.KEYCLOAK_ISSUER.rstrip("/")
    marker = "/realms/"
    idx = issuer.find(marker)
    if idx == -1:
        return None
    base = issuer[:idx]
    realm = issuer[idx + len(marker):]
    return f"{base}/admin/realms/{realm}"


def is_configured(settings: Settings) -> bool:
    """True when the switch service account is available for admin lookups."""
    return bool(
        settings.KEYCLOAK_SWITCH_CLIENT_SECRET
        and _admin_realm_base(settings) is not None
    )


async def _mint_admin_token(
    client: httpx.AsyncClient, settings: Settings
) -> str | None:
    """client_credentials grant for the contact-ops-switch service account.

    Returns the access token, or ``None`` on any failure (logged). The secret is
    sent only in the form body and never logged.
    """
    try:
        resp = await client.post(
            _token_url(settings),
            data={
                "grant_type": "client_credentials",
                "client_id": settings.KEYCLOAK_SWITCH_CLIENT_ID,
                "client_secret": settings.KEYCLOAK_SWITCH_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except httpx.HTTPError as exc:
        logger.warning("kc_admin_token_failed", error=type(exc).__name__)
        return None
    if resp.status_code != 200:
        logger.warning("kc_admin_token_failed", status=resp.status_code)
        return None
    try:
        token = resp.json().get("access_token")
    except (ValueError, KeyError):
        return None
    return token if isinstance(token, str) and token else None


def _summarize(user: dict[str, Any]) -> dict[str, Any]:
    """Project a Keycloak UserRepresentation to the fields the UI/label needs."""
    first = (user.get("firstName") or "").strip()
    last = (user.get("lastName") or "").strip()
    full = " ".join(p for p in (first, last) if p)
    return {
        "uc_uid": str(user.get("id") or ""),
        "email": user.get("email"),
        "username": user.get("username"),
        "full_name": full or None,
        "enabled": bool(user.get("enabled", True)),
    }


async def find_user_by_email(
    settings: Settings, email: str
) -> dict[str, Any] | None:
    """Resolve an email to a single Keycloak user (exact, case-insensitive).

    Returns the ``_summarize`` projection, or ``None`` if unconfigured, not
    found, ambiguous (>1 match), or on any IdP error. Keycloak's ``email``
    query with ``exact=true`` is case-insensitive on the stored email.
    """
    if not is_configured(settings):
        return None
    base = _admin_realm_base(settings)
    if base is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=_KC_TIMEOUT_SECONDS) as client:
            token = await _mint_admin_token(client, settings)
            if token is None:
                return None
            resp = await client.get(
                f"{base}/users",
                params={"email": email, "exact": "true"},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                logger.warning("kc_find_user_failed", status=resp.status_code)
                return None
            users = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("kc_find_user_failed", error=type(exc).__name__)
        return None
    if not isinstance(users, list) or len(users) != 1:
        # 0 = not registered; >1 = ambiguous (do not guess which identity).
        return None
    return _summarize(users[0])


async def get_users_by_ids(
    settings: Settings, user_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Best-effort batch label lookup: {uc_uid: summary} for the given subs.

    Used to decorate membership rows. Unconfigured / failed lookups simply omit
    that id from the map (the caller falls back to the raw uc_uid). One GET per
    id, but a workspace has few members and the result is cached by the UI.
    """
    out: dict[str, dict[str, Any]] = {}
    ids = [i for i in dict.fromkeys(user_ids) if i]  # de-dupe, drop empties
    if not ids or not is_configured(settings):
        return out
    base = _admin_realm_base(settings)
    if base is None:
        return out
    try:
        async with httpx.AsyncClient(timeout=_KC_TIMEOUT_SECONDS) as client:
            token = await _mint_admin_token(client, settings)
            if token is None:
                return out
            headers = {"Authorization": f"Bearer {token}"}
            for uid in ids:
                try:
                    resp = await client.get(
                        f"{base}/users/{quote(uid, safe='')}", headers=headers
                    )
                except httpx.HTTPError:
                    continue
                if resp.status_code == 200:
                    try:
                        out[uid] = _summarize(resp.json())
                    except ValueError:
                        continue
    except httpx.HTTPError as exc:
        logger.warning("kc_get_users_failed", error=type(exc).__name__)
    return out
