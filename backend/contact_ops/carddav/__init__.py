"""Contact-Ops CardDAV server (RFC 6352).

Mounts at ``/carddav`` and exposes per-tenant addressbooks consumable by
iOS Contacts.app, macOS Contacts.app, and Thunderbird CardBook.

The router runs HTTP Basic auth against per-device app passwords
(see :mod:`contact_ops.carddav.auth`) and bypasses the global JWT
middleware — iOS Contacts.app cannot send bearer tokens.

vCard 4.0 ↔ canonical-schema mapping lives in
:mod:`contact_ops.carddav.vcard_serialize` and
:mod:`contact_ops.carddav.vcard_parse`. Apple-specific quirks
(ITEMn grouping, X-SOCIALPROFILE, CLIENTPIDMAP) are isolated in
:mod:`contact_ops.carddav.apple_quirks` so the canonical mapping
stays RFC-clean.

Design contract: see ``Contact-Ops-MCP-Design.md`` §4.1.16 plus
RFC 6352 and RFC 6350.
"""

from __future__ import annotations

from contact_ops.carddav.router import carddav_router

__all__ = ["carddav_router"]
