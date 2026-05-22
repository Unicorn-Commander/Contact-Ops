"""vCard 4.0 text → :class:`CanonicalContact`.

Parsing is intentionally forgiving: we accept vCard 3.0 inputs from
older clients by treating their differences (X-ANNIVERSARY, X-GENDER,
ITEM<n>.X-ABLABEL grouping, X-SOCIALPROFILE) the same way we emit
them on output. The parser never raises on malformed properties — they
are dropped with a structured warning so the addressbook handler can
log them.

Mapping mirrors :mod:`contact_ops.carddav.vcard_serialize` so that a
serialize → parse round-trip is byte-stable on the canonical fields.
"""

from __future__ import annotations

import base64
import re
import warnings
from datetime import datetime, timezone

from contact_ops.carddav.apple_quirks import (
    VCardProperty,
    collapse_item_groups,
    extract_clientpidmaps,
    serialize_clientpidmaps_to_jsonb,
)
from contact_ops.carddav.vcard_lines import (
    parse_vcard,
    split_components,
    split_list,
    unescape_text,
)
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


_BDAY_FULL_RE = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})$")
_BDAY_NOYEAR_RE = re.compile(r"^--(\d{2})-?(\d{2})$")
_REV_RE = re.compile(
    r"^(\d{4})-?(\d{2})-?(\d{2})T(\d{2}):?(\d{2}):?(\d{2})Z?$"
)
_DATA_URI_RE = re.compile(r"^data:([^;]+);base64,(.*)$", re.DOTALL)


_PHONE_VCARD_TO_TYPE = {
    "CELL": "mobile",
    "MOBILE": "mobile",
    "HOME": "home",
    "WORK": "work",
    "FAX": "fax",
    "MAIN": "main",
    "OTHER": "other",
}

_EMAIL_VCARD_TO_TYPE = {
    "HOME": "personal",
    "WORK": "work",
    "INTERNET": "other",
    "OTHER": "other",
}

_ADDR_VCARD_TO_TYPE = {
    "HOME": "home",
    "WORK": "work",
    "POSTAL": "mailing",
    "OTHER": "other",
}


def parse_vcard_to_canonical(text: str) -> CanonicalContact:
    """Parse a single vCard document into a :class:`CanonicalContact`.

    Skips properties we don't recognize but preserves their CLIENTPIDMAP
    pid bindings so multi-source merge data round-trips.
    """

    raw_properties = parse_vcard(text)
    # Collapse Apple ITEMn.X-ABLABEL into LABEL params on sibling props first.
    properties = collapse_item_groups(raw_properties)

    by_name: dict[str, list[VCardProperty]] = {}
    for prop in properties:
        by_name.setdefault(prop.name.upper(), []).append(prop)

    fn = _scalar(by_name, "FN", default="")
    uid = _scalar(by_name, "UID", default="").strip()
    if not uid:
        warnings.warn("vcard missing UID; downstream caller must assign one")

    n_value = _scalar(by_name, "N", default="")
    family, given, additional, prefix, suffix = _parse_n(n_value)

    nicknames = []
    if "NICKNAME" in by_name:
        nicknames = [v for v in split_list(by_name["NICKNAME"][0].value) if v]
        nicknames = [unescape_text(n) for n in nicknames]

    bday = _parse_date(_scalar(by_name, "BDAY"))
    anniversary = _parse_date(
        _scalar(by_name, "ANNIVERSARY") or _scalar(by_name, "X-ANNIVERSARY")
    )

    gender_identity, gender_freeform = _parse_gender(
        _scalar(by_name, "GENDER") or _scalar(by_name, "X-GENDER")
    )
    pronouns = _scalar(by_name, "X-PRONOUNS")

    languages = [unescape_text(p.value) for p in by_name.get("LANG", []) if p.value]
    time_zone = _scalar(by_name, "TZ") or None

    kind_value = _scalar(by_name, "KIND", default="individual").lower()
    if kind_value not in {"individual", "group", "org", "location"}:
        kind_value = "individual"

    note = _scalar(by_name, "NOTE") or None

    categories: list[str] = []
    if "CATEGORIES" in by_name:
        categories = [unescape_text(t).strip() for t in split_list(by_name["CATEGORIES"][0].value)]
        categories = [c for c in categories if c]

    rev = _parse_rev(_scalar(by_name, "REV"))

    photo_data, photo_ct, photo_url = _parse_photo(by_name.get("PHOTO", []))
    members = _parse_members(by_name.get("MEMBER", []))

    pid_maps = extract_clientpidmaps(properties)
    source_pid_map = serialize_clientpidmaps_to_jsonb(pid_maps)

    emails = _parse_emails(by_name.get("EMAIL", []))
    phones = _parse_phones(by_name.get("TEL", []))
    addresses = _parse_addresses(by_name.get("ADR", []), by_name.get("GEO", []))
    urls = _parse_urls(by_name.get("URL", []), by_name.get("X-SOCIALPROFILE", []))
    im_handles = _parse_impps(by_name.get("IMPP", []))
    org_roles = _parse_org(by_name)
    relations = _parse_related(by_name.get("RELATED", []))

    phonetic_first = _scalar(by_name, "X-PHONETIC-FIRST-NAME") or None
    phonetic_last = _scalar(by_name, "X-PHONETIC-LAST-NAME") or None

    return CanonicalContact(
        vcard_uid=uid,
        display_name=unescape_text(fn) if fn else "",
        family_name=family or None,
        given_name=given or None,
        additional_names=additional,
        honorific_prefix=prefix or None,
        honorific_suffix=suffix or None,
        nicknames=nicknames,
        phonetic_family_name=phonetic_last,
        phonetic_given_name=phonetic_first,
        birthday=bday,
        anniversary=anniversary,
        gender_identity=gender_identity,
        gender_freeform=gender_freeform,
        pronouns=pronouns,
        preferred_languages=languages,
        time_zone=time_zone,
        note=note,
        categories=categories,
        kind=kind_value,  # type: ignore[arg-type]
        members=members,
        rev=rev,
        photo_inline_data=photo_data,
        photo_inline_content_type=photo_ct,
        photo_url=photo_url,
        emails=emails,
        phones=phones,
        urls=urls,
        addresses=addresses,
        im_handles=im_handles,
        organizations=org_roles,
        related=relations,
        source_pid_map=source_pid_map,
    )


