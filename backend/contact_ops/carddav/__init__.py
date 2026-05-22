"""Contact-Ops CardDAV server (RFC 6352).

Mounts at ``/carddav`` and exposes per-tenant addressbooks consumable
by iOS Contacts.app, macOS Contacts.app, and Thunderbird CardBook.

The router runs HTTP Basic auth against per-device app passwords
(see :mod:`contact_ops.carddav.auth`) and bypasses the global JWT
middleware — iOS Contacts.app cannot send bearer tokens.

vCard 4.0 ↔ canonical-schema mapping lives in
:mod:`contact_ops.carddav.vcard_serialize` and
:mod:`contact_ops.carddav.vcard_parse`. Apple-specific quirks
(ITEMn grouping, X-SOCIALPROFILE, CLIENTPIDMAP) are isolated in
:mod:`contact_ops.carddav.apple_quirks`.

Design contract: see ``Contact-Ops-MCP-Design.md`` §4.1.16 plus
RFC 6352 and RFC 6350.

Note: this package's ``__init__.py`` intentionally does NOT import
the router or any module that touches the DB engine. Eager import
would force every test that exercises a pure-function helper (e.g.
:mod:`apple_quirks`, :mod:`vcard_lines`) to set ``DATABASE_URL``.
Import :mod:`contact_ops.carddav.router` directly when you need the
``carddav_router`` symbol.
"""
