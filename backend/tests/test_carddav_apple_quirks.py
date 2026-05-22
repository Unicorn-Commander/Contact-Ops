"""Tests for Apple ITEMn grouping, X-SOCIALPROFILE, CLIENTPIDMAP."""

from __future__ import annotations

from contact_ops.carddav.apple_quirks import (
    ClientPidMap,
    LABELABLE_PROPERTIES,
    VCardProperty,
    assign_item_groups,
    collapse_item_groups,
    deserialize_clientpidmaps_from_jsonb,
    emit_clientpidmaps,
    emit_x_socialprofile,
    extract_clientpidmaps,
    is_item_group,
    is_social_profile_url,
    serialize_clientpidmaps_to_jsonb,
    social_type_for_url,
)


def test_labelable_properties_covers_iOS_label_surfaces() -> None:
    for name in ("EMAIL", "TEL", "URL", "ADR", "IMPP", "X-SOCIALPROFILE"):
        assert name in LABELABLE_PROPERTIES


def test_is_item_group_recognizes_apple_form() -> None:
    assert is_item_group("ITEM1")
    assert is_item_group("ITEM12")
    assert is_item_group("item1")
    assert not is_item_group("EMAIL")
    assert not is_item_group("ITEM")
    assert not is_item_group("ITEMA")


def test_assign_item_groups_assigns_when_label_present() -> None:
    props = [
        VCardProperty(name="EMAIL", value="REMOVED", params={"LABEL": ["AOL"]}),
        VCardProperty(name="EMAIL", value="REMOVED", params={"TYPE": ["WORK"]}),
        VCardProperty(name="TEL", value="+18435551234", params={"LABEL": ["After-hours"]}),
    ]
    grouped = assign_item_groups(props)

    # Each labeled property got an ITEMn group + sibling X-ABLABEL
    by_name = {}
    for p in grouped:
        by_name.setdefault(p.name, []).append(p)
    assert len(by_name["X-ABLABEL"]) == 2
    assert by_name["EMAIL"][0].group is not None
    assert by_name["EMAIL"][0].group == by_name["X-ABLABEL"][0].group
    # LABEL param removed; lives on X-ABLABEL line instead
    assert "LABEL" not in by_name["EMAIL"][0].params

    # Unlabeled EMAIL untouched
    assert by_name["EMAIL"][1].group is None
    assert "LABEL" not in by_name["EMAIL"][1].params


def test_assign_item_groups_skips_empty_labels() -> None:
    props = [
        VCardProperty(name="EMAIL", value="REMOVED", params={"LABEL": [""]}),
    ]
    grouped = assign_item_groups(props)
    assert len(grouped) == 1
    assert grouped[0].group is None


def test_collapse_item_groups_promotes_label_back_onto_sibling() -> None:
    props = [
        VCardProperty(name="EMAIL", value="REMOVED", group="ITEM1"),
        VCardProperty(name="X-ABLABEL", value="AOL", group="ITEM1"),
        VCardProperty(name="TEL", value="+1", group="ITEM2"),
        VCardProperty(name="X-ABLABEL", value="Carrier Pigeon", group="ITEM2"),
    ]
    collapsed = collapse_item_groups(props)
    names = [p.name for p in collapsed]
    assert "X-ABLABEL" not in names
    assert collapsed[0].params["LABEL"] == ["AOL"]
    assert collapsed[1].params["LABEL"] == ["Carrier Pigeon"]


def test_roundtrip_assign_then_collapse_preserves_label() -> None:
    original = [
        VCardProperty(name="EMAIL", value="REMOVED", params={"LABEL": ["Pager"]}),
    ]
    grouped = assign_item_groups([VCardProperty(**vars(p)) for p in original])
    collapsed = collapse_item_groups(grouped)
    # Only the EMAIL line survives, with the LABEL param back in place
    assert [p.name for p in collapsed] == ["EMAIL"]
    assert collapsed[0].params["LABEL"] == ["Pager"]


def test_social_type_for_url_recognizes_known_hosts() -> None:
    assert social_type_for_url("https://linkedin.com/in/aaron") == "linkedin"
    assert social_type_for_url("https://www.linkedin.com/in/aaron") == "linkedin"
    assert social_type_for_url("https://x.com/aaron") == "twitter"
    assert social_type_for_url("https://twitter.com/aaron") == "twitter"
    assert social_type_for_url("https://github.com/aaronstransky") == "github"


def test_social_type_for_url_returns_none_for_unknown() -> None:
    assert social_type_for_url("https://magicunicorn.dev/aaron") is None
    assert social_type_for_url("") is None


def test_is_social_profile_url_matches_known_hosts() -> None:
    assert is_social_profile_url("https://linkedin.com/in/aaron")
    assert not is_social_profile_url("https://example.com/aaron")


def test_emit_x_socialprofile_renders_with_type_param() -> None:
    prop = emit_x_socialprofile("https://linkedin.com/in/aaron")
    assert prop is not None
    assert prop.name == "X-SOCIALPROFILE"
    assert prop.params["TYPE"] == ["linkedin"]


def test_emit_x_socialprofile_returns_none_for_unknown_host() -> None:
    assert emit_x_socialprofile("https://example.com") is None


def test_extract_clientpidmaps_parses_correctly() -> None:
    props = [
        VCardProperty(name="CLIENTPIDMAP", value="1;urn:uuid:device-a"),
        VCardProperty(name="CLIENTPIDMAP", value="2;urn:uuid:device-b"),
        VCardProperty(name="CLIENTPIDMAP", value="malformed"),  # dropped
    ]
    maps = extract_clientpidmaps(props)
    assert len(maps) == 2
    assert maps[0].pid == 1
    assert maps[0].source_uri == "urn:uuid:device-a"
    assert maps[1].pid == 2


def test_emit_clientpidmaps_inverts_extract() -> None:
    maps = [
        ClientPidMap(pid=1, source_uri="urn:uuid:a"),
        ClientPidMap(pid=2, source_uri="urn:uuid:b"),
    ]
    props = emit_clientpidmaps(maps)
    assert [p.value for p in props] == ["1;urn:uuid:a", "2;urn:uuid:b"]
    # And extract round-trips
    assert extract_clientpidmaps(props) == maps


def test_clientpidmap_jsonb_roundtrip() -> None:
    maps = [
        ClientPidMap(pid=1, source_uri="urn:uuid:a"),
        ClientPidMap(pid=2, source_uri="urn:uuid:b"),
    ]
    payload = serialize_clientpidmaps_to_jsonb(maps)
    assert payload == {"1": "urn:uuid:a", "2": "urn:uuid:b"}
    assert deserialize_clientpidmaps_from_jsonb(payload) == maps
    assert deserialize_clientpidmaps_from_jsonb(None) == []