# ---------- scalar helpers ----------


def _scalar(by_name: dict[str, list[VCardProperty]], key: str, default: str = "") -> str:
    """Return the first property's raw value (no unescape) or default."""

    props = by_name.get(key.upper())
    if not props:
        return default
    return props[0].value


def _parse_n(value: str) -> tuple[str, str, list[str], str, str]:
    if not value:
        return "", "", [], "", ""
    parts = split_components(value)
    while len(parts) < 5:
        parts.append("")
    family = unescape_text(parts[0]).strip()
    given = unescape_text(parts[1]).strip()
    additional = [unescape_text(x).strip() for x in split_list(parts[2]) if x.strip()]
    prefix = unescape_text(parts[3]).strip()
    suffix = unescape_text(parts[4]).strip()
    return family, given, additional, prefix, suffix


def _parse_date(value: str | None) -> CanonicalDate | None:
    if not value:
        return None
    cleaned = value.strip()
    full = _BDAY_FULL_RE.match(cleaned)
    if full:
        y, mo, d = (int(g) for g in full.groups())
        # iOS 3.0-downgrade convention: year 1604 = "year unknown".
        return CanonicalDate(year=None if y == 1604 else y, month=mo, day=d)
    noyear = _BDAY_NOYEAR_RE.match(cleaned)
    if noyear:
        mo, d = (int(g) for g in noyear.groups())
        return CanonicalDate(year=None, month=mo, day=d)
    return None


def _parse_rev(value: str | None) -> datetime | None:
    if not value:
        return None
    m = _REV_RE.match(value.strip())
    if not m:
        return None
    y, mo, d, hh, mm, ss = (int(g) for g in m.groups())
    return datetime(y, mo, d, hh, mm, ss, tzinfo=timezone.utc)


