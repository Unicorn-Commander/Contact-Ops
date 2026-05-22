# Contact-Ops User Guide

**Date**: 2026-05-21
**Status**: Phase 0 (scaffold). Most of what's described here lands in Phases 1-6.

For the canonical design doc see `/Users/aaronstransky/Documents/Contact-Ops-MCP-Design.md`.

---

## 1. What Contact-Ops does for you

Contact-Ops is your lifetime contact registry. Not a quarterly CRM, not a B2B verification database (that's Data Intel, separate). Contact-Ops is the place your contacts live for the rest of your working life.

It does five things you can't get anywhere else off the shelf:

1. **Lifetime durability.** Every change is reversible. Every field knows where it came from. Merges undo for 90 days. Hard deletes are a glass-break ceremony, not the default.
2. **Agents do the curation.** Sixteen named agents watch your meetings, emails, documents, and external sources, and propose updates. You approve, edit, or reject from an Inbox. Calibration tracks which agents you trust and promotes them automatically.
3. **3D graph navigation.** "Who do I know at Microsoft," "who connects me to this person," "what clusters exist in my network", these are queries, not gut feel.
4. **iOS/macOS sync via CardDAV.** Your iPhone Contacts app talks to Contact-Ops natively. No third-party CardDAV server, no proprietary lock-in.
5. **Per-tenant white-label.** Your personal tenant is private. Your Magic Unicorn tenant has team visibility. Your customer tenants (if any) are isolated. HIPAA tenants are physically fenced.

Phase 0 is the scaffold. None of these features are user-facing yet. Phase 1+2 ships the human UI + CardDAV. Phase 3+4 ships the agent fleet and the 3D viewer. Phase 5 connects the rest of the ecosystem.

---

## 2. Phase 0 status: what works today

| Feature | Phase 0 status |
|---------|----------------|
| Schema | Migrations 0001-0015 land in Postgres |
| MCP server | Answers JSON-RPC handshake, no tools registered |
| Human UI | Not yet built |
| CardDAV | Not yet built |
| Agents | Not yet running |
| 3D viewer | Not yet built |
| iOS sync | Not yet wired |

The end-user surface starts landing in Phase 1. This guide describes the eventual product so you (and any new team member) understand the shape of what's coming.

---

## 3. The MCP tool surface (Phase 1+)

When the tools are live, you interact with Contact-Ops in three ways:

- **Direct MCP**: from Claude Code or any MCP client, calling tools like `get_person`, `upsert_person`, `search_people`. You'll need an OBO token (your access token exchanged for one targeting Contact-Ops). The Developer Guide has the details.
- **Human UI**: `contacts.magicunicorn.dev`. A Next.js app for browsing, editing, and approving agent proposals. Same auth as the rest of the ecosystem (Keycloak SSO).
- **CardDAV**: `carddav.contacts.magicunicorn.dev/<your-user>/<your-tenant>/`. Point your iPhone / Mac / Thunderbird at this URL, log in with HTTP Basic over TLS (or use the iOS Contact setup wizard), and your contacts sync bi-directionally.

Phase 0 has none of these wired up to real tools yet. The endpoints exist; the tools land in Phase 1.

---

## 4. Tenants

A **tenant** is a logical container for a set of contacts. You belong to (at least) two:

- **Your personal tenant** (`aaron-personal` or similar, the exact slug is pending Aaron's confirmation in §10.3 of the design doc). Your iPhone contacts. Your family. Your private notes.
- **Magic Unicorn LLC tenant** (`magic-unicorn-llc`). Your work contacts. Client relationships. Vendor info.

You can be a member of more tenants:

- **Brand tenants**: Center Deep, Majiks, GFL. Each has its own contact registry visible to that brand's team.
- **White-label customer tenants** (eventually): when a customer signs up for Contact-Ops as a SaaS product, they get their own isolated tenant.
- **HIPAA tenants** (if applicable): physically fenced from cross-tenant auto-merge and Data Intel publishing.

### 4.1 How tenants interact

A person can be **canonical-owned by one tenant** and **membership-shared into others**. For example: Allie Menegakis (Aaron's counsel) is canonical-owned in `aaron-personal` (Aaron's address book) but membership-shared into `magic-unicorn-llc` (because she does work for the company too). Each tenant has its own per-tenant notes, tags, custom attributes, and visibility scope on her.

When you switch tenants in the UI (tenant switcher in the top bar), the visible person set changes. The canonical record doesn't move; just what you can see and edit.

### 4.2 Visibility tiers

Within a tenant, each membership has a visibility scope:

| Scope | Who can see |
|-------|-------------|
| `private` | Only you (the membership owner) |
| `team` | Your team within the tenant |
| `org` | Everyone in the tenant |
| `shared` | Everyone in the tenant + explicit external shares |

Default for new memberships is `private`. The tag agent and other auto-curators default new tags to `private` too, per Aaron's `feedback_agent_scoping_pattern.md` memory.

---

## 5. Where your data lives

| Data | Where |
|------|-------|
| Canonical person/org records | Postgres `contact_ops_db` on `unicorn-postgresql` (centerdeep) |
| Per-tenant tags, notes, custom attributes | Same Postgres, in `person_tenant_membership` and `tags` tables |
| Photos, voice samples, business-card scans, vCard archives | Garage (`unicorn-garage` on bigboy), bucket `contact-ops-<tenant>-*`, SSE-KMS encrypted |
| Voice fingerprints, face embeddings, name + bio embeddings | Postgres pgvector + Qdrant (centerdeep) |
| Relationship graph (3D viz) | FalkorDB (`contactops-falkordb` on centerdeep) |
| Audit log (every change with full actor chain) | Postgres `action_event` table, append-only, archived nightly to write-once Garage bucket |

Everything is yours. Export at any time (vCard, JSON, CSV, Phase 2 ships these).

---

## 6. The approval inbox (Phase 3)

Agents propose; humans approve. Most agent proposals land in your Inbox with:

- **What's proposed**: e.g., "Add phone `+15555550100` to Jane Doe (work)."
- **Why**: the evidence. Source(s), trace_id, model used, confidence score.
- **Calibration**: how often this agent's similar proposals have been approved vs reverted.
- **Actions**: approve, reject, edit-then-approve, snooze.

Bulk operations work too: select all proposals from a given source, approve as a batch (one click), revert as a batch (one click) if you change your mind.

### 6.1 Confidence tiers

The system uses four bands:

| Confidence | What happens |
|-----------|--------------|
| `≥ 0.95` | Auto-applied. Lands in audit log, not the Inbox. You can still revert. |
| `0.75 - 0.95` | Lands in the Inbox for your approval. |
| `0.50 - 0.75` | Lands in the Inbox, lower priority. |
| `< 0.50` | Discarded. Logged for calibration. |

You can tune the thresholds per-tenant and per-event-type. Want auto-apply for tag suggestions but propose-only for phone additions? You can set that.

### 6.2 Always-propose-only categories (forever)

Some things never auto-apply regardless of confidence (Aaron's standing rule from `feedback_confidence_tags_legal_work.md`):

- **Legal relations**: `counsel_for`, `client_of_counsel`, `witness_for`, `party_to`, `opposing_party_to`, `expert_for`. Court-grade assertions need human review.
- **Family relations**: `parent_of`, `child_of`, `spouse_of`, `sibling_of`. Sensitive enough that "agent inferred" is never good enough.
- **Status fields**: `is_deceased`, `death_date`. False positives are devastating; always require human confirmation.

The Calibration Daemon's promotion ceiling enforces this regardless of agent trust tier.

---

## 7. The 3D graph viewer (Phase 4)

Open your ego graph by clicking your own avatar. See, in 3D:

- **Direct connections** (`KNOWS`, `WORKS_AT`, `FAMILY_OF`).
- **Two-hop connections** (mutuals).
- **Topical clusters** (people you talk to about X).
- **Path queries**: "shortest path to Morgan Ali" (resolves through Shafen via the SHAFEN-LOANS Crisis-Ops case).
- **Intro suggestions**: "you might want to introduce these two people who don't know each other but share three contacts."

Same engine as Crisis-Ops's `Graph3DView.jsx` and Brigade's `KnowledgeGraph.jsx`, `react-force-graph-3d` with `three-spritetext` labels.

Click any node to drill in: see all of that person's emails, phones, employments, relationships, last interactions, and field provenance. "Where did this fact come from?" is a click away.

---

## 8. iOS / macOS sync via CardDAV (Phase 2)

Add Contact-Ops as a CardDAV account on your iPhone:

1. Settings -> Contacts -> Accounts -> Add Account -> Other -> Add CardDAV Account.
2. Server: `carddav.contacts.magicunicorn.dev`.
3. Username: your `uc_uid`.
4. Password: a service password (or use OAuth-bridge for first-class auth).
5. Description: "Contact-Ops Personal" (or whatever you prefer).

Your iPhone shows the new account in Contacts. Changes flow bi-directionally:

- You edit a contact on your phone: Contact-Ops gets an inbound PUT, the CardDAV Reconciliation Agent runs, the change auto-applies (high confidence because YOU made it on YOUR phone).
- Contact-Ops updates a contact server-side: next sync pulls the new vCard.
- Both edited the same contact: both versions land in the Inbox as a conflict pair. You decide which wins.

Per-tenant address books: when you switch tenants in the iPhone Contacts app account picker, you see only that tenant's visible memberships.

CLIENTPIDMAP preserves multi-source fidelity. If a contact has fields from iCloud + Google + Contact-Ops, round-trips don't lose data.

---

## 9. Tags, notes, custom attributes

Per-tenant ontology. Each tenant has its own tag tree (slugs are unique within a tenant). Tags carry color, description, optional parent for hierarchy.

Custom attributes are JSONB per `person_tenant_membership` row. Schema-less by design: store whatever you need ("alma_mater", "favorite_coffee", "prefers_morning_calls") without migrations.

When something is truly cross-tenant and structured, it lives in `facts` instead, with full provenance.

---

## 10. Privacy + ACL model

- **`canonical_owner_tenant_id`** is the row's home tenant. RLS enforces that only that tenant (and explicit memberships) can read or modify.
- **`person_tenant_membership.visibility`** controls per-tenant visibility within the membership tenant: `private` / `team` / `org` / `shared`.
- **Consent records** track per-person per-purpose consent (`marketing`, `transactional`, `data_intel_share`, `telemetry`). Withdrawals are authoritative, the Consent Watchdog Agent (trust tier `authoritative` from day one) processes opt-outs immediately, no human approval required.
- **HIPAA mode** on a tenant: three-layer fence (flag + RLS policy + merge trigger). Cross-tenant auto-merge is rejected. Data Intel outbound is structurally disabled. Read access is logged in `action_event`.
- **GDPR mode** on a tenant: surfaces erasure requests as a glass-break tool (`delete_person`). Hard delete tombstones for 90 days then physically purges row content, keeping a SHA-256 of original for audit-integrity.

---

## 11. Data export ("rest of my life" durability)

The full registry exports cleanly for portability:

- **vCard 4.0** export (`export_vcard`): full canonical record per person, including X-SOCIALPROFILE, photo BASE64-embed, CLIENTPIDMAP.
- **JSON** export (`export_json`): every field, every relationship, every fact, every interaction. The raw shape.
- **CSV** export (`export_csv`): flat denormalized snapshot, one row per person, configurable columns.
- **GDPR DSAR** export: full data subject access request output, all sources cited, in a single archive.

Exports land in Garage `contact-ops-<tenant>-exports` with 14-day TTL on the signed URL. Re-request as needed.

If you ever want to leave the platform: every export above is sufficient to load your contacts into any standards-compliant alternative. Lock-in is not a strategy.

---

## 12. What's NOT in Contact-Ops

Contact-Ops is a registry. It is deliberately not:

- **A CRM.** Lead pipeline, deal stage, quota tracking, sales sequences, these live in a future CRM-Ops, separate downstream consumer.
- **An email verifier.** That's Data Intel at `verify.centerdeep.online`. Contact-Ops federates with Data Intel via RFC 8693 OBO; you don't manage verification jobs from inside Contact-Ops.
- **A marketing automation tool.** Mass send, A/B tests, drip campaigns, different product, not built here.
- **A phone book.** You can search by name, but the primary navigation is graph-based ("who knows whom") and agent-driven (Inbox approvals).

If you find yourself wanting CRM-style features: open a question on `/Users/aaronstransky/Documents/Contact-Ops-Open-Questions.md` and Aaron will decide whether CRM-Ops gets prioritized.

---

## 13. Where to learn more

- **Canonical design**: `/Users/aaronstransky/Documents/Contact-Ops-MCP-Design.md` (5,685 lines). Every architectural decision is in here.
- **Architecture summary**: `ARCHITECTURE.md` in this repo.
- **Developer workflow**: `docs/DEVELOPER_GUIDE.md`.
- **Ecosystem integration**: `INTEGRATION_GUIDE.md`.
- **AI agent orientation**: `CLAUDE.md`.

If something isn't clear, append to `/Users/aaronstransky/Documents/Contact-Ops-Open-Questions.md` and Aaron will resolve.
