"""PERSONAL vs PROSPECT: who may see which contacts, and how a cross-category
identity collision is marked without merging the rows.

A human session sees both categories unless it asks for one. A service or agent
principal (personal access token, Brigade federation, Keycloak service account,
or a nested RFC 8693 ``act`` chain) sees PROSPECT only, unless it passes
``category=PERSONAL`` and holds ``contacts:read_personal``. That scope is not
granted to service principals by default.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.models.enums import ContactCategory

READ_PERSONAL_SCOPE = "contacts:read_personal"
SOURCED_PROSPECT_TAG = "sourced-prospect"
POSSIBLE_SAME_PERSON_EVENT = "person.possible_same_person"
_PAT_ISSUER = "contact-ops:personal-access-token"
_STANDALONE_ISSUER = "standalone://contact-ops"
_SPACE_RE = re.compile(r"\s+")


class CategoryScopeDenied(Exception):  # noqa: N818 - matches ToolError / SSRFBlocked
    """Caller asked for PERSONAL data without ``contacts:read_personal``."""

    def __init__(self, scope: str = READ_PERSONAL_SCOPE) -> None:
        self.scope = scope
        super().__init__(scope)


def _scopes(claims: dict[str, Any] | None) -> set[str]:
    raw = (claims or {}).get("scope", "")
    if isinstance(raw, str):
        return {scope for scope in raw.split() if scope}
    if isinstance(raw, list):
        return {str(scope) for scope in raw}
    return set()


def _has_act(obj: dict[str, Any] | None) -> bool:
    if not isinstance(obj, dict):
        return False
    act = obj.get("act")
    return isinstance(act, dict) and bool(act)


def _human_issuers() -> set[str]:
    issuers = {_STANDALONE_ISSUER}
    try:
        from contact_ops.core.config import get_settings

        keycloak = (get_settings().KEYCLOAK_ISSUER or "").rstrip("/")
    except Exception:  # noqa: BLE001 — unknown issuer stays non-human
        keycloak = ""
    if keycloak:
        issuers.add(keycloak)
    return issuers


def is_service_principal(
    claims: dict[str, Any] | None,
    actor_chain: dict[str, Any] | None = None,
    human_authority: str | None = None,
) -> bool:
    """True for a PAT, Brigade actor, service account, or agent acting for someone.

    A missing issuer (internal tool contexts and tests) is a human. A Keycloak
    or standalone session is a human unless it carries an actor chain, a PAT, or
    a service-account username.
    """
    claims = claims or {}
    if claims.get("pat_id"):
        return True
    if claims.get("brigade_claims"):
        return True
    if str(claims.get("iss") or "") == _PAT_ISSUER:
        return True
    if _has_act(claims) or _has_act(actor_chain):
        return True
    username = str(claims.get("preferred_username") or "")
    if username.startswith("service-account-"):
        return True
    if human_authority == "agent":
        return True
    iss = str(claims.get("iss") or "").rstrip("/")
    if not iss:
        return False
    return iss not in _human_issuers()


def _requested(value: str | None) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip().upper()
    return text_value or None


def resolve_category_filter(
    claims: dict[str, Any] | None,
    requested: str | None,
    actor_chain: dict[str, Any] | None = None,
    human_authority: str | None = None,
) -> ContactCategory | None:
    """Category predicate for a list, search, or export.

    ``None`` means both categories. A service that omits the filter, or passes
    ``ALL``, gets PROSPECT and does not widen to the personal network.
    """
    service = is_service_principal(claims, actor_chain, human_authority)
    raw = _requested(requested)
    if raw == "ALL":
        return ContactCategory.PROSPECT if service else None
    if raw is None:
        return ContactCategory.PROSPECT if service else None
    chosen = ContactCategory(raw)
    if (
        chosen is ContactCategory.PERSONAL
        and service
        and READ_PERSONAL_SCOPE not in _scopes(claims)
    ):
        raise CategoryScopeDenied(READ_PERSONAL_SCOPE)
    return chosen


def tag_marks_sourced_prospect(tags: list[str] | None) -> bool:
    for tag in tags or []:
        slug = _SPACE_RE.sub("-", tag.strip().lower())
        if slug == SOURCED_PROSPECT_TAG:
            return True
    return False


def category_for_write(
    claims: dict[str, Any] | None,
    explicit: str | None,
    tags: list[str] | None = None,
    actor_chain: dict[str, Any] | None = None,
    human_authority: str | None = None,
) -> ContactCategory:
    """Category stored on create/upsert.

    An explicit value wins. A service that omits it writes PROSPECT, which is
    how Customer-Ops ``upsertContact`` (tool ``upsert_person``) lands a lead.
    A human that omits it writes PERSONAL. The ``sourced-prospect`` tag also
    forces PROSPECT when no category was sent. A service may write PERSONAL
    only when it says so and holds ``contacts:read_personal``.
    """
    service = is_service_principal(claims, actor_chain, human_authority)
    if explicit is not None:
        chosen = ContactCategory(str(explicit).strip().upper())
        if (
            chosen is ContactCategory.PERSONAL
            and service
            and READ_PERSONAL_SCOPE not in _scopes(claims)
        ):
            raise CategoryScopeDenied(READ_PERSONAL_SCOPE)
        return chosen
    if tag_marks_sourced_prospect(tags):
        return ContactCategory.PROSPECT
    if service:
        return ContactCategory.PROSPECT
    return ContactCategory.PERSONAL


def categories_conflict(left: Any, right: Any) -> bool:
    return _category_value(left) != _category_value(right)


def _category_value(value: Any) -> str:
    raw = value.value if hasattr(value, "value") else value
    return str(raw or "")


def normalize_display_name(name: str | None) -> str:
    return " ".join((name or "").casefold().split())


def assert_personal_subject_readable(
    claims: dict[str, Any] | None,
    category: Any,
    actor_chain: dict[str, Any] | None = None,
    human_authority: str | None = None,
) -> None:
    """A one-person export of a PERSONAL row needs the personal-data scope."""
    if _category_value(category) != ContactCategory.PERSONAL.value:
        return
    if not is_service_principal(claims, actor_chain, human_authority):
        return
    if READ_PERSONAL_SCOPE not in _scopes(claims):
        raise CategoryScopeDenied(READ_PERSONAL_SCOPE)


def should_skip_alias_collapse(
    *,
    op_type: str,
    entity: str,
    human_authorized: bool,
    category_a: str | None,
    category_b: str | None,
) -> bool:
    """True when a graph ALIAS_OF write would collapse two categories.

    Unmerge still deletes the edge. A human merge sets ``human_authorized`` and
    may collapse the pair on purpose. An automation never does.
    """
    if entity != "person":
        return False
    if str(op_type).endswith("unmerge"):
        return False
    if human_authorized:
        return False
    if category_a is None or category_b is None:
        return False
    return category_a != category_b


async def link_suspected_same_as(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    left_id: UUID,
    right_id: UUID,
) -> None:
    """Idempotent ``suspected_same_as`` edge. Does not change either person."""
    if left_id == right_id:
        return
    first, second = sorted((left_id, right_id), key=str)
    await db.execute(
        text(
            """
            INSERT INTO person_person_relation (
                from_person_id, to_person_id, relation_type, inverse_relation_type,
                tenant_visibility, context
            ) VALUES (
                CAST(:left AS uuid), CAST(:right AS uuid),
                'suspected_same_as'::relation_type, 'suspected_same_as'::relation_type,
                CAST(:tenant AS uuid), 'possible same person'
            )
            ON CONFLICT (from_person_id, to_person_id, relation_type, tenant_visibility)
            DO NOTHING
            """
        ),
        {"left": str(first), "right": str(second), "tenant": str(tenant_id)},
    )


async def pair_categories(
    db: AsyncSession, left_id: UUID, right_id: UUID
) -> tuple[str | None, str | None]:
    rows = (
        await db.execute(
            text(
                """
                SELECT id, category::text AS category
                FROM persons
                WHERE id IN (CAST(:left AS uuid), CAST(:right AS uuid))
                """
            ),
            {"left": str(left_id), "right": str(right_id)},
        )
    ).mappings().all()
    found = {str(row["id"]): row["category"] for row in rows}
    return found.get(str(left_id)), found.get(str(right_id))
