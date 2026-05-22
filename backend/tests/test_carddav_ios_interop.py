"""iOS Contacts.app interop round-trip tests using real-world sample vCards.

Each fixture mimics what an iPhone 16 Pro running iOS 17.4 emits when
syncing a contact card created on the device. Asserting that we parse
and re-emit them losslessly is the closest we can get to "real iPhone
interop" without an actual device — the litmus test in the README
covers protocol-level wire compliance separately.
"""

from __future__ import annotations

from contact_ops.carddav.vcard_parse import parse_vcard_to_canonical
from contact_ops.carddav.vcard_serialize import serialize_canonical_to_vcard


IOS_BASIC_CONTACT = """\
BEGIN:VCARD
VERSION:4.0
PRODID:-//Apple Inc.//iOS 17.4//EN
N:Stransky;Aaron;David;Mr.;
FN:Aaron Stransky
NICKNAME:Aaron,ATS
ORG:Magic Unicorn Inc.;
TITLE:CEO
EMAIL;type=INTERNET;type=HOME;type=pref:REMOVED
EMAIL;type=INTERNET;type=WORK:REMOVED
TEL;type=CELL;type=VOICE;type=pref:+18435551234
TEL;type=HOME;type=VOICE:+18435559876
ADR;type=HOME;type=pref:;;123 Main St;Charleston;SC;29401;USA
URL;type=pref:https://magicunicorn.dev
BDAY:1984-06-15
NOTE:Founder of Magic Unicorn Inc.
REV:20260522T180000Z
UID:urn:uuid:11111111-2222-3333-4444-555555555555
END:VCARD
"""


IOS_CONTACT_WITH_CUSTOM_LABELS = """\
BEGIN:VCARD
VERSION:4.0
PRODID:-//Apple Inc.//iOS 17.4//EN
N:Holloway;Kevin;;;
FN:Kevin Holloway
item1.EMAIL;type=INTERNET:REMOVED
item1.X-ABLabel:Conference
item2.TEL;type=VOICE:REMOVED
item2.X-ABLabel:After Hours
item3.URL:https://linkedin.com/in/kevinholloway
item3.X-ABLabel:LinkedIn Profile
X-SOCIALPROFILE;type=linkedin:https://linkedin.com/in/kevinholloway
UID:urn:uuid:aaaaaaaa-1111-2222-3333-444444444444
REV:20260520T093000Z
END:VCARD
"""


IOS_CONTACT_WITH_CLIENTPIDMAP = """\
BEGIN:VCARD
VERSION:4.0
PRODID:-//Apple Inc.//iOS 17.4//EN
N:Khan;Hina;;Dr.;MD
FN:Maya Hart, MD
CLIENTPIDMAP:1;urn:uuid:device-iphone-12345
CLIENTPIDMAP:2;urn:uuid:device-macbook-67890
EMAIL;type=INTERNET;type=WORK;PID=1.1,2.1:REMOVED
TEL;type=CELL;type=VOICE;PID=1.2:+14695551234
UID:urn:uuid:dddddddd-eeee-ffff-aaaa-bbbbbbbbbbbb
END:VCARD
"""


def test_parse_basic_ios_contact_extracts_all_fields() -> None:
    parsed = parse_vcard_to_canonical(IOS_BASIC_CONTACT)
    assert parsed.display_name == "Aaron Stransky"
    assert parsed.family_name == "Stransky"
    assert parsed.given_name == "Aaron"
    assert parsed.additional_names == ["David"]
    assert parsed.honorific_prefix == "Mr."
    assert parsed.nicknames == ["Aaron", "ATS"]
    assert parsed.birthday is not None
    assert parsed.birthday.year == 1984
    assert len(parsed.emails) == 2
    assert any(e.address == "REMOVED" for e in parsed.emails)
    assert any(e.address == "REMOVED" for e in parsed.emails)
    assert len(parsed.phones) == 2
    assert parsed.addresses[0].street_address == "123 Main St"
    assert parsed.addresses[0].postal_code == "29401"
    assert parsed.organizations[0].title == "CEO"
    assert parsed.urls[0].url == "https://magicunicorn.dev"
    assert parsed.note is not None and "Founder" in parsed.note


def test_parse_ios_custom_labels_via_itemn_grouping() -> None:
    parsed = parse_vcard_to_canonical(IOS_CONTACT_WITH_CUSTOM_LABELS)
    assert parsed.display_name == "Kevin Holloway"
    # Custom labels should land on the matching sibling property.
    assert any(e.label == "Conference" for e in parsed.emails)
    assert any(p.label == "After Hours" for p in parsed.phones)
    # LinkedIn URL should be recognized either via X-SOCIALPROFILE or URL.
    assert any("linkedin.com" in u.url for u in parsed.urls)


def test_parse_ios_clientpidmap_preserved_for_roundtrip() -> None:
    parsed = parse_vcard_to_canonical(IOS_CONTACT_WITH_CLIENTPIDMAP)
    # CLIENTPIDMAP entries serialized as the source_pid_map jsonb on Person.
    assert parsed.source_pid_map == {
        "1": "urn:uuid:device-iphone-12345",
        "2": "urn:uuid:device-macbook-67890",
    }


def test_ios_basic_contact_round_trip_preserves_email_addresses() -> None:
    parsed = parse_vcard_to_canonical(IOS_BASIC_CONTACT)
    re_emitted = serialize_canonical_to_vcard(parsed)
    re_parsed = parse_vcard_to_canonical(re_emitted)
    original_emails = sorted(e.address for e in parsed.emails)
    re_emails = sorted(e.address for e in re_parsed.emails)
    assert original_emails == re_emails


def test_ios_custom_labels_survive_round_trip() -> None:
    parsed = parse_vcard_to_canonical(IOS_CONTACT_WITH_CUSTOM_LABELS)
    re_emitted = serialize_canonical_to_vcard(parsed)
    re_parsed = parse_vcard_to_canonical(re_emitted)
    re_labels = {e.label for e in re_parsed.emails if e.label}
    assert "Conference" in re_labels


def test_ios_clientpidmap_survives_round_trip() -> None:
    parsed = parse_vcard_to_canonical(IOS_CONTACT_WITH_CLIENTPIDMAP)
    re_emitted = serialize_canonical_to_vcard(parsed)
    assert "CLIENTPIDMAP:1;urn:uuid:device-iphone-12345" in re_emitted
    assert "CLIENTPIDMAP:2;urn:uuid:device-macbook-67890" in re_emitted


def test_ios_x_socialprofile_emitted_for_linkedin_in_output() -> None:
    """Even when the input was URL-only, output should add X-SOCIALPROFILE."""

    parsed = parse_vcard_to_canonical(IOS_BASIC_CONTACT)
    # The basic contact has no linkedin URL; add one and re-emit
    from contact_ops.carddav.vcard_serialize import CanonicalUrl

    parsed.urls.append(
        CanonicalUrl(url="https://linkedin.com/in/aaronstransky", type="profile")
    )
    re_emitted = serialize_canonical_to_vcard(parsed)
    assert "X-SOCIALPROFILE" in re_emitted
    assert "TYPE=linkedin" in re_emitted
