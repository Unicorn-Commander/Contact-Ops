# contactops_push

Shared producer connector for the **Contact-Ops ingest seam**
(`CONTACT-OPS-INGEST-CONTRACT` §1B / §3). Write-once; drop into each leadgen app
(NorthwindLeads, Unicorn Outreach, multistate-retirement-leads, LoopNet-Leads, …).

A producing app maps its **graduated** lead to identity attributes + a
source/consent block and calls `push_contact`. Contact-Ops does the idempotent
upsert and returns the shared `contact_id` to store for back-reference.

- **Self-contained** — only depends on `httpx` + the stdlib; does **not** import
  the contact_ops backend, so copy/submodule it freely.
- **Idempotent** — the caller supplies the dedup keys (email / phone /
  `external_ref`); a retry or re-graduation updates instead of duplicating.
- **Degrade-clean** — timeouts / connection errors / HTTP 5xx / 429 are retried
  with backoff, then raise a typed `ContactOpsPushTransientError` (queue + retry
  later). A 4xx raises `ContactOpsPushClientError` immediately (retry won't help).

## Install

```
pip install httpx        # the only runtime dependency
```

## Usage

```python
import asyncio
from contactops_push import ContactOpsPush

async def main():
    async with ContactOpsPush(base_url="https://contacts.magicunicorn.dev",
                              token="svc-token-or-callable") as push:
        res = await push.push_contact(
            identity={"match_by": [{"kind": "email", "value": "REMOVED"}],
                      "attributes": {"display_name": "Dana Lead",
                                     "emails": [{"address": "REMOVED"}]}},
            source={"source_label": "NorthwindLeads", "source_kind": "leadgen_app",
                    "external_ref": "LV-42", "consent_basis": "opted_in"},
            rollups={"lead_score": 87.5, "stage": "qualified"})
        print(res)  # {"contact_id": "…uuid…", "action": "created"}

asyncio.run(main())
```

### Short-lived tokens (Brigade-STS / OBO)

Pass a (sync or async) zero-arg callable as `token`; it's invoked per push so the
bearer is always fresh:

```python
push = ContactOpsPush(base_url=URL, token=lambda: brigade.current_access_token())
```

### Bulk

```python
summary = await push.push_contacts_bulk([
    {"identity": {...}, "source": {...}, "rollups": {...}},
    ...,  # up to 50
])
# {"created": N, "updated": N, "noop": N, "errors": N, "results": [...]}
```

## Contract notes

- `attributes` is **identity only** (name / emails / phones / social / company).
  Keep vertical/enrichment data in the producing app; send only identity + a
  rollup tag.
- `source.consent_basis` is **required** (one of the `ConsentBasis` vocabulary:
  `self_provided`, `business_card`, `opted_in`, `public_record`,
  `legitimate_interest`, `vendor_provided`).
- The returned `contact_id` is the shared id — store it for back-reference.
