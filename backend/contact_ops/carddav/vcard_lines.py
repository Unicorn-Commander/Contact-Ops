"""Low-level vCard line format encode + decode (RFC 6350 §3).

vCard wire format primitives:

  * Lines are folded at 75 octets via CRLF followed by a single SP or HT
    (§3.2). Unfolding removes the continuation whitespace.
  * Property text values escape ``\\``, ``,``, ``;``, and newlines (§3.4).
  * Compound values use ``;`` as the component separator; list values use
    ``,`` within a component.
  * Parameter values are case-insensitive on name; values may be quoted
    when they contain ``,`` ``;`` or ``:``.
  * Groups (``ITEM1.EMAIL:...``) are a ``"." DOT``-separated prefix on the
    property name; preserved by us for Apple-quirks interop.

The :class:`VCardProperty` dataclass is re-exported from
:mod:`contact_ops.carddav.apple_quirks` so that low-level decode/encode
and the Apple-quirk transforms share one shape.
"""

from __future__ import annotations

import re
from typing import Iterable

from contact_ops.carddav.apple_quirks import VCardProperty


# Per RFC 6350 §3.2 — fold at 75 octets, continuation = CRLF + SP/HT.
_FOLD_OCTETS = 75
_CRLF = "\r\n"


# Order of params we like to emit consistently to make round-trips deterministic.
_PARAM_PRIORITY: tuple[str, ...] = ("TYPE", "PREF", "VALUE", "MEDIATYPE", "LANGUAGE")


def unfold(text: str) -> str:
    """Reverse the RFC 6350 §3.2 line-folding."""

    # Accept CRLF, LF, or CR line breaks defensively.
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n[ \t]", "", normalized)


def fold(line: str, *, width: int = _FOLD_OCTETS) -> str:
    """Apply RFC 6350 §3.2 folding to a single logical line.

    Folds on byte boundaries (UTF-8 multi-byte safe — we only break on
    ASCII columns). Continuation lines are prefixed with a single SP.
    """

    if len(line.encode("utf-8")) <= width:
        return line
    out: list[str] = []
    pos = 0
    line_bytes = line.encode("utf-8")
    first = True
    while pos < len(line_bytes):
        slice_width = width if first else width - 1
        end = min(pos + slice_width, len(line_bytes))
        # Avoid splitting a UTF-8 multi-byte sequence.
        while end < len(line_bytes) and (line_bytes[end] & 0xC0) == 0x80:
            end -= 1
        chunk = line_bytes[pos:end].decode("utf-8")
        out.append(chunk if first else " " + chunk)
        pos = end
        first = False
    return _CRLF.join(out)


