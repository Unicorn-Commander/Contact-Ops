"""Google Contacts Takeout CSV importer."""
# ruff: noqa: I001

from __future__ import annotations

import csv
from pathlib import Path

import phonenumbers
from phonenumbers import PhoneNumberFormat

from contact_ops.importers.base import (
    CanonicalImportRecord,
    ImportAddress,
    ImportEmail,
    ImportEmployment,
    ImportPhone,
    Importer,
    SourceKind,
    file_uri,
)


class GoogleCSVImporter(Importer):
    def __init__(
        self, *, path: str | Path, batch_size: int = 50, default_country: str = "US"
    ) -> None:
        self.path = Path(path).expanduser()
        super().__init__(
            source_uri=file_uri(self.path),
            batch_size=batch_size,
            default_country=default_country,
        )

    @property
    def source_kind(self) -> SourceKind:
        return "google_csv"

    async def records(self) -> list[CanonicalImportRecord]:
        with self.path.open(encoding="utf-8-sig", newline="") as handle:
            return [
                record_from_row(row, row_number=index, default_country=self.default_country)
                for index, row in enumerate(csv.DictReader(handle), start=2)
                if any((value or "").strip() for value in row.values())
            ]


def record_from_row(
    row: dict[str, str | None], *, row_number: int, default_country: str = "US"
) -> CanonicalImportRecord:
    first = _get(row, "First Name")
    middle = _get(row, "Middle Name")
    last = _get(row, "Last Name")
    display = _first_non_empty(
        _get(row, "Name"),
        _get(row, "File As"),
        " ".join(p for p in [first, middle, last] if p),
    )
    org = _get(row, "Organization Name")
    title = _get(row, "Organization Title")
    department = _get(row, "Organization Department")
    labels = _split_labels(_get(row, "Labels"))
    return CanonicalImportRecord(
        source_record_id=f"google-csv-row:{row_number}",
        display_name=display or "Unnamed Google Contact",
        given_name=first or None,
        family_name=last or None,
        additional_names=[middle] if middle else [],
        honorific_prefix=_get(row, "Name Prefix") or None,
        honorific_suffix=_get(row, "Name Suffix") or None,
        nicknames=[_get(row, "Nickname")] if _get(row, "Nickname") else [],
        birthday=_birthday(_get(row, "Birthday")),
        notes=_get(row, "Notes") or None,
        headline=title or None,
        occupation_title=title or None,
        emails=_emails(row),
        phones=_phones(row, default_country),
        addresses=_addresses(row),
        employments=[
            ImportEmployment(company=org, title=title or None, department=department or None)
            for _ in [org]
            if org
        ],
        tags=labels,
        raw={key: value for key, value in row.items() if value},
    )


def _get(row: dict[str, str | None], key: str) -> str:
    return (row.get(key) or "").strip()


def _first_non_empty(*values: str) -> str:
    for value in values:
        if value.strip():
            return value.strip()
    return ""


def _emails(row: dict[str, str | None]) -> list[ImportEmail]:
    items: list[ImportEmail] = []
    for index in range(1, 100):
        value = _get(row, f"E-mail {index} - Value")
        if not value:
            break
        items.append(
            ImportEmail(
                address=value.lower(),
                type=_email_type(_get(row, f"E-mail {index} - Label")),
                label=_get(row, f"E-mail {index} - Label") or None,
                is_primary=index == 1,
            )
        )
    return items


def _phones(row: dict[str, str | None], default_country: str) -> list[ImportPhone]:
    items: list[ImportPhone] = []
    for index in range(1, 100):
        value = _get(row, f"Phone {index} - Value")
        if not value:
            break
        normalized = _normalize_phone(value, default_country)
        if normalized is None:
            continue
        items.append(
            ImportPhone(
                e164=normalized,
                type=_phone_type(_get(row, f"Phone {index} - Label")),
                label=_get(row, f"Phone {index} - Label") or None,
                is_primary=index == 1,
            )
        )
    return items


def _addresses(row: dict[str, str | None]) -> list[ImportAddress]:
    items: list[ImportAddress] = []
    for index in range(1, 100):
        street = _get(row, f"Address {index} - Street")
        city = _get(row, f"Address {index} - City")
        region = _get(row, f"Address {index} - Region")
        postal = _get(row, f"Address {index} - Postal Code")
        country = _get(row, f"Address {index} - Country")
        extended = _get(row, f"Address {index} - Extended Address")
        if not any([street, city, region, postal, country, extended]):
            break
        items.append(
            ImportAddress(
                type=_address_type(_get(row, f"Address {index} - Label")),
                label=_get(row, f"Address {index} - Label") or None,
                is_primary=index == 1,
                street_address=street or None,
                extended_address=extended or None,
                locality=city or None,
                region=region or None,
                postal_code=postal or None,
                country_name=country or None,
            )
        )
    return items


def _normalize_phone(value: str, default_country: str) -> str | None:
    try:
        parsed = phonenumbers.parse(value, default_country)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)


def _email_type(label: str) -> str:
    label = label.lower()
    if "work" in label:
        return "work"
    if "school" in label:
        return "school"
    if "home" in label or "personal" in label:
        return "personal"
    return "other"


def _phone_type(label: str) -> str:
    label = label.lower()
    if "mobile" in label or "cell" in label:
        return "mobile"
    if "home" in label:
        return "home"
    if "work" in label:
        return "work"
    if "fax" in label:
        return "fax"
    return "other"


def _address_type(label: str) -> str:
    label = label.lower()
    if "home" in label:
        return "home"
    if "work" in label:
        return "work"
    return "other"


def _split_labels(value: str) -> list[str]:
    # Google Contacts CSV joins multiple labels with " ::: " and prefixes its own
    # system labels with "* " (e.g. "* myContacts", "* starred"). Split on that
    # separator first, drop the system labels, and tolerate comma/semicolon exports.
    if not value:
        return []
    normalized = value.replace(" ::: ", ",").replace(":::", ",").replace(";", ",")
    out: list[str] = []
    for part in normalized.split(","):
        cleaned = part.strip()
        if not cleaned or cleaned.startswith("* "):
            continue
        out.append(cleaned)
    return out


def _birthday(value: str) -> dict[str, int | None] | None:
    if not value:
        return None
    parts = value.replace("/", "-").split("-")
    try:
        if len(parts) == 3:
            return {"year": int(parts[0]), "month": int(parts[1]), "day": int(parts[2])}
        if len(parts) == 2:
            return {"year": None, "month": int(parts[0]), "day": int(parts[1])}
    except ValueError:
        return None
    return None
