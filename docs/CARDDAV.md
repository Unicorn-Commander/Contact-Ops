# Contact-Ops CardDAV

Contact-Ops exposes every tenant's contact list as a standards-compliant
**CardDAV** addressbook so users can subscribe directly from iOS
Contacts.app, macOS Contacts.app, or Thunderbird CardBook. Reads and
writes are bidirectional — a contact created on an iPhone shows up in
Contact-Ops within seconds, and any change Contact-Ops applies
(via the MCP surface, the human UI, or an agent) shows up on the
device on the next sync.

This guide covers:

1. [How the URL space is shaped](#url-space)
2. [How to provision a per-device app password](#provisioning-an-app-password)
3. [Client setup walkthroughs](#client-setup) (iOS, macOS, Thunderbird)
4. [Troubleshooting](#troubleshooting)
5. [Operator notes (HIPAA / multi-tenant / Garage)](#operator-notes)

---

## URL space

| URL                                                       | What lives there                                                                    |
| --------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `/.well-known/carddav`                                    | 301 redirect → `/carddav/` (per RFC 6764)                                            |
| `/carddav/`                                               | Root principal collection                                                            |
| `/carddav/{user_id}/`                                     | Per-user principal — one entry per `uc_uid`                                          |
| `/carddav/{user_id}/{tenant_slug}/`                       | The addressbook collection for that tenant                                           |
| `/carddav/{user_id}/{tenant_slug}/{vcard_uid}.vcf`        | Individual vCard 4.0 resource                                                        |

A single user can belong to multiple tenants (e.g., Aaron has a
`magic-unicorn` tenant and an `aaron-personal` tenant). Each tenant
gets its own addressbook URL. The HTTP Basic credential is bound to
**one (user, tenant) pair** — to subscribe to two tenants on the same
iPhone, generate two app passwords and configure two CardDAV accounts
(iOS supports this natively).

---

## Provisioning an app password

iOS Contacts.app cannot send bearer JWTs. Contact-Ops therefore issues
**per-device app passwords** stored only as bcrypt hashes. Plaintext is
shown ONCE at generation time — write it down or paste it directly into
the device's CardDAV setup screen.

### Via MCP (preferred)

```jsonc
// tools/call
{
  "name": "generate_carddav_app_password",
  "arguments": {
    "device_label": "Aaron's iPhone 16 Pro",
    "scopes": ["carddav:read", "carddav:write"]
  }
}
```

Response (annotated):

```json
{
  "app_password_id": "0190a3c0-1234-7000-8000-...",
  "app_password_plaintext": "rN_8x...mq3w",     // copy NOW — never shown again
  "last_4_chars": "mq3w",                       // shown later for recognition
  "device_label": "Aaron's iPhone 16 Pro",
  "scopes": ["carddav:read", "carddav:write"],
  "created_at": "2026-05-22T18:14:02.184Z"
}
```

### Other administration tools

- `list_carddav_app_passwords` — see which devices are subscribed,
  when they last synced, the user-agent string, and the source IP.
- `revoke_carddav_app_password` — soft-delete (sets `revoked_at`).
  The device starts failing auth within 60 seconds.

---

## Client setup

### iOS Contacts.app (iOS 17+)

1. **Settings → Contacts → Accounts → Add Account → Other → Add CardDAV Account**.
2. Fill in:
   - **Server**: `contacts.magicunicorn.dev` (or your Contact-Ops host)
   - **User Name**: your `uc_uid` (e.g. `aaron`)
   - **Password**: the plaintext from `generate_carddav_app_password`
   - **Description**: tenant slug (helps disambiguate when you add a
     second tenant's account)
3. Tap **Next**. iOS will probe `/.well-known/carddav` and resolve the
   account; on success you'll see the new account in the list.
4. **Settings → Contacts → Default Account** lets you choose which
   addressbook new contacts go into (iCloud vs. Contact-Ops).

Custom labels (`Home`, `Work`, anything you've typed) survive
round-trips via Apple's ITEMn grouping. Profile URLs for LinkedIn /
Twitter / GitHub / Facebook show up in the "Social Profiles" section
of the contact card via `X-SOCIALPROFILE`.

### macOS Contacts.app (macOS 14+)

1. **Contacts → Add Account → Other Contacts Account...**.
2. **Account Type: CardDAV**.
3. Fields are identical to iOS. The "Server URL" field accepts either
   `contacts.magicunicorn.dev` or the full `/carddav/<user_id>/` URL —
   Contact-Ops handles both forms.

### Thunderbird (CardBook addon)

1. Install the **CardBook** addon.
2. **CardBook → Address Book → New Address Book → Remote → CardDAV**.
3. URL: `https://contacts.magicunicorn.dev/carddav/<user_id>/<tenant_slug>/`
4. Username / Password: as above.

---

## Troubleshooting

| Symptom                                          | What to check                                                                                                                                          |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| iOS "Account refused to connect"                 | The hostname must be reachable over **HTTPS**. CardDAV over plain HTTP is silently rejected. Verify with `curl -I https://...`.                          |
| "Cannot Connect Using SSL"                       | iOS Contacts.app is strict about cert validity. The Contact-Ops Traefik front-end must serve a publicly-trusted cert; self-signed test certs fail.       |
| 401 on every sync                                | `list_carddav_app_passwords` will show the device making the attempt (via `last_used_user_agent`). Confirm the device is using the right `uc_uid` + tenant.|
| 429 Too Many Requests                            | Rate limiter trips after 5 failed attempts in 60 seconds per (IP, username). Wait 60s or revoke + reissue the password.                                |
| `412 Precondition Failed` on PUT                  | The client sent a stale `If-Match`. iOS retries the next sync cycle — this is normal under conflict, not an error to chase.                              |
| Photos missing on iOS                            | Phase 2 only emits `PHOTO` lines when the canonical record has inline base64 data. Photo presigned-URL support arrives with Codex 1's `storage.py`.     |
| Custom email/phone label appears as "(no label)" | iOS only honors labels emitted via ITEMn grouping. Confirm the canonical row's `label` column is set, not just the `type`.                              |
| Contact disappears after deletion on iOS          | DELETE soft-deletes via `PersonTenantMembership.visibility=archived` (or `Person.merge_status=archived` if your tenant owns the record). The data remains; the listing filter hides it.|

### Running litmus against a dev server

```bash
brew install litmus    # macOS: requires `--with-ssl`
# or
apt install litmus     # Linux

STANDALONE_MODE=true uvicorn contact_ops.main:app --port 8501 &

# Mint an app password for the standalone user first via the MCP server,
# then run litmus with that credential pair:
litmus -k \
    http://localhost:8501/carddav/standalone-user/standalone/ \
    standalone-user \
    "$APP_PASSWORD_PLAINTEXT"
```

litmus's `props`, `basic`, and `copymove` checks should pass; the
`http`, `locks`, and `large` sections target generic WebDAV behaviors
that are out of scope for an addressbook server (we deliberately do
not support LOCK, MOVE, COPY).

---

## Operator notes

### Test-time dependencies

`lxml` and `bcrypt` are runtime requirements but are intentionally
pinned outside the hashed `requirements.txt` to keep the operator's
`pip-compile --generate-hashes` flow predictable. Install them at the
top of any environment that runs the CardDAV tests:

```bash
pip install lxml bcrypt
```

### HIPAA tenants

The HIPAA fence on `apply_vcard_text_to_db` raises
`CrossTenantWriteRefused` (mapped to HTTP 403) whenever a PUT would
touch a person whose `canonical_owner_tenant_id` is not the
caller's tenant. iOS surfaces this as "Failed to update contact",
and the device will continue retrying — operators should advise users
on HIPAA tenants to keep cross-tenant contacts off the device entirely
unless they have explicit data-sharing in place.

### Garage buckets

Run `scripts/garage_bootstrap.sh` after creating any new tenant — it
provisions the five per-tenant buckets (`photos`, `voice-samples`,
`business-cards`, `vcard-archive`, `evidence-snapshots`) with the
right retention policy (HIPAA → indefinite; non-HIPAA evidence → 90 d;
vcard-archive → 7 d). The script is idempotent; re-running it does no
harm.

### Production hardening (Phase 3)

The in-process rate limiter in `carddav/auth.py` is fine for a single
uvicorn worker. When Contact-Ops scales to multiple workers, swap to
the Redis token-bucket implementation referenced in the module's
docstring — every other piece of the auth path is already wire-compatible.

`sync-collection` REPORT support is a Phase 2 stub that returns the
full member set. Real token-based incremental sync lands alongside the
`graph_sync_outbox` outbox processor in Phase 3.
