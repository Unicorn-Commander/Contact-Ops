from pathlib import Path

import pytest

from contact_ops.importers.linkedin_csv import LinkedInCSVImporter


@pytest.mark.asyncio
async def test_linkedin_csv_importer_maps_profile_identifier_and_employment() -> None:
    importer = LinkedInCSVImporter(path=Path("tests/fixtures/importers/linkedin_connections.csv"))
    records = await importer.records()
    assert len(records) == 1
    record = records[0]
    assert record.identifiers[0].namespace == "linkedin.com"
    assert record.identifiers[0].value == "https://www.linkedin.com/in/linlinked"
    assert record.employments[0].company == "Company Inc"
    assert record.employments[0].title == "VP Sales"
