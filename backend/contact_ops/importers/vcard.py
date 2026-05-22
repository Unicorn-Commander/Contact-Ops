"""vCard importers for iOS export and CardDAV sources."""
# ruff: noqa: I001

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from contact_ops.carddav.vcard_parse import parse_vcard_to_canonical
from contact_ops.carddav.vcard_serialize import CanonicalAddress, CanonicalContact
from contact_ops.importers.base import (
    CanonicalImportRecord,
    ImportAddress,
    ImportEmail,
    ImportEmployment,
    ImportIdentifier,
    ImportPhone,
    Importer,
    SourceKind,
    file_uri,
)

_VCARD_BLOCK_RE = re.compile(r"BEGIN:VCARD.*?END:VCARD", re.DOTALL | re.IGNORECASE)


def split_vcards(text: str) -> list[str]:
    return [match.group(0).strip() for match in _VCARD_BLOCK_RE.finditer(text)]


def record_from_vcard_text(
    text: str, *, source_record_id: str | None = None
) -> CanonicalImportRecord:
    return record_from_canonical(
        parse_vcard_to_canonical(text),
        source_record_id=source_record_id or _record_id(text),
    )


def record_from_canonical(
    contact: CanonicalContact, *, source_record_id: str | None = None
) -> CanonicalImportRecord:
    display_name = (
        contact.display_name
        or " ".join(part for part in [contact.given_name, contact.family_name] if part)
        or contact.vcard_uid
        or "Unnamed Contact"
    )
    birthday = None
    if contact.birthday is not None:
        birthday = {
            "year": contact.birthday.year,
            "month": contact.birthday.month,
            "day": contact.birthday.day,
        }
    return CanonicalImportRecord(
        source_record_id=source_record_id or contact.vcard_uid or display_name,
        display_name=display_name,
        given_name=contact.given_name,
        family_name=contact.family_name,
        additional_names=list(contact.additional_names),
        honorific_prefix=contact.honorific_prefix,
        honorific_suffix=contact.honorific_suffix,
        nicknames=list(contact.nicknames),
        birthday=birthday,
        notes=contact.note,
        emails=[
            ImportEmail(
                address=email.address.strip().lower(),
                type=_email_type(email.type),
                label=email.label,
                is_primary=email.is_primary,
            )
            for email in contact.emails
            if email.address
        ],
        phones=[
            ImportPhone(
                e164=phone.e164,
                type=_phone_type(phone.type),
                label=phone.label,
                is_primary=phone.is_primary,
            )
            for phone in contact.phones
            if phone.e164
        ],
        addresses=[_address(addr) for addr in contact.addresses],
        identifiers=[
            ImportIdentifier(namespace="vcard_uid", value=contact.vcard_uid)
            for _ in [contact.vcard_uid]
            if contact.vcard_uid
        ],
        employments=[
            ImportEmployment(
                company=role.org_display_name,
                title=role.title,
                department=role.department,
                is_primary=role.is_primary,
            )
            for role in contact.organizations
            if role.org_display_name
        ],
        tags=list(contact.categories),
        raw={
            "vcard_uid": contact.vcard_uid,
            "rev": contact.rev.isoformat() if contact.rev else None,
        },
    )


class VCardImporter(Importer):
    def __init__(self, *, path: str | Path, batch_size: int = 50) -> None:
        self.path = Path(path).expanduser()
        super().__init__(source_uri=file_uri(self.path), batch_size=batch_size)

    @property
    def source_kind(self) -> SourceKind:
        return "vcard"

    async def records(self) -> list[CanonicalImportRecord]:
        text = self.path.read_text(encoding="utf-8-sig")
        blocks = split_vcards(text)
        return [record_from_vcard_text(block) for block in blocks]


def _record_id(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode()).hexdigest()}"


def _email_type(value: str | None) -> str:
    if value in {"personal", "work", "school", "other", "alias"}:
        return value
    if value == "home":
        return "personal"
    return "other"


def _phone_type(value: str | None) -> str:
    if value in {"mobile", "home", "work", "fax", "main", "other"}:
        return value
    return "other"


def _address(item: CanonicalAddress) -> ImportAddress:
    address_type = (
        item.type if item.type in {"home", "work", "billing", "shipping", "mailing"} else "other"
    )
    return ImportAddress(
        type=address_type,
        label=item.label,
        is_primary=item.is_primary,
        po_box=item.po_box,
        extended_address=item.extended_address,
        street_address=item.street_address,
        locality=item.locality,
        region=item.region,
        postal_code=item.postal_code,
        country_name=item.country_name,
        country_code=item.country_code,
    )