def _parse_gender(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    parts = split_components(value)
    sex_code = unescape_text(parts[0]).strip().upper() if parts else ""
    freeform = unescape_text(parts[1]).strip() if len(parts) > 1 else None

    identity_map = {
        "M": "male",
        "F": "female",
        "O": "other",
        "N": "none",
        "U": "unknown",
    }
    identity = identity_map.get(sex_code)
    return identity, freeform or None


def _parse_photo(properties: list[VCardProperty]) -> tuple[str | None, str | None, str | None]:
    if not properties:
        return None, None, None
    raw_value = properties[0].value.strip()
    m = _DATA_URI_RE.match(raw_value)
    if m:
        ct = m.group(1)
        b64 = re.sub(r"\s+", "", m.group(2))
        # Validate it round-trips so we don't store garbage; on failure, drop.
        try:
            base64.b64decode(b64, validate=True)
        except Exception:
            return None, None, None
        return b64, ct, None
    return None, None, unescape_text(raw_value)


def _parse_members(properties: list[VCardProperty]) -> list[str]:
    return [unescape_text(p.value).strip() for p in properties if p.value.strip()]


# ---------- multi-row property parsers ----------


def _parse_emails(properties: list[VCardProperty]) -> list[CanonicalEmail]:
    out: list[CanonicalEmail] = []
    for prop in properties:
        address = unescape_text(prop.value).strip().strip("<>")
        if not address:
            continue
        type_tokens = [t.upper() for t in prop.get_param("TYPE")]
        type_value = next(
            (
                _EMAIL_VCARD_TO_TYPE[t]
                for t in type_tokens
                if t in _EMAIL_VCARD_TO_TYPE
            ),
            "other",
        )
        label = _label_from_prop(prop)
        pid = _pid_from_prop(prop)
        is_primary = _pref_from_prop(prop) == 1 or "PREF" in [
            t.upper() for t in type_tokens
        ]
        out.append(
            CanonicalEmail(
                address=address.lower(),
                type=type_value,
                label=label,
                is_primary=is_primary,
                pid=pid,
            )
        )
    return out


def _parse_phones(properties: list[VCardProperty]) -> list[CanonicalPhone]:
    out: list[CanonicalPhone] = []
    for prop in properties:
        raw = unescape_text(prop.value).strip()
        if raw.lower().startswith("tel:"):
            raw = raw[4:]
        if not raw:
            continue
        type_tokens = [t.upper() for t in prop.get_param("TYPE")]
        phone_type = next(
            (_PHONE_VCARD_TO_TYPE[t] for t in type_tokens if t in _PHONE_VCARD_TO_TYPE),
            "other",
        )
        is_sms = "TEXT" in type_tokens or "SMS" in type_tokens
        label = _label_from_prop(prop)
        pid = _pid_from_prop(prop)
        is_primary = _pref_from_prop(prop) == 1 or "PREF" in type_tokens
        out.append(
            CanonicalPhone(
                e164=raw,
                type=phone_type,
                label=label,
                is_primary=is_primary,
                is_sms_capable=is_sms,
                pid=pid,
            )
        )
    return out


def _parse_addresses(
    adr_properties: list[VCardProperty],
    geo_properties: list[VCardProperty],
) -> list[CanonicalAddress]:
    out: list[CanonicalAddress] = []
    geo_lat: float | None = None
    geo_lng: float | None = None
    if geo_properties:
        geo_lat, geo_lng = _parse_geo(geo_properties[0].value)

    for index, prop in enumerate(adr_properties):
        parts = split_components(prop.value)
        while len(parts) < 7:
            parts.append("")
        po_box, ext, street, locality, region, postal, country = (
            unescape_text(parts[0]).strip(),
            unescape_text(parts[1]).strip(),
            unescape_text(parts[2]).strip(),
            unescape_text(parts[3]).strip(),
            unescape_text(parts[4]).strip(),
            unescape_text(parts[5]).strip(),
            unescape_text(parts[6]).strip(),
        )
        type_tokens = [t.upper() for t in prop.get_param("TYPE")]
        addr_type = next(
            (_ADDR_VCARD_TO_TYPE[t] for t in type_tokens if t in _ADDR_VCARD_TO_TYPE),
            "other",
        )
        label = _label_from_prop(prop)
        pid = _pid_from_prop(prop)
        is_primary = _pref_from_prop(prop) == 1 or "PREF" in type_tokens
        addr = CanonicalAddress(
            type=addr_type,
            label=label,
            is_primary=is_primary,
            po_box=po_box or None,
            extended_address=ext or None,
            street_address=street or None,
            locality=locality or None,
            region=region or None,
            postal_code=postal or None,
            country_name=country or None,
            pid=pid,
        )
        if index == 0 or is_primary:
            addr.geo_lat = geo_lat
            addr.geo_lng = geo_lng
        out.append(addr)
    return out


def _parse_geo(value: str) -> tuple[float | None, float | None]:
    cleaned = value.strip()
    if cleaned.lower().startswith("geo:"):
        cleaned = cleaned[4:]
    if "," not in cleaned:
        return None, None
    try:
        lat_s, lng_s = cleaned.split(",", 1)
        return float(lat_s.strip()), float(lng_s.strip())
    except ValueError:
        return None, None


def _parse_urls(
    url_properties: list[VCardProperty],
    social_properties: list[VCardProperty],
) -> list[CanonicalUrl]:
    out: list[CanonicalUrl] = []
    seen: set[str] = set()

    for index, prop in enumerate(url_properties):
        href = unescape_text(prop.value).strip()
        if not href:
            continue
        seen.add(href.lower())
        type_tokens = [t.lower() for t in prop.get_param("TYPE")]
        kind = "homepage" if "home" in type_tokens else (
            "work" if "work" in type_tokens else "profile"
        )
        is_primary = _pref_from_prop(prop) == 1 or "pref" in type_tokens
        out.append(
            CanonicalUrl(
                url=href,
                type=kind,
                label=_label_from_prop(prop),
                is_primary=is_primary or index == 0,
                pid=_pid_from_prop(prop),
            )
        )

    # X-SOCIALPROFILE entries that aren't already covered by a URL line.
    for prop in social_properties:
        href = unescape_text(prop.value).strip()
        if not href or href.lower() in seen:
            continue
        seen.add(href.lower())
        out.append(
            CanonicalUrl(
                url=href,
                type="profile",
                label=_label_from_prop(prop),
                is_primary=False,
                pid=_pid_from_prop(prop),
            )
        )
    return out


def _parse_impps(properties: list[VCardProperty]) -> list[CanonicalIM]:
    out: list[CanonicalIM] = []
    for prop in properties:
        raw = unescape_text(prop.value).strip()
        if not raw:
            continue
        service = prop.get_param("X-SERVICE-TYPE")
        protocol = service[0].lower() if service else raw.split(":", 1)[0].lower()
        handle = raw.split(":", 1)[1] if ":" in raw else raw
        out.append(
            CanonicalIM(
                protocol=protocol,
                handle=handle,
                label=_label_from_prop(prop),
                is_primary=_pref_from_prop(prop) == 1,
                pid=_pid_from_prop(prop),
            )
        )
    return out


def _parse_org(by_name: dict[str, list[VCardProperty]]) -> list[CanonicalOrgRole]:
    if "ORG" not in by_name:
        return []
    parts = split_components(by_name["ORG"][0].value)
    while len(parts) < 3:
        parts.append("")
    org_name = unescape_text(parts[0]).strip()
    department = unescape_text(parts[1]).strip() or None
    section = unescape_text(parts[2]).strip() or None
    title = unescape_text(_scalar(by_name, "TITLE")).strip() or None
    role_type = unescape_text(_scalar(by_name, "ROLE")).strip() or None
    if not org_name and not title:
        return []
    return [
        CanonicalOrgRole(
            org_display_name=org_name,
            department=department,
            section=section,
            title=title,
            role_type=role_type,
            is_current=True,
            is_primary=True,
        )
    ]


def _parse_related(properties: list[VCardProperty]) -> list[CanonicalRelation]:
    from contact_ops.carddav.vcard_serialize import (
        RELATION_INTERNAL_TO_VCARD,
        RFC6350_RELATED_TYPES,
    )

    # invert the map: vCard type → first matching internal type
    inverse = {v: k for k, v in RELATION_INTERNAL_TO_VCARD.items()}

    out: list[CanonicalRelation] = []
    for prop in properties:
        href = unescape_text(prop.value).strip()
        if not href:
            continue
        type_tokens = [t.lower() for t in prop.get_param("TYPE")]
        rel_type: str | None = None
        for token in type_tokens:
            if token in RFC6350_RELATED_TYPES:
                rel_type = inverse.get(token, token)
                break
        if rel_type is None:
            rel_type = "knows"
        out.append(CanonicalRelation(related_uid=href, type=rel_type))
    return out


def _label_from_prop(prop: VCardProperty) -> str | None:
    label_param = prop.get_param("LABEL")
    if label_param:
        text = label_param[0].strip().strip('"').strip()
        return text or None
    return None


def _pid_from_prop(prop: VCardProperty) -> int | None:
    pid_param = prop.get_param("PID")
    if not pid_param:
        return None
    try:
        return int(str(pid_param[0]).split(".", 1)[0])
    except (TypeError, ValueError):
        return None


def _pref_from_prop(prop: VCardProperty) -> int | None:
    pref_param = prop.get_param("PREF")
    if not pref_param:
        return None
    try:
        return int(pref_param[0])
    except (TypeError, ValueError):
        return None
