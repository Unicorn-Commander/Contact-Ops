from pathlib import Path

import pytest

from contact_ops.importers.vcard import VCardImporter, split_vcards


@pytest.mark.asyncio
async def test_vcard_importer_parses_concatenated_ios_export() -> None:
    path = Path("tests/fixtures/importers/sample.vcf")
    records = await VCardImporter(path=path).records()
    assert len(records) == 2
    assert records[0].display_name == "Jane Example"
    assert records[0].emails[0].address == "REMOVED"
    assert records[0].phones[0].e164 == "+14155550100"
    assert records[0].tags == ["Friends", "Test"]
    assert len(split_vcards(path.read_text())) == 2
