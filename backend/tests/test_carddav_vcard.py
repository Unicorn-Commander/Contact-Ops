"""End-to-end vCard serialize ↔ parse round-trip tests."""

from __future__ import annotations

from datetime import datetime, timezone

from contact_ops.carddav.vcard_lines import (
    escape_text,
    fold,
    join_components,
    join_list,
    parse_property_line,
    serialize_property,
    split_components,
    split_list,
    unescape_text,
    unfold,
)
from contact_ops.carddav.vcard_parse import parse_vcard_to_canonical
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
    serialize_canonical_to_vcard,
)


# ---------- line primitives ----------


def test_escape_unescape_roundtrip() -> None:
    samples = [
        "Plain text",
        "Has, comma",
        "Has; semicolon",
        "Has\nnewline",
        "Has\\backslash",
        "Has \"quote\" mark",
    ]
    for s in samples:
        assert unescape_text(escape_text(s)) == s


def test_unfold_handles_crlf_and_lf() -> None:
    # Per RFC 6350 §3.2, CRLF + a single LWSP char is the entire continuation
    # marker and gets removed wholesale. The space is part of the marker, not
    # the content — to preserve a real space across a fold, callers must
    # encode TWO spaces ("Hello \n  World" -> "Hello World").
    folded = "BEGIN:VCARD\r\nFN:Hello\r\n World\r\nEND:VCARD"
    assert "HelloWorld" in unfold(folded)


def test_fold_keeps_short_lines_intact() -> None:
    short = "FN:Short Name"
    assert fold(short) == short


def test_fold_wraps_long_lines_with_space_continuation() -> None:
    long_line = "X-VERY-LONG-PROPERTY:" + "x" * 200
    folded = fold(long_line)
    lines = folded.split("\r\n")
    assert len(lines) > 1
    for line in lines[1:]:
        assert line.startswith(" ")


def test_split_components_honors_escaped_semicolons() -> None:
    raw = r"PO Box;Suite\;5;123 Main\;St;Charleston;SC;29401;USA"
    parts = split_components(raw)
    assert parts == ["PO Box", r"Suite\;5", r"123 Main\;St", "Charleston", "SC", "29401", "USA"]


def test_split_list_honors_escaped_commas() -> None:
    parts = split_list(r"Alpha,Beta\,Gamma,Delta")
    assert parts == ["Alpha", r"Beta\,Gamma", "Delta"]


def test_join_components_and_list_escape_special_chars() -> None:
    joined = join_components(["a,b", "c;d"])
    assert joined == r"a\,b;c\;d"
    listed = join_list(["one, item", "two"])
    assert listed == r"one\, item,two"


def test_parse_property_line_extracts_group_name_params_value() -> None:
    line = 'ITEM1.EMAIL;TYPE=INTERNET;TYPE=WORK:REMOVED'
    prop = parse_property_line(line)
    assert prop is not None
    assert prop.group == "ITEM1"
    assert prop.name == "EMAIL"
    assert prop.value == "REMOVED"
    assert prop.params["TYPE"] == ["INTERNET", "WORK"]


def test_parse_property_line_handles_quoted_param_values() -> None:
    # Per RFC 6350 §3.3, DQUOTE quoting protects the entire value including
    # internal commas — `TYPE="WORK,VOICE"` is ONE param value containing a
    # literal comma. Multi-value params use unquoted CSV: `TYPE=WORK,VOICE`.
    prop = parse_property_line('EMAIL;TYPE="WORK,VOICE";LABEL="Hello, World":REMOVED')
    assert prop is not None
    assert prop.params["TYPE"] == ["WORK,VOICE"]
    assert prop.params["LABEL"] == ["Hello, World"]


def test_parse_property_line_handles_unquoted_csv_param() -> None:
    prop = parse_property_line("EMAIL;TYPE=WORK,VOICE:REMOVED")
    assert prop is not None
    assert prop.params["TYPE"] == ["WORK", "VOICE"]


def test_serialize_property_inverts_parse() -> None:
    from contact_ops.carddav.apple_quirks import VCardProperty

    prop = VCardProperty(name="FN", value="Aaron Stransky")
    assert serialize_property(prop) == "FN:Aaron Stransky"


# ---------- end-to-end round-trip ----------


def _sample_contact() -> CanonicalContact:
    return CanonicalContact(
        vcard_uid="urn:uuid:12345678-1234-1234-1234-123456789abc",
        display_name="Aaron Stransky",
        family_name="Stransky",
        given_name="Aaron",
        additional_names=["David"],
        honorific_prefix="Mr.",
        honorific_suffix="O-1E",
        nicknames=["AaronS", "ATS"],
        birthday=CanonicalDate(year=1984, month=6, day=15),
        anniversary=CanonicalDate(year=None, month=12, day=21),
        gender_identity="male",
        pronouns="he/him",
        preferred_languages=["en-US"],
        time_zone="America/New_York",
        note="Test contact for round-trip verification",
        categories=["founder", "veteran"],
        rev=datetime(2026, 5, 22, 18, 14, 2, tzinfo=timezone.utc),
        emails=[
            CanonicalEmail(address="REMOVED", type="work", is_primary=True),
            CanonicalEmail(address="REMOVED", type="personal", label="Personal Gmail"),
        ],
        phones=[
            CanonicalPhone(e164="+18435551234", type="mobile", is_sms_capable=True, is_primary=True),
        ],
        urls=[
            CanonicalUrl(url="https://linkedin.com/in/aaronstransky", type="profile"),
            CanonicalUrl(url="https://github.com/aaronstransky", type="profile"),
        ],
        addresses=[
            CanonicalAddress(
                type="home",
                street_address="123 Main St",
                locality="Charleston",
                region="SC",
                postal_code="29401",
                country_name="USA",
                country_code="US",
                geo_lat=32.7765,
                geo_lng=-79.9311,
                is_primary=True,
            ),
        ],
        im_handles=[
            CanonicalIM(protocol="signal", handle="+18435551234", is_primary=True),
        ],
        organizations=[
            CanonicalOrgRole(
                org_display_name="Magic Unicorn Inc.",
                department="Engineering",
                title="CEO",
                role_type="founder",
                is_current=True,
                is_primary=True,
            ),
        ],
        related=[
            CanonicalRelation(related_uid="urn:uuid:other", type="spouse_of"),
        ],
        source_pid_map={"1": "urn:uuid:device-a"},
    )