def escape_text(value: str) -> str:
    """Escape a vCard 4.0 text value per RFC 6350 §3.4."""

    # Order matters: replace backslash first.
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def unescape_text(value: str) -> str:
    """Inverse of :func:`escape_text`."""

    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt in ("n", "N"):
                out.append("\n")
            elif nxt == "\\":
                out.append("\\")
            elif nxt in (",", ";", ":"):
                out.append(nxt)
            else:
                out.append(nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def split_components(value: str) -> list[str]:
    """Split a compound vCard value on ``;``, honoring backslash escapes."""

    parts: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            buf.append(ch)
            buf.append(value[i + 1])
            i += 2
            continue
        if ch == ";":
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def split_list(value: str) -> list[str]:
    """Split a list-value component on ``,`` honoring escapes."""

    parts: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            buf.append(ch)
            buf.append(value[i + 1])
            i += 2
            continue
        if ch == ",":
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def join_components(parts: Iterable[str]) -> str:
    """Inverse of :func:`split_components`. Each part is escape_text()-ed."""

    return ";".join(escape_text(p) for p in parts)


def join_list(parts: Iterable[str]) -> str:
    return ",".join(escape_text(p) for p in parts)


def parse_property_line(line: str) -> VCardProperty | None:
    """Parse a single unfolded vCard property line.

    Returns ``None`` for malformed lines (skipped silently — clients
    occasionally emit junk that we should not propagate).
    """

    if not line or line.startswith("#"):
        return None

    # Split on first unquoted ":" — value portion is everything after.
    name_and_params, _, value = _split_unquoted(line, ":")
    if not name_and_params or value is None:
        return None

    # Parameters: split on ; honoring quoted values.
    pieces = _split_unquoted_multi(name_and_params, ";")
    head = pieces[0].strip()

    # Group prefix (ITEM1.EMAIL ...).
    group: str | None = None
    if "." in head:
        group, head = head.split(".", 1)
        group = group.strip()
    name = head.strip().upper()
    if not name:
        return None

    params: dict[str, list[str]] = {}
    for piece in pieces[1:]:
        if "=" not in piece:
            continue
        pname, _, pval = piece.partition("=")
        key = pname.strip().upper()
        # Param value may itself be a comma-separated list (e.g. TYPE=work,voice).
        if not pval:
            params.setdefault(key, []).append("")
            continue
        vals = _split_quoted_csv(pval)
        params.setdefault(key, []).extend(vals)

    return VCardProperty(name=name, value=value, params=params, group=group)


def serialize_property(prop: VCardProperty) -> str:
    """Emit a single vCard property line (pre-fold). The caller folds + joins."""

    head_parts: list[str] = []
    if prop.group:
        head_parts.append(f"{prop.group}.{prop.name}")
    else:
        head_parts.append(prop.name)

    # Emit params in a stable order: priority first, then alphabetical.
    keys = list(prop.params.keys())
    keys.sort(key=lambda k: (_PARAM_PRIORITY.index(k) if k in _PARAM_PRIORITY else 99, k))
    for key in keys:
        values = prop.params[key]
        if not values:
            continue
        rendered = ",".join(_quote_param_value(v) for v in values if v)
        if not rendered:
            continue
        head_parts.append(f"{key}={rendered}")

    head = ";".join(head_parts)
    return f"{head}:{prop.value}"


def serialize_vcard(properties: Iterable[VCardProperty]) -> str:
    """Render a full vCard 4.0 object with BEGIN/VERSION/END wrappers."""

    parts: list[VCardProperty] = list(properties)
    out_lines = [
        fold("BEGIN:VCARD"),
        fold("VERSION:4.0"),
    ]
    for prop in parts:
        if prop.name.upper() in {"BEGIN", "END", "VERSION"}:
            continue
        out_lines.append(fold(serialize_property(prop)))
    out_lines.append(fold("END:VCARD"))
    return _CRLF.join(out_lines) + _CRLF


def parse_vcard(text: str) -> list[VCardProperty]:
    """Parse a single vCard document. Multi-vCard streams should pre-split.

    Returns the property list between BEGIN:VCARD and END:VCARD, skipping
    BEGIN/END/VERSION wrapper lines. Malformed lines are dropped silently.
    """

    unfolded = unfold(text)
    properties: list[VCardProperty] = []
    inside = False
    for raw_line in unfolded.split("\n"):
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue
        if line.upper().startswith("BEGIN:VCARD"):
            inside = True
            continue
        if line.upper().startswith("END:VCARD"):
            inside = False
            continue
        if not inside:
            continue
        if line.upper().startswith("VERSION:"):
            continue
        parsed = parse_property_line(line)
        if parsed is not None:
            properties.append(parsed)
    return properties


# ---------- private helpers ----------


def _split_unquoted(line: str, delim: str) -> tuple[str, str, str | None]:
    """Split on the first occurrence of ``delim`` outside a double-quoted span."""

    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == delim and not in_quote:
            return line[:i], delim, line[i + 1 :]
    return line, "", None


def _split_unquoted_multi(line: str, delim: str) -> list[str]:
    """Split on every occurrence of ``delim`` outside a double-quoted span."""

    out: list[str] = []
    buf: list[str] = []
    in_quote = False
    for ch in line:
        if ch == '"':
            in_quote = not in_quote
            buf.append(ch)
            continue
        if ch == delim and not in_quote:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    out.append("".join(buf))
    return out


def _split_quoted_csv(value: str) -> list[str]:
    """Split a comma-separated param value, honoring DQUOTE wrapping."""

    out: list[str] = []
    buf: list[str] = []
    in_quote = False
    for ch in value:
        if ch == '"':
            in_quote = not in_quote
            continue
        if ch == "," and not in_quote:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    out.append("".join(buf))
    return [v.strip() for v in out if v.strip()]


def _quote_param_value(value: str) -> str:
    """Wrap a param value in DQUOTE if it contains ``:``, ``;``, or ``,``."""

    if any(c in value for c in (":", ";", ",")) or " " in value:
        return f'"{value}"'
    return value
