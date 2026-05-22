from pathlib import Path

import pytest

from contact_ops.importers.google_csv import GoogleCSVImporter


@pytest.mark.asyncio
async def test_google_csv_importer_normalizes_phone_and_labels() -> None:
    importer = GoogleCSVImporter(path=Path("tests/fixtures/importers/google_contacts.csv"))
    records = await importer.records()
    assert len(records) == 1
    record = records[0]
    assert record.display_name == "Alice Google"
    assert record.phones[0].e164 == "+14155550123"
    assert record.emails[0].type == "work"
    assert record.tags == ["Coworker", "Google"]
