"""Canonical Contact-Ops record → vCard 4.0 text.

The full mapping table lives in ``Contact-Ops-MCP-Design.md`` §4.1.16.
This module owns the wire format; the DB-row ↔ :class:`CanonicalContact`
conversion lives in :mod:`contact_ops.carddav.addressbook` so that this
module stays a pure-function emitter with no DB dependency.

Apple ITEMn grouping, ``X-SOCIALPROFILE``, and ``CLIENTPIDMAP`` are
post-processed by :mod:`contact_ops.carddav.apple_quirks` after the main
emission so that the RFC 6350 path is uncluttered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Literal

from contact_ops.carddav.apple_quirks import (
    ClientPidMap,
    VCardProperty,
    assign_item_groups,
    emit_clientpidmaps,
    emit_x_socialprofile,
)
from contact_ops.carddav.vcard_lines import (
    escape_text,
    join_components,
    join_list,
    serialize_vcard,
)


# ---------- canonical representation ----------


@dataclass
class CanonicalEmail:
    address: str
    type: str = "other"          # personal | work | school | other | alias
    label: str | None = None     # surfaces via Apple ITEMn / X-ABLABEL
    is_primary: bool = False
    pid: int | None = None       # RFC 6350 §6.7.4 multi-source merge id


@dataclass
class CanonicalPhone:
    e164: str
    type: str = "other"          # mobile | home | work | fax | main | other
    label: str | None = None
    is_primary: bool = False
    is_sms_capable: bool = False
    is_signal: bool = False
    is_whatsapp: bool = False
    is_imessage: bool = False
    pid: int | None = None


@dataclass
class CanonicalUrl:
    url: str
    type: str = "profile"
    label: str | None = None
    is_primary: bool = False
    pid: int | None = None


@dataclass
class CanonicalAddress:
    type: str = "other"          # home | work | mailing | billing | shipping | other
    label: str | None = None
    is_primary: bool = False
    po_box: str | None = None
    extended_address: str | None = None
    street_address: str | None = None
    locality: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country_name: str | None = None
    country_code: str | None = None
    geo_lat: float | None = None
    geo_lng: float | None = None
    pid: int | None = None


@dataclass
class CanonicalIM:
    protocol: str                # signal | xmpp | matrix | telegram | discord | ...
    handle: str
    label: str | None = None
    is_primary: bool = False
    pid: int | None = None


@dataclass
class CanonicalOrgRole:
    org_display_name: str
    department: str | None = None
    section: str | None = None
    title: str | None = None
    role_type: str | None = None       # employee | founder | co_founder | ...
    is_current: bool = True
    is_primary: bool = False


@dataclass
class CanonicalRelation:
    """RFC 6350 §6.6.6 RELATED — vCard-portable subset of person_person_relation."""

    related_uid: str             # urn:uuid:... or other URI
    type: str                    # contact | spouse | friend | ...


@dataclass
class CanonicalDate:
    """A loose vCard 4.0 date with optional year (e.g., ``--MM-DD``)."""

    year: int | None
    month: int
    day: int


@dataclass
class CanonicalContact:
    """The intermediate representation between ORM rows and vCard text."""

    vcard_uid: str
    display_name: str

    family_name: str | None = None
    given_name: str | None = None
    additional_names: list[str] = field(default_factory=list)
    honorific_prefix: str | None = None
    honorific_suffix: str | None = None
    nicknames: list[str] = field(default_factory=list)
    phonetic_family_name: str | None = None
    phonetic_given_name: str | None = None

    birthday: CanonicalDate | None = None
    anniversary: CanonicalDate | None = None
    gender_identity: str | None = None     # M | F | O | N | U or freeform
    gender_freeform: str | None = None
    pronouns: str | None = None

    preferred_languages: list[str] = field(default_factory=list)
    time_zone: str | None = None

    note: str | None = None
    categories: list[str] = field(default_factory=list)

    kind: Literal["individual", "group", "org", "location"] = "individual"
    members: list[str] = field(default_factory=list)  # for kind=group: list of urn:uuid:...

    rev: datetime | None = None

    photo_inline_data: str | None = None       # raw bytes base64 (without data: prefix)
    photo_inline_content_type: str | None = None  # image/jpeg etc.
    photo_url: str | None = None               # presigned GET URL alternative

    emails: list[CanonicalEmail] = field(default_factory=list)
    phones: list[CanonicalPhone] = field(default_factory=list)
    urls: list[CanonicalUrl] = field(default_factory=list)
    addresses: list[CanonicalAddress] = field(default_factory=list)
    im_handles: list[CanonicalIM] = field(default_factory=list)
    organizations: list[CanonicalOrgRole] = field(default_factory=list)
    related: list[CanonicalRelation] = field(default_factory=list)

    source_pid_map: dict[str, str] = field(default_factory=dict)


# ---------- vCard 4.0 type-mapping helpers ----------


_EMAIL_TYPE_TO_VCARD = {
    "personal": ["HOME"],
    "work": ["WORK"],
    "school": ["WORK"],   # vCard has no SCHOOL — closest is WORK; label preserves intent
    "other": ["OTHER"],
    "alias": ["OTHER"],
}

_PHONE_TYPE_TO_VCARD = {
    "mobile": ["CELL", "VOICE"],
    "home": ["HOME", "VOICE"],
    "work": ["WORK", "VOICE"],
    "fax": ["FAX"],
    "main": ["WORK", "VOICE"],
    "other": ["OTHER", "VOICE"],
}

_ADDR_TYPE_TO_VCARD = {
    "home": ["HOME"],
    "work": ["WORK"],
    "mailing": ["POSTAL"],
    "billing": ["POSTAL"],
    "shipping": ["POSTAL"],
    "other": ["OTHER"],
}

_URL_TYPE_TO_VCARD = {
    "homepage": ["HOME"],
    "work": ["WORK"],
    "profile": [],
    "blog": [],
    "other": [],
}


# vCard 4.0 GENDER sex token. RFC 6350 §6.2.7 allows M, F, O, N, U.
_GENDER_TO_VCARD = {
    "male": "M",
    "m": "M",
    "female": "F",
    "f": "F",
    "non-binary": "O",
    "nonbinary": "O",
    "nb": "O",
    "other": "O",
    "none": "N",
    "n/a": "N",
    "na": "N",
    "unknown": "U",
}


# RFC 6350 §6.6.6 RELATED — only this subset is allowed as a TYPE= value;
# other vendor terms must be dropped or mapped.
RFC6350_RELATED_TYPES: frozenset[str] = frozenset(
    {
        "contact", "acquaintance", "friend", "met", "co-worker", "colleague",
        "co-resident", "neighbor", "child", "parent", "sibling", "spouse",
        "kin", "muse", "crush", "date", "sweetheart", "me", "agent", "emergency",
    }
)


# Map our internal RelationType -> RFC 6350 RELATED type.
RELATION_INTERNAL_TO_VCARD: dict[str, str] = {
    "parent_of": "child",          # I am parent_of X → relationship in card-of-X is parent
    "child_of": "parent",
    "spouse_of": "spouse",
    "partner_of": "spouse",
    "sibling_of": "sibling",
    "friend_of": "friend",
    "close_friend_of": "friend",
    "acquaintance_of": "acquaintance",
    "colleague_of": "colleague",
    "co_founder_of": "co-worker",
    "collaborator_of": "co-worker",
    "household_member": "co-resident",
    "roommate_of": "co-resident",
    "emergency_contact_for": "emergency",
    "knows": "contact",
    "met_once": "met",
    "mentor_of": "contact",
    "mentee_of": "contact",
    "introduced": "contact",
    "introduced_by": "contact",
    "self": "me",
}


# ---------- public entry point ----------


def serialize_canonical_to_vcard(contact: CanonicalContact) -> str:
    """Render a :class:`CanonicalContact` as a full vCard 4.0 document.

    Returns the wire-form text including ``BEGIN:VCARD``/``END:VCARD`` and
    CRLF line endings (RFC 6350 §3.1).
    """

    properties: list[VCardProperty] = []
    _emit_identity(contact, properties)
    _emit_demographics(contact, properties)
    _emit_languages(contact, properties)
    _emit_kind(contact, properties)
    _emit_emails(contact.emails, properties)
    _emit_phones(contact.phones, properties)
    _emit_addresses(contact.addresses, properties)
    _emit_urls_and_social(contact.urls, properties)
    _emit_im_handles(contact.im_handles, properties)
    _emit_organizations(contact.organizations, properties)
    _emit_related(contact.related, properties)
    _emit_photo(contact, properties)
    _emit_note(contact, properties)
    _emit_categories(contact, properties)
    _emit_rev(contact, properties)
    _emit_members(contact, properties)
    _emit_clientpidmap(contact, properties)
    # Apply Apple ITEMn grouping last so all upstream LABEL params are converted.
    grouped = assign_item_groups(properties)
    return serialize_vcard(grouped)


# ---------- field emitters ----------


def _emit_identity(contact: CanonicalContact, out: list[VCardProperty]) -> None:
    out.append(VCardProperty(name="UID", value=escape_text(contact.vcard_uid)))
    out.append(VCardProperty(name="FN", value=escape_text(contact.display_name)))

    additional = ",".join(contact.additional_names) if contact.additional_names else ""
    n_value = join_components(
        [
            contact.family_name or "",
            contact.given_name or "",
            additional,
            contact.honorific_prefix or "",
            contact.honorific_suffix or "",
        ]
    )
    out.append(VCardProperty(name="N", value=n_value))

    if contact.nicknames:
        out.append(VCardProperty(name="NICKNAME", value=join_list(contact.nicknames)))

    if contact.phonetic_family_name or contact.phonetic_given_name:
        # Apple emits X-PHONETIC-FIRST-NAME / X-PHONETIC-LAST-NAME.
        if contact.phonetic_given_name:
            out.append(
                VCardProperty(
                    name="X-PHONETIC-FIRST-NAME",
                    value=escape_text(contact.phonetic_given_name),
                )
            )
        if contact.phonetic_family_name:
            out.append(
                VCardProperty(
                    name="X-PHONETIC-LAST-NAME",
                    value=escape_text(contact.phonetic_family_name),
                )
            )


def _emit_demographics(contact: CanonicalContact, out: list[VCardProperty]) -> None:
    if contact.birthday is not None:
        out.append(VCardProperty(name="BDAY", value=_format_date(contact.birthday)))
    if contact.anniversary is not None:
        out.append(VCardProperty(name="ANNIVERSARY", value=_format_date(contact.anniversary)))

    gender_value = _format_gender(contact.gender_identity, contact.gender_freeform)
    if gender_value:
        out.append(VCardProperty(name="GENDER", value=gender_value))

    if contact.pronouns:
        # No RFC 6350 vocabulary for pronouns — emit Apple-style X-PRONOUNS.
        out.append(VCardProperty(name="X-PRONOUNS", value=escape_text(contact.pronouns)))


def _emit_languages(contact: CanonicalContact, out: list[VCardProperty]) -> None:
    for lang in contact.preferred_languages:
        if lang:
            out.append(VCardProperty(name="LANG", value=escape_text(lang)))
    if contact.time_zone:
        out.append(VCardProperty(name="TZ", value=escape_text(contact.time_zone)))


def _emit_kind(contact: CanonicalContact, out: list[VCardProperty]) -> None:
    if contact.kind and contact.kind != "individual":
        out.append(VCardProperty(name="KIND", value=contact.kind))


def _emit_emails(emails: Iterable[CanonicalEmail], out: list[VCardProperty]) -> None:
    # Pref=1 is more preferred. Sort: primary first, then stable.
    sorted_emails = sorted(emails, key=lambda e: (not e.is_primary,))
    for index, email in enumerate(sorted_emails, start=1):
        if not email.address:
            continue
        prop = VCardProperty(name="EMAIL", value=escape_text(email.address))
        prop.set_param("TYPE", _EMAIL_TYPE_TO_VCARD.get(email.type, ["OTHER"]))
        if email.is_primary or index == 1:
            prop.set_param("PREF", ["1"])
        if email.label:
            prop.set_param("LABEL", [email.label])
        if email.pid is not None:
            prop.set_param("PID", [str(email.pid)])
        out.append(prop)


def _emit_phones(phones: Iterable[CanonicalPhone], out: list[VCardProperty]) -> None:
    sorted_phones = sorted(phones, key=lambda p: (not p.is_primary,))
    for index, phone in enumerate(sorted_phones, start=1):
        if not phone.e164:
            continue
        prop = VCardProperty(name="TEL", value=escape_text(phone.e164))
        type_tokens = list(_PHONE_TYPE_TO_VCARD.get(phone.type, ["VOICE"]))
        if phone.is_sms_capable and "TEXT" not in type_tokens:
            type_tokens.append("TEXT")
        prop.set_param("TYPE", type_tokens)
        if phone.is_primary or index == 1:
            prop.set_param("PREF", ["1"])
        # vCard 4.0 wants the value-type URI form `tel:` but Apple is
        # forgiving — we keep the plain E.164 to avoid double-prefixing.
        if phone.label:
            prop.set_param("LABEL", [phone.label])
        if phone.pid is not None:
            prop.set_param("PID", [str(phone.pid)])
        out.append(prop)


def _emit_addresses(addresses: Iterable[CanonicalAddress], out: list[VCardProperty]) -> None:
    sorted_addrs = sorted(addresses, key=lambda a: (not a.is_primary,))
    for index, addr in enumerate(sorted_addrs, start=1):
        # ADR is `po-box;ext;street;locality;region;postal;country` per RFC 6350 §6.3.1.
        adr_value = join_components(
            [
                addr.po_box or "",
                addr.extended_address or "",
                addr.street_address or "",
                addr.locality or "",
                addr.region or "",
                addr.postal_code or "",
                addr.country_name or addr.country_code or "",
            ]
        )
        prop = VCardProperty(name="ADR", value=adr_value)
        prop.set_param("TYPE", _ADDR_TYPE_TO_VCARD.get(addr.type, ["OTHER"]))
        if addr.is_primary or index == 1:
            prop.set_param("PREF", ["1"])
        if addr.label:
            prop.set_param("LABEL", [addr.label])
        if addr.pid is not None:
            prop.set_param("PID", [str(addr.pid)])
        out.append(prop)

        # Emit GEO when we have coords AND this address is the primary one.
        if (addr.is_primary or index == 1) and addr.geo_lat is not None and addr.geo_lng is not None:
            out.append(
                VCardProperty(
                    name="GEO",
                    value=f"geo:{addr.geo_lat},{addr.geo_lng}",
                )
            )


def _emit_urls_and_social(urls: Iterable[CanonicalUrl], out: list[VCardProperty]) -> None:
    for index, link in enumerate(urls, start=1):
        if not link.url:
            continue
        prop = VCardProperty(name="URL", value=escape_text(link.url))
        type_tokens = _URL_TYPE_TO_VCARD.get(link.type, [])
        if type_tokens:
            prop.set_param("TYPE", type_tokens)
        if link.is_primary or index == 1:
            prop.set_param("PREF", ["1"])
        if link.label:
            prop.set_param("LABEL", [link.label])
        if link.pid is not None:
            prop.set_param("PID", [str(link.pid)])
        out.append(prop)

        # Apple X-SOCIALPROFILE companion (recognized social networks only).
        social_prop = emit_x_socialprofile(link.url, label=link.label)
        if social_prop is not None:
            if link.is_primary or index == 1:
                social_prop.set_param("PREF", ["1"])
            out.append(social_prop)


def _emit_im_handles(handles: Iterable[CanonicalIM], out: list[VCardProperty]) -> None:
    for index, im in enumerate(handles, start=1):
        if not im.handle:
            continue
        value = _build_impp_uri(im.protocol, im.handle)
        prop = VCardProperty(name="IMPP", value=escape_text(value))
        prop.set_param("X-SERVICE-TYPE", [im.protocol])
        if im.is_primary or index == 1:
            prop.set_param("PREF", ["1"])
        if im.label:
            prop.set_param("LABEL", [im.label])
        if im.pid is not None:
            prop.set_param("PID", [str(im.pid)])
        out.append(prop)


def _emit_organizations(roles: Iterable[CanonicalOrgRole], out: list[VCardProperty]) -> None:
    primaries = [r for r in roles if r.is_current and r.is_primary]
    pick = primaries[0] if primaries else next(
        (r for r in roles if r.is_current), None
    )
    if pick is None:
        return
    org_value = join_components(
        [pick.org_display_name or "", pick.department or "", pick.section or ""]
    )
    out.append(VCardProperty(name="ORG", value=org_value))
    if pick.title:
        out.append(VCardProperty(name="TITLE", value=escape_text(pick.title)))
    if pick.role_type:
        out.append(VCardProperty(name="ROLE", value=escape_text(pick.role_type)))


def _emit_related(relations: Iterable[CanonicalRelation], out: list[VCardProperty]) -> None:
    for rel in relations:
        if not rel.related_uid:
            continue
        rel_type = RELATION_INTERNAL_TO_VCARD.get(rel.type, rel.type.lower())
        if rel_type not in RFC6350_RELATED_TYPES:
            continue
        prop = VCardProperty(name="RELATED", value=escape_text(rel.related_uid))
        prop.set_param("TYPE", [rel_type])
        prop.set_param("VALUE", ["uri"])
        out.append(prop)


def _emit_photo(contact: CanonicalContact, out: list[VCardProperty]) -> None:
    if contact.photo_inline_data:
        ct = contact.photo_inline_content_type or "image/jpeg"
        # vCard 4.0 §6.2.4 — PHOTO is a URI value. Use data: scheme.
        out.append(
            VCardProperty(
                name="PHOTO",
                value=f"data:{ct};base64,{contact.photo_inline_data}",
            )
        )
        return
    if contact.photo_url:
        out.append(VCardProperty(name="PHOTO", value=escape_text(contact.photo_url)))


def _emit_note(contact: CanonicalContact, out: list[VCardProperty]) -> None:
    if contact.note:
        out.append(VCardProperty(name="NOTE", value=escape_text(contact.note)))


def _emit_categories(contact: CanonicalContact, out: list[VCardProperty]) -> None:
    if contact.categories:
        out.append(VCardProperty(name="CATEGORIES", value=join_list(contact.categories)))


def _emit_rev(contact: CanonicalContact, out: list[VCardProperty]) -> None:
    if contact.rev is not None:
        out.append(VCardProperty(name="REV", value=_format_rev(contact.rev)))


def _emit_members(contact: CanonicalContact, out: list[VCardProperty]) -> None:
    if contact.kind != "group":
        return
    for uid in contact.members:
        if not uid:
            continue
        prop = VCardProperty(name="MEMBER", value=escape_text(uid))
        prop.set_param("VALUE", ["uri"])
        out.append(prop)


def _emit_clientpidmap(contact: CanonicalContact, out: list[VCardProperty]) -> None:
    if not contact.source_pid_map:
        return
    maps = [
        ClientPidMap(pid=int(k), source_uri=str(v))
        for k, v in contact.source_pid_map.items()
        if str(k).isdigit()
    ]
    out.extend(emit_clientpidmaps(maps))


# ---------- formatting helpers ----------


def _format_date(d: CanonicalDate) -> str:
    """RFC 6350 §4.3 — ``YYYYMMDD`` or ``--MMDD`` (year-omitted)."""

    if d.year is None:
        return f"--{d.month:02d}{d.day:02d}"
    return f"{d.year:04d}{d.month:02d}{d.day:02d}"


def _format_rev(value: datetime | date) -> str:
    """RFC 6350 §4.3 timestamp — ``20260522T143000Z`` (UTC, no separators)."""

    if isinstance(value, datetime):
        return value.strftime("%Y%m%dT%H%M%SZ")
    return value.strftime("%Y%m%d")


def _format_gender(identity: str | None, freeform: str | None) -> str | None:
    """Format the vCard 4.0 ``GENDER`` value.

    Form is ``sex[;identity]`` where sex ∈ {M,F,O,N,U} and identity is
    a free-form string (RFC 6350 §6.2.7).
    """

    if not identity and not freeform:
        return None

    sex_code: str | None = None
    if identity:
        sex_code = _GENDER_TO_VCARD.get(identity.lower().strip())
    if sex_code is None and freeform:
        # Try freeform-derived code; else fall back to U.
        sex_code = _GENDER_TO_VCARD.get(freeform.lower().strip(), "U")

    label = freeform or identity
    if label:
        return f"{sex_code or ''};{escape_text(label)}"
    return sex_code or "U"


def _build_impp_uri(protocol: str, handle: str) -> str:
    """Produce an IMPP URI per RFC 6350 §6.4.3 / iana protocol schemes."""

    proto = protocol.strip().lower()
    if not proto:
        return handle
    if handle.lower().startswith(f"{proto}:"):
        return handle
    return f"{proto}:{handle}"
