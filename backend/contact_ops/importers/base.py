"""Shared importer types and base class."""
# ruff: noqa: I001

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SourceKind = Literal[
    "vcard",
    "google_csv",
    "linkedin_csv",
    "nextcloud",
    "icloud",
]

SOURCE_TYPE_BY_KIND: dict[SourceKind, str] = {
    "vcard": "ios_contacts_export",
    "google_csv": "google_contacts",
    "linkedin_csv": "linkedin",
    "nextcloud": "nextcloud",
    "icloud": "icloud",
}

SOURCE_RELIABILITY_BY_KIND: dict[SourceKind, float] = {
    "vcard": 0.9,
    "google_csv": 0.7,
    "linkedin_csv": 0.8,
    "nextcloud": 0.85,
    "icloud": 0.95,
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class ImportEmail(BaseModel):
    address: str
    type: Literal["personal", "work", "school", "other", "alias"] = "other"
    label: str | None = None
    is_primary: bool = False


class ImportPhone(BaseModel):
    e164: str
    type: Literal["mobile", "home", "work", "fax", "main", "other"] = "mobile"
    label: str | None = None
    is_primary: bool = False


class ImportAddress(BaseModel):
    type: Literal["home", "work", "billing", "shipping", "mailing", "other"] = "other"
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


class ImportIdentifier(BaseModel):
    namespace: str
    value: str
    url: str | None = None
    verified: bool = False


class ImportEmployment(BaseModel):
    company: str
    title: str | None = None
    department: str | None = None
    started_at: str | None = None
    is_primary: bool = True


class CanonicalImportRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_record_id: str
    display_name: str
    given_name: str | None = None
    family_name: str | None = None
    additional_names: list[str] = Field(default_factory=list)
    honorific_prefix: str | None = None
    honorific_suffix: str | None = None
    nicknames: list[str] = Field(default_factory=list)
    birthday: dict[str, int | None] | None = None
    notes: str | None = None
    headline: str | None = None
    occupation_title: str | None = None
    emails: list[ImportEmail] = Field(default_factory=list)
    phones: list[ImportPhone] = Field(default_factory=list)
    addresses: list[ImportAddress] = Field(default_factory=list)
    identifiers: list[ImportIdentifier] = Field(default_factory=list)
    employments: list[ImportEmployment] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    raw: dict[str, str | list[str] | None] = Field(default_factory=dict)

    def stable_identifiers(self) -> list[ImportIdentifier]:
        values: list[ImportIdentifier] = []
        values.extend(
            ImportIdentifier(namespace="email", value=email.address) for email in self.emails
        )
        values.extend(
            ImportIdentifier(namespace="phone", value=phone.e164) for phone in self.phones
        )
        values.extend(self.identifiers)
        if not values and self.birthday:
            key = (
                f"{self.display_name}|{self.birthday.get('year')}|"
                f"{self.birthday.get('month')}|{self.birthday.get('day')}"
            )
            values.append(ImportIdentifier(namespace="dob_name", value=key.lower()))
        return values


@dataclass(slots=True)
class ProvenanceContext:
    source_kind: SourceKind
    source_uri: str
    tenant_id: uuid.UUID | None = None
    observed_at: datetime = field(default_factory=utcnow)

    @property
    def source_type(self) -> str:
        return SOURCE_TYPE_BY_KIND[self.source_kind]

    @property
    def source_reliability_base(self) -> float:
        return SOURCE_RELIABILITY_BY_KIND[self.source_kind]

    def confidence(self, multiplier: float = 1.0) -> float:
        return min(1.0, max(0.0, self.source_reliability_base * multiplier))


@dataclass(slots=True)
class ImportStats:
    created: int = 0
    merged: int = 0
    candidates_logged: int = 0
    errors: int = 0
    skipped: int = 0  # individual fields skipped (e.g. an invalid phone) without losing the contact


@dataclass(slots=True)
class ImportResult:
    source: SourceKind
    source_uri: str
    total_records: int
    stats: ImportStats = field(default_factory=ImportStats)
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


class Importer(ABC):
    def __init__(
        self,
        *,
        source_uri: str,
        batch_size: int = 50,
        default_country: str = "US",
    ) -> None:
        self.source_uri = source_uri
        self.batch_size = batch_size
        self.default_country = default_country

    @property
    @abstractmethod
    def source_kind(self) -> SourceKind:
        raise NotImplementedError

    @abstractmethod
    async def records(self) -> list[CanonicalImportRecord]:
        raise NotImplementedError


def file_uri(path: str | Path) -> str:
    return Path(path).expanduser().resolve().as_uri()
