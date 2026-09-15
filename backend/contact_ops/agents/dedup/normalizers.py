from __future__ import annotations

import csv
from pathlib import Path

import phonenumbers
import usaddress

_GMAIL_DOMAINS: frozenset[str] = frozenset({
    "gmail.com",
    "googlemail.com",
    "gnail.com",
    "gmale.com",
    "gemail.com",
    "gmil.com",
    "gmial.com",
    "gamil.com",
    "gmai.com",
    "gmail.con",
    "gmaiil.com",
    "gmaill.com",
    "gmaail.com",
    "gogglemail.com",
})

_NICKNAME_CACHE: frozenset[tuple[str, str]] | None = None
_NICKNAME_FILE = Path(__file__).resolve().parent / "data" / "carltonnorthern_nicknames.csv"


def _load_nicknames() -> frozenset[tuple[str, str]]:
    global _NICKNAME_CACHE
    if _NICKNAME_CACHE is not None:
        return _NICKNAME_CACHE
    pairs: set[tuple[str, str]] = set()
    with _NICKNAME_FILE.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("relationship") == "has_nickname":
                pairs.add((row["name1"].strip().lower(), row["name2"].strip().lower()))
    _NICKNAME_CACHE = frozenset(pairs)
    return _NICKNAME_CACHE


def normalize_email(email: str | None) -> str | None:
    if email is None:
        return None
    email = email.strip().lower()
    if "@" not in email:
        return None
    local, _, domain = email.partition("@")
    if domain in _GMAIL_DOMAINS:
        domain = "gmail.com"
        local = local.replace(".", "")
        plus_idx = local.find("+")
        if plus_idx != -1:
            local = local[:plus_idx]
    return f"{local}@{domain}"


def normalize_phone_e164(phone: str | None, *, region: str = "US") -> str | None:
    if phone is None:
        return None
    try:
        parsed = phonenumbers.parse(phone, region)
        if not phonenumbers.is_valid_number(parsed):
            return None
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        return None


def normalize_address(address: str | None) -> dict | None:
    if address is None:
        return None
    try:
        parsed, _ = usaddress.tag(address)
    except Exception:
        return None
    return {
        "street_number": parsed.get("AddressNumber"),
        "street_name": parsed.get("StreetName"),
        "street_name_suffix": parsed.get("StreetNamePostType"),
        "secondary_designator": parsed.get("OccupancyType"),
        "secondary_number": parsed.get("OccupancyIdentifier"),
        "locality": parsed.get("PlaceName"),
        "region_name": parsed.get("StateName"),
        "postal_code": parsed.get("ZipCode"),
    }


def extract_vcard_uid(source_pid_map: dict | None) -> str | None:
    if source_pid_map is None:
        return None
    for key in ("ios", "icloud", "apple"):
        val = source_pid_map.get(key)
        if isinstance(val, dict):
            uid = val.get("uid")
            if isinstance(uid, str):
                return uid
    return None


def apply_nickname_map(given_name: str | None) -> list[str]:
    if given_name is None:
        return []
    name = given_name.strip().lower()
    nicknames = _load_nicknames()
    result: list[str] = [name]
    for n1, n2 in nicknames:
        if n1 == name:
            result.append(n2.lower())
    seen: set[str] = set()
    deduped: list[str] = []
    for item in result:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def name_variants(
    given_name: str | None,
    family_name: str | None,
    nicknames: list[str] | None = None,
    person_names: list[str] | None = None,
) -> list[str]:
    """Every name string a person can be matched on, lowercased + deduped.

    Includes the given/family name, the stored `persons.nicknames`, and any
    typed `person_names` values (legal/preferred/nickname/aka/former/maiden/
    stage). Used by search + dedup so a contact known only by a nickname
    (e.g. "Diesel") still matches a later record that carries it as a given or
    full name.
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(value: str | None) -> None:
        if value is None:
            return
        for token in str(value).strip().lower().split():
            if token and token not in seen:
                seen.add(token)
                out.append(token)

    add(given_name)
    add(family_name)
    for n in nicknames or []:
        add(n)
    for n in person_names or []:
        add(n)
    return out


def names_share_variant(a: list[str], b: list[str]) -> bool:
    """True when two name-variant lists overlap (any shared name token)."""
    return bool(set(a) & set(b))