def test_full_serialize_emits_required_fields() -> None:
    text = serialize_canonical_to_vcard(_sample_contact())
    assert text.startswith("BEGIN:VCARD\r\n")
    assert "VERSION:4.0" in text
    assert "FN:Aaron Stransky" in text
    assert "UID:urn:uuid:" in text
    assert "EMAIL" in text
    assert "TEL" in text
    assert "ADR" in text
    assert "GEO:geo:32.7765,-79.9311" in text
    assert "BDAY:19840615" in text
    assert "ANNIVERSARY:--1221" in text
    assert "GENDER:M;male" in text
    assert "ORG:Magic Unicorn Inc.;Engineering;" in text
    assert "TITLE:CEO" in text
    assert "NOTE:Test contact" in text
    assert "CATEGORIES:founder,veteran" in text
    assert "REV:20260522T181402Z" in text
    assert "CLIENTPIDMAP:1;urn:uuid:device-a" in text
    assert "END:VCARD" in text


def test_full_roundtrip_preserves_canonical_fields() -> None:
    original = _sample_contact()
    rendered = serialize_canonical_to_vcard(original)
    reparsed = parse_vcard_to_canonical(rendered)

    assert reparsed.vcard_uid == original.vcard_uid
    assert reparsed.display_name == original.display_name
    assert reparsed.family_name == original.family_name
    assert reparsed.given_name == original.given_name
    assert reparsed.additional_names == original.additional_names
    assert reparsed.honorific_prefix == original.honorific_prefix
    assert reparsed.honorific_suffix == original.honorific_suffix
    assert reparsed.nicknames == original.nicknames
    assert reparsed.birthday == original.birthday
    assert reparsed.anniversary == original.anniversary
    assert reparsed.gender_identity == original.gender_identity
    assert reparsed.preferred_languages == original.preferred_languages
    assert reparsed.time_zone == original.time_zone
    assert reparsed.note == original.note
    assert reparsed.categories == original.categories
    assert reparsed.source_pid_map == original.source_pid_map


def test_roundtrip_emails_with_labels_via_item_grouping() -> None:
    original = _sample_contact()
    rendered = serialize_canonical_to_vcard(original)
    # Apple ITEMn grouping should appear for the labeled email
    assert "X-ABLABEL:Personal Gmail" in rendered
    reparsed = parse_vcard_to_canonical(rendered)
    labels = sorted([(e.address, e.label) for e in reparsed.emails])
    assert ("REMOVED", "Personal Gmail") in labels


def test_roundtrip_phone_e164_preserved() -> None:
    original = _sample_contact()
    rendered = serialize_canonical_to_vcard(original)
    reparsed = parse_vcard_to_canonical(rendered)
    assert reparsed.phones[0].e164 == "+18435551234"


def test_roundtrip_x_socialprofile_emitted_for_linkedin() -> None:
    original = _sample_contact()
    rendered = serialize_canonical_to_vcard(original)
    assert "X-SOCIALPROFILE" in rendered
    assert "TYPE=linkedin" in rendered


def test_anniversary_yearless_form() -> None:
    contact = _sample_contact()
    rendered = serialize_canonical_to_vcard(contact)
    assert "ANNIVERSARY:--1221" in rendered
    reparsed = parse_vcard_to_canonical(rendered)
    assert reparsed.anniversary is not None
    assert reparsed.anniversary.year is None
    assert reparsed.anniversary.month == 12
    assert reparsed.anniversary.day == 21


def test_parse_handles_vcard3_x_anniversary_input() -> None:
    text = "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Test\r\nUID:test\r\nX-ANNIVERSARY:--1221\r\nEND:VCARD\r\n"
    parsed = parse_vcard_to_canonical(text)
    assert parsed.anniversary is not None
    assert parsed.anniversary.month == 12


def test_parse_handles_legacy_year_1604_as_yearless() -> None:
    text = "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Test\r\nUID:test\r\nBDAY:1604-06-15\r\nEND:VCARD\r\n"
    parsed = parse_vcard_to_canonical(text)
    assert parsed.birthday is not None
    assert parsed.birthday.year is None
    assert parsed.birthday.month == 6


def test_n_component_escaping_survives_roundtrip() -> None:
    contact = CanonicalContact(
        vcard_uid="urn:uuid:x",
        display_name="O'Hara, Sr.",
        family_name="O;Hara",
        given_name="Test",
        honorific_suffix="Sr., Esq.",
    )
    rendered = serialize_canonical_to_vcard(contact)
    reparsed = parse_vcard_to_canonical(rendered)
    assert reparsed.family_name == "O;Hara"
    assert reparsed.honorific_suffix == "Sr., Esq."


def test_minimal_contact_renders_required_fields_only() -> None:
    contact = CanonicalContact(vcard_uid="urn:uuid:1", display_name="Just A Name")
    text = serialize_canonical_to_vcard(contact)
    assert "BEGIN:VCARD" in text
    assert "FN:Just A Name" in text
    assert "EMAIL" not in text  # no emails -> no EMAIL line
    assert "END:VCARD" in text
