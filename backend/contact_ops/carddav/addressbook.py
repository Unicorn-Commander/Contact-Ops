"""Database ↔ :class:`CanonicalContact` bridge for the CardDAV router.

The serialize/parse modules are pure functions. This module owns every
side effect: loading a :class:`Person` plus its emails/phones/urls/etc.
into a :class:`CanonicalContact`, applying an inbound canonical record
back onto the DB (find-or-create person + sync rows), listing the
visible members of a tenant's addressbook, and soft-deleting via the
per-tenant membership table.

All queries assume the session has already bound the tenant GUC with
:func:`contact_ops.carddav.auth.bind_tenant` — RLS does the heavy
lifting for tenant isolation.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import phonenumbers
import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from contact_ops.carddav.vcard_parse import parse_vcard_to_canonical
from contact_ops.carddav.vcard_serialize import (
    CanonicalAddress,
    CanonicalContact,
    CanonicalDate,
    CanonicalEmail,
    CanonicalIM,
    CanonicalOrgRole,
    CanonicalPhone,
    CanonicalRelation,
    CanonicalUrl,
)
from contact_ops.models import (
    Email,
    IMHandle,
    Organization,
    Person,
    PersonOrgRole,
    PersonPersonRelation,
    PersonTenantMembership,
    Phone,
    PostalAddress,
    Url,
)
from contact_ops.models.enums import (
    AddressType,
    EmailType,
    PhoneType,
)

logger = structlog.get_logger(__name__)


class CrossTenantWriteRefused(Exception):
    """Raised when a CardDAV PUT would touch a person owned by another tenant.

    The router maps this to HTTP 403 with a CardDAV-shaped error body.
    """


# ---------- addressbook listing ----------


@dataclass(frozen=True)
class AddressbookMember:
    person_id: uuid.UUID
    vcard_uid: str
    etag: str
    last_modified: datetime
    display_name: str


async def list_addressbook_members(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    include_archived: bool = False,
) -> list[AddressbookMember]:
    """Return every visible person in the principal's tenant addressbook.

    A person surfaces in the addressbook if:
      * Its ``canonical_owner_tenant_id`` matches the principal's tenant
        AND ``merge_status`` is ``canonical``, OR
      * A :class:`PersonTenantMembership` row exists for this tenant with
        ``visibility`` not equal to ``archived`` (unless overridden).
    """

    own_rows = (
        await session.execute(
            select(Person).where(
                Person.canonical_owner_tenant_id == tenant_id,
                Person.merge_status == "canonical",
            )
        )
    ).scalars().all()

    members: dict[uuid.UUID, AddressbookMember] = {}
    for row in own_rows:
        members[row.id] = _member_from_person(row)

    shared_rows = (
        await session.execute(
            select(Person, PersonTenantMembership.visibility)
            .join(
                PersonTenantMembership,
                PersonTenantMembership.person_id == Person.id,
            )
            .where(
                PersonTenantMembership.tenant_id == tenant_id,
                Person.merge_status == "canonical",
            )
        )
    ).all()
    for person, visibility in shared_rows:
        if not include_archived and visibility == "archived":
            continue
        if person.id in members:
            continue
        members[person.id] = _member_from_person(person)

    return sorted(
        members.values(), key=lambda m: (m.display_name.lower(), m.vcard_uid)
    )


def _member_from_person(row: Person) -> AddressbookMember:
    uid = row.vcard_uid or f"urn:uuid:{row.id}"
    return AddressbookMember(
        person_id=row.id,
        vcard_uid=uid,
        etag=row.etag or str(row.id),
        last_modified=row.updated_at or row.created_at,
        display_name=row.display_name,
    )


# ---------- person → canonical ----------


async def load_canonical_from_person(
    session: AsyncSession,
    *,
    person_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> CanonicalContact | None:
    """Hydrate a full :class:`CanonicalContact` for the requested person."""

    person = await session.get(Person, person_id)
    if person is None or person.merge_status != "canonical":
        return None

    emails = await _load_active(
        session, Email, person_id, Email.valid_until.is_(None)
    )
    phones = await _load_active(
        session, Phone, person_id, Phone.valid_until.is_(None)
    )
    urls = await _load_active(session, Url, person_id, None)
    addresses = await _load_active(
        session,
        PostalAddress,
        person_id,
        PostalAddress.valid_until.is_(None),
    )
    im_handles = await _load_active(session, IMHandle, person_id, None)

    org_rows = (
        await session.execute(
            select(PersonOrgRole, Organization)
            .join(Organization, Organization.id == PersonOrgRole.organization_id)
            .where(PersonOrgRole.person_id == person.id)
        )
    ).all()

    relations = (
        await session.execute(
            select(PersonPersonRelation, Person)
            .join(Person, Person.id == PersonPersonRelation.related_person_id)
            .where(PersonPersonRelation.subject_person_id == person.id)
        )
    ).all()

    membership = await session.scalar(
        select(PersonTenantMembership).where(
            PersonTenantMembership.person_id == person.id,
            PersonTenantMembership.tenant_id == tenant_id,
        )
    )
    note = membership.notes if membership else None
    categories = list(membership.tags) if membership else []

    kind_value: str = (
        person.kind.value if hasattr(person.kind, "value") else str(person.kind or "individual")
    )

    return CanonicalContact(
        vcard_uid=person.vcard_uid or f"urn:uuid:{person.id}",
        display_name=person.display_name,
        family_name=person.family_name,
        given_name=person.given_name,
        additional_names=list(person.additional_names or []),
        honorific_prefix=person.honorific_prefix,
        honorific_suffix=person.honorific_suffix,
        nicknames=list(person.nicknames or []),
        phonetic_family_name=person.phonetic_family_name,
        phonetic_given_name=person.phonetic_given_name,
        birthday=_canonical_date_from_jsonb(person.birthday),
        anniversary=_canonical_date_from_jsonb(person.anniversary),
        gender_identity=person.gender_identity,
        gender_freeform=None,
        pronouns=person.pronouns,
        preferred_languages=list(person.preferred_languages or []),
        time_zone=person.time_zone,
        note=note,
        categories=categories,
        kind=kind_value,  # type: ignore[arg-type]
        members=[],
        rev=person.updated_at,
        photo_inline_data=None,
        photo_inline_content_type=None,
        photo_url=None,
        emails=[_email_to_canonical(e) for e in emails],
        phones=[_phone_to_canonical(p) for p in phones],
        urls=[_url_to_canonical(u) for u in urls],
        addresses=[_address_to_canonical(a) for a in addresses],
        im_handles=[_im_to_canonical(h) for h in im_handles],
        organizations=[_org_to_canonical(role, org) for role, org in org_rows],
        related=[_relation_to_canonical(rel, target) for rel, target in relations],
        source_pid_map={k: str(v) for k, v in (person.source_pid_map or {}).items()},
    )


async def _load_active(session, model, person_id, soft_delete_clause):  # type: ignore[no-untyped-def]
    """Fetch rows whose person_id matches AND (optional) the soft-delete clause."""

    stmt = select(model).where(model.person_id == person_id)
    if soft_delete_clause is not None:
        stmt = stmt.where(soft_delete_clause)
    return (await session.execute(stmt)).scalars().all()


# ---------- vCard text → DB upsert ----------


async def apply_vcard_text_to_db(
    session: AsyncSession,
    *,
    vcard_text: str,
    principal_tenant_id: uuid.UUID,
    principal_user_id: str,
    target_vcard_uid: str,
    existing_person_id: uuid.UUID | None,
) -> tuple[Person, CanonicalContact]:
    """Parse + upsert a vCard payload received via PUT."""

    canonical = parse_vcard_to_canonical(vcard_text)
    # The URL path's UID is authoritative.
    if not canonical.vcard_uid or canonical.vcard_uid != target_vcard_uid:
        canonical.vcard_uid = target_vcard_uid

    person = None
    if existing_person_id is not None:
        person = await session.get(Person, existing_person_id)
    if person is None:
        person = await session.scalar(
            select(Person).where(Person.vcard_uid == canonical.vcard_uid)
        )

    if person is None:
        person = Person(
            id=uuid.uuid4(),
            display_name=canonical.display_name or canonical.vcard_uid,
            canonical_owner_tenant_id=principal_tenant_id,
            vcard_uid=canonical.vcard_uid,
        )
        session.add(person)
        await session.flush()
    elif person.canonical_owner_tenant_id != principal_tenant_id:
        # HIPAA fence — never cross-tenant-merge via CardDAV PUT.
        raise CrossTenantWriteRefused(
            f"person {person.id} is owned by tenant "
            f"{person.canonical_owner_tenant_id}, not {principal_tenant_id}"
        )

    _apply_canonical_scalars(person, canonical)
    await _sync_emails(session, person.id, canonical.emails)
    await _sync_phones(session, person.id, canonical.phones)
    await _sync_urls(session, person.id, canonical.urls)
    await _sync_addresses(session, person.id, canonical.addresses)
    await _sync_im_handles(session, person.id, canonical.im_handles)
    await _sync_organizations(session, person.id, canonical.organizations)
    await _sync_membership_metadata(
        session,
        person_id=person.id,
        tenant_id=principal_tenant_id,
        note=canonical.note,
        categories=canonical.categories,
    )
    if canonical.source_pid_map:
        person.source_pid_map = dict(canonical.source_pid_map)

    await session.flush()
    return person, canonical


# ---------- soft-delete ----------


async def soft_delete_person_for_tenant(
    session: AsyncSession,
    *,
    person_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> bool:
    """Soft-delete a person from the requesting tenant's addressbook.

    Owning tenant → set Person.merge_status='archived'. Sharing tenant →
    flip PersonTenantMembership.visibility='archived' (the canonical
    record on the owning tenant remains intact).
    """

    person = await session.get(Person, person_id)
    if person is None:
        return False
    if person.canonical_owner_tenant_id == tenant_id:
        person.merge_status = "archived"  # type: ignore[assignment]
        await session.flush()
        return True
    await session.execute(
        update(PersonTenantMembership)
        .where(
            PersonTenantMembership.person_id == person_id,
            PersonTenantMembership.tenant_id == tenant_id,
        )
        .values(visibility="archived")
    )
    return True


# ---------- row → canonical helpers ----------


def _email_to_canonical(row: Email) -> CanonicalEmail:
    return CanonicalEmail(
        address=row.address,
        type=row.type.value if hasattr(row.type, "value") else str(row.type),
        label=row.label,
        is_primary=row.is_primary,
    )


def _phone_to_canonical(row: Phone) -> CanonicalPhone:
    return CanonicalPhone(
        e164=row.e164,
        type=row.type.value if hasattr(row.type, "value") else str(row.type),
        label=row.label,
        is_primary=row.is_primary,
        is_sms_capable=row.is_sms_capable,
        is_signal=row.is_signal,
        is_whatsapp=row.is_whatsapp,
        is_imessage=row.is_imessage,
    )


def _url_to_canonical(row: Url) -> CanonicalUrl:
    return CanonicalUrl(
        url=row.url,
        type=row.type,
        label=row.label,
        is_primary=row.is_primary,
    )


def _address_to_canonical(row: PostalAddress) -> CanonicalAddress:
    return CanonicalAddress(
        type=row.type.value if hasattr(row.type, "value") else str(row.type),
        label=row.label,
        is_primary=row.is_primary,
        po_box=row.po_box,
        extended_address=row.extended_address,
        street_address=row.street_address,
        locality=row.locality,
        region=row.region,
        postal_code=row.postal_code,
        country_name=row.country_name,
        country_code=row.country_code,
        geo_lat=float(row.geo_lat) if row.geo_lat is not None else None,
        geo_lng=float(row.geo_lng) if row.geo_lng is not None else None,
    )


def _im_to_canonical(row: IMHandle) -> CanonicalIM:
    return CanonicalIM(
        protocol=row.protocol,
        handle=row.handle,
        label=row.label,
        is_primary=row.is_primary,
    )


def _org_to_canonical(role: PersonOrgRole, org: Organization) -> CanonicalOrgRole:
    return CanonicalOrgRole(
        org_display_name=org.display_name,
        department=getattr(role, "department", None),
        section=None,
        title=getattr(role, "title", None),
        role_type=(
            role.role_type.value if hasattr(role.role_type, "value") else None
        ),
        is_current=role.is_current,
        is_primary=getattr(role, "is_primary", False),
    )


def _relation_to_canonical(
    rel: PersonPersonRelation, target: Person
) -> CanonicalRelation:
    rel_type = (
        rel.relation_type.value
        if hasattr(rel.relation_type, "value")
        else str(rel.relation_type)
    )
    return CanonicalRelation(
        related_uid=target.vcard_uid or f"urn:uuid:{target.id}",
        type=rel_type,
    )


def _canonical_date_from_jsonb(value: dict | None) -> CanonicalDate | None:
    if not value:
        return None
    try:
        return CanonicalDate(
            year=value.get("year"),
            month=int(value["month"]),
            day=int(value["day"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


# ---------- canonical → DB scalar applier ----------


def _apply_canonical_scalars(person: Person, canonical: CanonicalContact) -> None:
    person.display_name = canonical.display_name or person.display_name
    person.family_name = canonical.family_name
    person.given_name = canonical.given_name
    person.additional_names = list(canonical.additional_names)
    person.honorific_prefix = canonical.honorific_prefix
    person.honorific_suffix = canonical.honorific_suffix
    person.nicknames = list(canonical.nicknames)
    person.phonetic_family_name = canonical.phonetic_family_name
    person.phonetic_given_name = canonical.phonetic_given_name
    person.gender_identity = canonical.gender_identity
    person.pronouns = canonical.pronouns
    person.preferred_languages = list(canonical.preferred_languages)
    person.time_zone = canonical.time_zone
    if canonical.birthday is not None:
        person.birthday = {
            "year": canonical.birthday.year,
            "month": canonical.birthday.month,
            "day": canonical.birthday.day,
        }
    if canonical.anniversary is not None:
        person.anniversary = {
            "year": canonical.anniversary.year,
            "month": canonical.anniversary.month,
            "day": canonical.anniversary.day,
        }


# ---------- multi-row sync helpers ----------


async def _sync_emails(
    session: AsyncSession,
    person_id: uuid.UUID,
    canonical_emails: list[CanonicalEmail],
) -> None:
    rows = (
        await session.execute(
            select(Email).where(
                Email.person_id == person_id, Email.valid_until.is_(None)
            )
        )
    ).scalars().all()
    by_key = {row.address.lower(): row for row in rows}
    seen: set[str] = set()
    for index, item in enumerate(canonical_emails):
        if not item.address:
            continue
        key = item.address.lower().strip()
        seen.add(key)
        row = by_key.get(key)
        email_type = _safe_enum(EmailType, item.type) or EmailType.other
        if row is None:
            session.add(
                Email(
                    person_id=person_id,
                    address=key,
                    type=email_type,
                    label=item.label,
                    is_primary=item.is_primary or index == 0,
                    confidence=Decimal("1.000"),
                )
            )
        else:
            row.type = email_type
            row.label = item.label
            row.is_primary = item.is_primary or index == 0
    for key, row in by_key.items():
        if key not in seen:
            row.valid_until = datetime.now(timezone.utc)
            row.is_primary = False
    await session.flush()


async def _sync_phones(
    session: AsyncSession,
    person_id: uuid.UUID,
    canonical_phones: list[CanonicalPhone],
) -> None:
    rows = (
        await session.execute(
            select(Phone).where(
                Phone.person_id == person_id, Phone.valid_until.is_(None)
            )
        )
    ).scalars().all()
    by_key = {row.e164: row for row in rows}
    seen: set[str] = set()
    for index, item in enumerate(canonical_phones):
        if not item.e164:
            continue
        key = _normalize_phone(item.e164)
        seen.add(key)
        row = by_key.get(key)
        phone_type = _safe_enum(PhoneType, item.type) or PhoneType.other
        if row is None:
            session.add(
                Phone(
                    person_id=person_id,
                    e164=key,
                    type=phone_type,
                    label=item.label,
                    is_primary=item.is_primary or index == 0,
                    is_sms_capable=item.is_sms_capable,
                    is_signal=item.is_signal,
                    is_whatsapp=item.is_whatsapp,
                    is_imessage=item.is_imessage,
                    confidence=Decimal("1.000"),
                )
            )
        else:
            row.type = phone_type
            row.label = item.label
            row.is_primary = item.is_primary or index == 0
            row.is_sms_capable = item.is_sms_capable or row.is_sms_capable
    for key, row in by_key.items():
        if key not in seen:
            row.valid_until = datetime.now(timezone.utc)
            row.is_primary = False
    await session.flush()


async def _sync_urls(
    session: AsyncSession,
    person_id: uuid.UUID,
    canonical_urls: list[CanonicalUrl],
) -> None:
    rows = (
        await session.execute(select(Url).where(Url.person_id == person_id))
    ).scalars().all()
    by_key = {row.url.lower(): row for row in rows}
    seen: set[str] = set()
    for index, item in enumerate(canonical_urls):
        if not item.url:
            continue
        key = item.url.lower().strip()
        seen.add(key)
        row = by_key.get(key)
        if row is None:
            session.add(
                Url(
                    person_id=person_id,
                    url=item.url,
                    type=item.type or "profile",
                    label=item.label,
                    is_primary=item.is_primary or index == 0,
                    confidence=Decimal("1.000"),
                )
            )
        else:
            row.type = item.type or row.type
            row.label = item.label
            row.is_primary = item.is_primary or index == 0
    for key, row in by_key.items():
        if key not in seen:
            await session.delete(row)
    await session.flush()


async def _sync_addresses(
    session: AsyncSession,
    person_id: uuid.UUID,
    canonical_addresses: list[CanonicalAddress],
) -> None:
    rows = (
        await session.execute(
            select(PostalAddress).where(
                PostalAddress.person_id == person_id,
                PostalAddress.valid_until.is_(None),
            )
        )
    ).scalars().all()
    by_key = {_address_key(r): r for r in rows}
    seen: set[str] = set()
    for index, item in enumerate(canonical_addresses):
        key = _canonical_address_key(item)
        if not key:
            continue
        seen.add(key)
        row = by_key.get(key)
        addr_type = _safe_enum(AddressType, item.type) or AddressType.other
        if row is None:
            session.add(
                PostalAddress(
                    person_id=person_id,
                    type=addr_type,
                    label=item.label,
                    is_primary=item.is_primary or index == 0,
                    po_box=item.po_box,
                    extended_address=item.extended_address,
                    street_address=item.street_address,
                    locality=item.locality,
                    region=item.region,
                    postal_code=item.postal_code,
                    country_name=item.country_name,
                    country_code=(item.country_code or "")[:2] or None,
                    geo_lat=Decimal(str(item.geo_lat)) if item.geo_lat is not None else None,
                    geo_lng=Decimal(str(item.geo_lng)) if item.geo_lng is not None else None,
                    confidence=Decimal("1.000"),
                )
            )
        else:
            row.type = addr_type
            row.label = item.label
            row.is_primary = item.is_primary or index == 0
            row.po_box = item.po_box
            row.extended_address = item.extended_address
            row.street_address = item.street_address
            row.locality = item.locality
            row.region = item.region
            row.postal_code = item.postal_code
            row.country_name = item.country_name
            if item.country_code:
                row.country_code = item.country_code[:2]
    for key, row in by_key.items():
        if key not in seen:
            row.valid_until = datetime.now(timezone.utc)
            row.is_primary = False
    await session.flush()


async def _sync_im_handles(
    session: AsyncSession,
    person_id: uuid.UUID,
    canonical_im: list[CanonicalIM],
) -> None:
    rows = (
        await session.execute(select(IMHandle).where(IMHandle.person_id == person_id))
    ).scalars().all()
    by_key = {(r.protocol.lower(), r.handle.lower()): r for r in rows}
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(canonical_im):
        if not item.handle:
            continue
        key = (item.protocol.lower(), item.handle.lower())
        seen.add(key)
        row = by_key.get(key)
        if row is None:
            session.add(
                IMHandle(
                    person_id=person_id,
                    protocol=item.protocol,
                    handle=item.handle,
                    label=item.label,
                    is_primary=item.is_primary or index == 0,
                    confidence=Decimal("1.000"),
                )
            )
        else:
            row.label = item.label
            row.is_primary = item.is_primary or index == 0
    for key, row in by_key.items():
        if key not in seen:
            await session.delete(row)
    await session.flush()


async def _sync_organizations(
    session: AsyncSession,
    person_id: uuid.UUID,
    canonical_orgs: list[CanonicalOrgRole],
) -> None:
    """Phase 2 scope: do NOT auto-create Organizations on CardDAV write.

    Free-text ORG/TITLE updates from iOS would otherwise spam the
    organizations table with duplicate company names. We only update the
    current person's ``occupation_title`` placeholder column with the
    inbound title; full ``PersonOrgRole`` upserts land in Phase 3 when
    the organization-matching service is available.
    """

    if not canonical_orgs:
        return
    primary = canonical_orgs[0]
    if primary.title:
        person = await session.get(Person, person_id)
        if person is not None:
            person.occupation_title = primary.title
            await session.flush()
    logger.info(
        "carddav_org_update_deferred",
        person_id=str(person_id),
        org_name=primary.org_display_name,
    )


async def _sync_membership_metadata(
    session: AsyncSession,
    *,
    person_id: uuid.UUID,
    tenant_id: uuid.UUID,
    note: str | None,
    categories: list[str],
) -> None:
    row = await session.scalar(
        select(PersonTenantMembership).where(
            PersonTenantMembership.person_id == person_id,
            PersonTenantMembership.tenant_id == tenant_id,
        )
    )
    if row is None:
        session.add(
            PersonTenantMembership(
                person_id=person_id,
                tenant_id=tenant_id,
                visibility="visible",
                notes=note,
                tags=list(categories or []),
                custom_attrs={},
            )
        )
        await session.flush()
        return
    if note is not None:
        row.notes = note
    if categories:
        row.tags = list(categories)


# ---------- helpers ----------


def _safe_enum(enum_cls, raw):  # type: ignore[no-untyped-def]
    if raw is None:
        return None
    try:
        return enum_cls(raw)
    except (ValueError, KeyError):
        return None


_PHONE_DIGITS_RE = re.compile(r"\D+")


def _normalize_phone(value: str) -> str:
    cleaned = value.strip()
    try:
        parsed = phonenumbers.parse(cleaned, None)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )
    except phonenumbers.NumberParseException:
        pass
    digits = _PHONE_DIGITS_RE.sub("", cleaned)
    return f"+{digits}" if digits and not cleaned.startswith("+") else cleaned


def _address_key(row: PostalAddress) -> str:
    return "|".join(
        [
            (row.street_address or "").lower(),
            (row.locality or "").lower(),
            (row.region or "").lower(),
            (row.postal_code or "").lower(),
            (row.country_code or row.country_name or "").lower(),
        ]
    )


def _canonical_address_key(addr: CanonicalAddress) -> str:
    parts = [
        (addr.street_address or "").lower().strip(),
        (addr.locality or "").lower().strip(),
        (addr.region or "").lower().strip(),
        (addr.postal_code or "").lower().strip(),
        (addr.country_code or addr.country_name or "").lower().strip(),
    ]
    if not any(parts):
        return ""
    return "|".join(parts)
