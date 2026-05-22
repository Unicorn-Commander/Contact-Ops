"""Apple-specific vCard interop layer.

iOS Contacts.app and macOS Contacts.app emit and consume vCards that
diverge from RFC 6350 in ways that real-world Contact-Ops users will
notice if Contact-Ops emits strict-RFC output. This module owns three
of those divergences so the canonical mapping in
:mod:`contact_ops.carddav.vcard_serialize` stays clean.

1. **ITEMn grouping for custom labels**

   Apple expects::

       ITEM1.EMAIL;TYPE=INTERNET:REMOVED
       ITEM1.X-ABLABEL:AOL

   NOT::

       EMAIL;TYPE=INTERNET;LABEL="AOL":REMOVED

   We assign sequential ``ITEMn`` group identifiers to every property
   that carries a non-empty custom label and emit a matching
   ``X-ABLABEL`` line. On the parse side we collapse ``ITEMn.X-ABLABEL``
   back onto the sibling property's ``label`` field.

2. **X-SOCIALPROFILE**

   Apple stores LinkedIn / Twitter / etc. as ``X-SOCIALPROFILE`` and
   surfaces them in the "Social Profiles" section of the Contacts UI.
   We emit ``X-SOCIALPROFILE`` alongside the RFC-standard ``IMPP`` line
   for the known social-network namespaces, and we accept both on parse.

3. **CLIENTPIDMAP preservation**

   When a vCard originates on a device that participates in vCard 4.0
   multi-source merge (RFC 6350 §7), it carries a ``CLIENTPIDMAP`` line
   and ``PID=`` parameters on individual properties. We preserve those
   verbatim across round-trips by storing the structured form on
   ``persons.source_pid_map`` and reconstructing on export.

References:
    - https://sabre.io/dav/clients/ios/
    - RFC 6350 §7 (PID + CLIENTPIDMAP)
    - https://alessandrorossini.org/the-sad-story-of-the-vcard-format
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


# Properties that may carry an X-ABLABEL group label.
LABELABLE_PROPERTIES: frozenset[str] = frozenset(
    {"EMAIL", "TEL", "URL", "ADR", "IMPP", "X-SOCIALPROFILE"}
)


# Map of recognized social-profile hosts to the TYPE= parameter Apple expects
# on X-SOCIALPROFILE. Apple is case-insensitive on the parameter value.
SOCIAL_NETWORK_HOSTS: dict[str, str] = {
    "linkedin.com": "linkedin",
    "www.linkedin.com": "linkedin",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "facebook.com": "facebook",
    "www.facebook.com": "facebook",
    "github.com": "github",
    "instagram.com": "instagram",
    "www.instagram.com": "instagram",
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "tiktok.com": "tiktok",
    "www.tiktok.com": "tiktok",
}


# Apple's ITEMn group identifier. Spec is loose; we accept any ITEM\d+ form.
_ITEM_GROUP_RE = re.compile(r"^ITEM\d+$", re.IGNORECASE)


@dataclass
class VCardProperty:
    """A single decoded vCard property line.

    Closely models the wire format so the serialize/parse round-trip
    survives even fields we don't otherwise interpret.
    """

    name: str
    value: str
    params: dict[str, list[str]] = field(default_factory=dict)
    group: str | None = None  # ITEM1, ITEM2, ... for Apple-grouped properties

    def get_param(self, key: str) -> list[str]:
        return self.params.get(key.upper(), [])

    def set_param(self, key: str, values: Iterable[str]) -> None:
        self.params[key.upper()] = list(values)


def is_item_group(token: str) -> bool:
    """Return True if ``token`` looks like an Apple ITEMn group identifier."""

    return bool(_ITEM_GROUP_RE.match(token))


def assign_item_groups(properties: list[VCardProperty]) -> list[VCardProperty]:
    """Assign sequential ``ITEMn`` groups to properties with a custom label.

    Mutates the property list in place, walking left-to-right. Any property
    that already has a ``group`` set is left alone (e.g., a CLIENTPIDMAP
    we are preserving verbatim). Returns the same list for chainability.

    For each labelable property whose ``LABEL`` parameter is set, we strip
    that parameter, assign a fresh ITEMn group, and emit a sibling
    ``ITEMn.X-ABLABEL`` line carrying the label text.
    """

    used_indices: set[int] = set()
    for prop in properties:
        if prop.group and _ITEM_GROUP_RE.match(prop.group):
            try:
                used_indices.add(int(prop.group[4:]))
            except ValueError:
                continue

    def next_index() -> int:
        i = 1
        while i in used_indices:
            i += 1
        used_indices.add(i)
        return i

    out: list[VCardProperty] = []
    for prop in properties:
        out.append(prop)
        if prop.name.upper() not in LABELABLE_PROPERTIES:
            continue
        label_values = prop.get_param("LABEL")
        if not label_values:
            continue
        label_text = label_values[0].strip().strip('"')
        if not label_text:
            prop.params.pop("LABEL", None)
            continue

        # Don't double-assign — if the property is already in a group, just
        # ensure the sibling X-ABLABEL is present.
        group_id = prop.group or f"ITEM{next_index()}"
        prop.group = group_id
        prop.params.pop("LABEL", None)

        # Avoid duplicating an existing X-ABLABEL in the same group.
        already_labeled = any(
            p.group == group_id and p.name.upper() == "X-ABLABEL"
            for p in out
        )
        if not already_labeled:
            out.append(
                VCardProperty(
                    name="X-ABLABEL",
                    value=label_text,
                    group=group_id,
                )
            )
    return out


def collapse_item_groups(properties: list[VCardProperty]) -> list[VCardProperty]:
    """Inverse of :func:`assign_item_groups`.

    Walks the property list and folds every ``ITEMn.X-ABLABEL`` into a
    ``LABEL`` parameter on its sibling property within the same ITEMn group.
    The ``X-ABLABEL`` lines are dropped from the output. The group identifier
    is *also* preserved on the surviving sibling so the canonical schema can
    keep round-tripping vCards from multi-source devices without losing the
    Apple grouping.
    """

    labels_by_group: dict[str, str] = {}
    for prop in properties:
        if prop.name.upper() == "X-ABLABEL" and prop.group:
            labels_by_group[prop.group] = prop.value

    out: list[VCardProperty] = []
    for prop in properties:
        if prop.name.upper() == "X-ABLABEL":
            continue
        if prop.group and prop.group in labels_by_group:
            label = labels_by_group[prop.group]
            if "LABEL" not in prop.params:
                prop.set_param("LABEL", [label])
        out.append(prop)
    return out


def social_type_for_url(url: str) -> str | None:
    """Return the Apple ``TYPE=`` token for a social profile URL, or None.

    Lookup is host-only; query strings and paths are ignored.
    """

    if not url:
        return None
    lowered = url.lower().strip()
    # very forgiving host extraction — we accept bare hosts as well as URLs
    if "://" in lowered:
        rest = lowered.split("://", 1)[1]
    else:
        rest = lowered
    host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return SOCIAL_NETWORK_HOSTS.get(host)


def is_social_profile_url(url: str) -> bool:
    """True if ``url`` belongs to a known Apple-recognized social network."""

    return social_type_for_url(url) is not None


def emit_x_socialprofile(url: str, label: str | None = None) -> VCardProperty | None:
    """Build a vCard ``X-SOCIALPROFILE`` line for ``url`` if recognized.

    Returns ``None`` when the URL's host is not in our social-network table —
    callers should fall back to a plain ``URL`` (or ``IMPP``) emission.
    """

    social_type = social_type_for_url(url)
    if social_type is None:
        return None
    prop = VCardProperty(name="X-SOCIALPROFILE", value=url)
    prop.set_param("TYPE", [social_type])
    if label:
        prop.set_param("LABEL", [label])
    return prop


@dataclass
class ClientPidMap:
    """A single ``CLIENTPIDMAP`` row: pid -> source URI."""

    pid: int
    source_uri: str


def extract_clientpidmaps(properties: list[VCardProperty]) -> list[ClientPidMap]:
    """Read all ``CLIENTPIDMAP`` properties out of a parsed vCard."""

    out: list[ClientPidMap] = []
    for prop in properties:
        if prop.name.upper() != "CLIENTPIDMAP":
            continue
        # Value form per RFC 6350 §6.7.7 is `pid;source-uri`.
        if ";" not in prop.value:
            continue
        head, source = prop.value.split(";", 1)
        try:
            pid = int(head.strip())
        except ValueError:
            continue
        out.append(ClientPidMap(pid=pid, source_uri=source.strip()))
    return out


def emit_clientpidmaps(maps: Iterable[ClientPidMap]) -> list[VCardProperty]:
    """Inverse of :func:`extract_clientpidmaps`. Returns properties for emission."""

    return [
        VCardProperty(name="CLIENTPIDMAP", value=f"{m.pid};{m.source_uri}")
        for m in maps
    ]


def serialize_clientpidmaps_to_jsonb(
    maps: Iterable[ClientPidMap],
) -> dict[str, str]:
    """Serialize ``ClientPidMap`` list to the form stored on ``person.source_pid_map``."""

    return {str(m.pid): m.source_uri for m in maps}


def deserialize_clientpidmaps_from_jsonb(
    data: dict[str, str] | None,
) -> list[ClientPidMap]:
    """Inverse of :func:`serialize_clientpidmaps_to_jsonb`."""

    if not data:
        return []
    out: list[ClientPidMap] = []
    for k, v in data.items():
        try:
            out.append(ClientPidMap(pid=int(k), source_uri=str(v)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda m: m.pid)
    return out
