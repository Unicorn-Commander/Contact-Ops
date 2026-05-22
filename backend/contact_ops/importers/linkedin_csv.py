"""LinkedIn Connections.csv importer."""
# ruff: noqa: I001

from __future__ import annotations

import csv
from pathlib import Path

from contact_ops.importers.base import (
    CanonicalImportRecord,
    ImportEmail,
    ImportEmployment,
    ImportIdentifier,
    Importer,
    SourceKind,
    file_uri,
)


class LinkedInCSVImporter(Importer):
    def __init__(self, *, path: str | Path, batch_size: int = 50) -> None:
        self.path = Path(path).expanduser()
        super().__init__(source_uri=file_uri(self.path), batch_size=batch_size)

    @property
    def source_kind(self) -> SourceKind:
        return "linkedin_csv"

    async def records(self) -> list[CanonicalImportRecord]:
        with self.path.open(encoding="utf-8-sig", newline="") as handle:
            return [
                record_from_row(row, row_number=index)
                for index, row in enumerate(csv.DictReader(handle), start=2)
                if any((value or "").strip() for value in row.values())
            ]


def record_from_row(row: dict[str, str | None], *, row_number: int) -> CanonicalImportRecord:
    first = _get(row, "First Name")
    last = _get(row, "Last Name")
    url = _get(row, "URL")
    email = _get(row, "Email Address")
    company = _get(row, "Company")
    position = _get(row, "Position")
    connected_on = _get(row, "Connected On")
    display_name = (
        " ".join(part for part in [first, last] if part).strip()
        or url
        or "LinkedIn Connection"
    )
    return CanonicalImportRecord(
        source_record_id=url or f"linkedin-csv-row:{row_number}",
        display_name=display_name,
        given_name=first or None,
        family_name=last or None,
        headline=position or None,
        occupation_title=position or None,
        emails=[ImportEmail(address=email.lower(), type="work", is_primary=True)] if email else [],
        identifiers=[
            ImportIdentifier(namespace="linkedin.com", value=url, url=url, verified=False)
            for _ in [url]
            if url
        ],
        employments=[
            ImportEmployment(
                company=company,
                title=position or None,
                started_at=connected_on or None,
                is_primary=True,
            )
            for _ in [company]
            if company
        ],
        tags=["linkedin"],
        raw={key: value for key, value in row.items() if value},
    )


def _get(row: dict[str, str | None], key: str) -> str:
    return (row.get(key) or "").strip()
