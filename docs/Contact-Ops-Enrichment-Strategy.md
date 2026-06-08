# Contact-Ops Enrichment Strategy (agent-swarm synthesis, 2026-06-08)

> Produced by an 8-agent swarm (run wf_70192cbd-f4c): surveyed 4 leadgen repos + researched avatar/logo/people-data sources. Aligned to UC sovereignty + agent-first values.

I have a comprehensive set of surveys and research. This is a synthesis task, not an investigation task. Let me deliver the decisive strategy directly.

---

# Contact-Ops Enrichment Strategy

The whole strategy hangs on one principle that should already be familiar from your memory: **identity centralizes, domain federates, provenance is stamped on every field, and nothing third-party ever becomes canonical without confirmation.** Avatars and logos are the *visible* payoff (the 3D graph), but the real architecture is a provenance-stamped, sovereignty-first enrichment waterfall behind a `ResolverProvider` abstraction — the exact shape of your existing `BILLING_PROVIDER` (local/federated) pattern.

---

## 1. Enrichment Architecture — sources, fallback chains, keying, sync timing

### 1a. The core abstraction

Build **one Contact-Ops Enrichment Service** exposing a `ResolverProvider` interface (mirrors `BILLING_PROVIDER`). Three resolver *families* — `avatar`, `logo`, `firmographic/people` — each a **waterfall of providers** ordered by sovereignty → cost → reliability. **Every write carries `{value, source, method, fetched_at, confidence, is_default}`.** This is non-negotiable and you're already wired for it (ProvenanceBadge, canonical-vs-provisional). It is simultaneously your compliance story (GDPR Art. 14 source-disclosure) and your re-rank/refresh key.

Keying primitives computed **once per contact, stored**:
```
email_norm   = email.strip().lower()
email_sha256 = sha256(email_norm)        # Gravatar + Libravatar
email_md5    = md5(email_norm)           # legacy compat
domain       = eTLD+1(email_norm)        # PSL-aware; logo/favicon/firmographic key
```

### 1b. Avatar chain (per-person)

| # | Source | Key | Posture |
|---|--------|-----|---------|
| 1 | Google People / Workspace photo (`default=false`) | match contact by email inside the user's connected directory/other-contacts | User-consented, no third-party leak. **Only if user OAuth-linked Google** — and you already broker this via Keycloak `uchub` IdP tokens (`feedback_suite_sso_brokered_idp_tokens`), do NOT mint a new Google client. |
| 2 | Microsoft Graph `/users/{UPN}/photo` or `/contacts/{id}/photo` | UPN/email | User-consented, tenant-scoped. MS already has the broker token. |
| 3 | **Self-hosted Libravatar** (auto-redirects to Gravatar on miss) | `email_sha256` | Federated + self-hostable → lookup originates from *your* server, covers Libravatar+Gravatar in one call. This is the privacy-correct way to "use Gravatar." |
| 3b | *(only if you skip self-hosting Libravatar)* Gravatar `d=404` | `email_sha256` | Same coverage, but **leaks email-hash to Automattic** — must be opt-in + disclosed. |
| 4 | GitHub avatar | username (when on contact) | Public, permissive, overlaps Gravatar. No email→user lookup; skip if no handle. |
| 5 | Domain logo (see 1c) | `domain` | Org badge as weak personal stand-in. |
| 6 | **Self-hosted DiceBear** (`initials` or a CC0 style) | seed = email/name | **Terminal. Never fails, never leaks, MIT core.** Pick a CC0 style or `initials` to dodge CC-BY attribution. |

**Hard exclusions:** LinkedIn/Twitter/X photo scraping (ToS + GDPR — the hiQ consent judgment was $500K + injunction + destroy-all-data; stay out). Hosted unavatar.io / hosted ui-avatars as *primary* (egress + tiny limits).

### 1c. Logo chain (per-org, keyed on `domain`)

| # | Source | Posture |
|---|--------|---------|
| 1 | **BIMI DNS** (`default._bimi.<domain>` → `l=` SVG) | Brand-authoritative, native vector, free, **legally cacheable**. ~5% hit but exact-brand when it lands. Pure DNS+HTTPS GET, zero vendor. |
| 2 | **Wikidata P154 → Commons** | CC0/CC-BY-SA → **the one source you can legally download, cache, self-host, re-serve.** Often official SVG. Best sovereign hit for notable orgs. |
| 3 | **logo.dev** (`img.logo.dev/<domain>?token=`) | Broad-coverage workhorse, sanctioned Clearbit successor. Free = hotlink + required attribution link; **caching the bytes requires Pro $1,260/yr.** |
| 4 | **Self-hosted favicon extraction** (apple-touch-icon → manifest icons → `/favicon.ico`) | Fully sovereign, cacheable. Google/DuckDuckGo favicon endpoints as *soft* fallback only (unofficial, flatten transparency). |
| 5 | **Generated monogram** (first letter + brand-color chip) | Deterministic terminal fallback so every node renders. |

**Dead/excluded:** Clearbit Logo API (shut down Dec 8 2025 — purge any `logo.clearbit.com` refs). **Brandfetch** despite 60M catalog: terms *forbid programmatic download/caching, hotlink-only* ("scraping logos will lead to a block") — incompatible with a sovereign cache. Keep only as optional hotlink source if 1–4 miss.

### 1d. Firmographics + people-data chain (the substance)

This is the §4 hybrid from the research, **liability-layered** (legal liability forces the layering, not cost):

- **Layer 1 — BUILD/self-host (default-on, the foundation):**
  - Firmographics from **authoritative public registries**: SEC EDGAR + Companies House (free APIs) as the spine; `company_dns`-style Wikidata/SIC join. **OpenCorporates only via paid commercial license (£2,250/yr+)** if you need 140-jurisdiction breadth — never free-tier-then-redistribute into a closed product (share-alike trap).
  - **First-party signal = your best, cleanest data.** Meeting-Ops attendees/speakers + Customer-Ops leads already federate into Contact-Ops as SoT. Enrichment derived from your own customers' interactions has the cleanest legal basis and zero redistribution risk. Brokers can't sell it; competitors can't copy it. **Lean into this hardest.**
  - **Email discovery+verification:** self-hosted permutation generator + layered syntax→MX→SMTP-RCPT, with **explicit catch-all detection that downgrades confidence** (15–28% of B2B domains are catch-all; never assert validity). Run from warmed IPs in-cell.
  - **OSINT correlation** (SpiderFoot 200+ no-key modules / holehe / theHarvester) behind the same interface, with **collection hygiene enforced in code**: logged-out only, robots/opt-out respected, no gated-platform scraping, CNIL exclusion-list support.
- **Layer 2 — AGENT (on-demand, supervised):** Brigade enrichment agent for the **long tail + narrative** (what the company does, recent news, "who is this person here"). Constrained: structured output, **mandatory per-field source URLs**, reflexion pass, **confidence gate** → below-threshold returns "unverified, needs review", written as `provenance=agent_inferred`, **never canonical** until confirmed (mirror your Meeting-Ops 0.80 speaker-fingerprint threshold + your FAKE-tag cleanup discipline).
- **Layer 3 — BUY (opt-in flag, per-field, never bulk):** Hunter (cheapest, clean email find/verify) and optionally PDL on-prem, as last-resort fallbacks. **Hard rules:** opt-in per deployment (sovereign customers leave OFF); per-record calls only, never bulk-ingest/redistribute a broker graph; DPA on file; stamp `provenance=third_party:<vendor>`. **Never** bulk-buy Apollo/ZoomInfo — they literally "sell personal information"; redistributing breaks both sovereignty and GDPR.

### 1e. Sync timing — on-import vs on-demand vs background-outbox

Three modes, all through the same engine:

- **On-import (synchronous, cheap-only):** at contact creation/ingest, run **only zero-latency, zero-cost, zero-egress** resolvers — compute the hash/domain keys, generate the DiceBear terminal avatar + monogram logo immediately (so a node *always* renders instantly), and attempt BIMI/favicon (fast DNS/HTTP). Never block import on SMTP, agents, or paid APIs.
- **On-demand (user/agent triggered):** "Enrich this person/org" button (human) + MCP `enrich_person`/`enrich_org` tool (agent) — co-equal interfaces over one engine (your no-wrappers principle). Runs the fuller waterfall incl. Layer-2 agent + Layer-3 (if flag on). Returns provisional results with provenance for review.
- **Background-outbox sync (the workhorse):** a **bounded, fair, rate-limited queue** — reuse the **exact Arq pattern** Meeting-Ops already ships (bounded reprocess queue, per-source rate limiter, unique `job_id` Redis lock, idempotency hash to skip provably-identical re-enrichment, staleness threshold to decide refresh). On import you enqueue an enrichment job; the outbox drains it respecting per-source rate limits (SearXNG, SMTP, logo.dev monthly budget, Gravatar caching). **Cache every fetched image on your own Garage/S3** (content-hash + TTL + per-domain *negative* cache) — both for perf and to stop re-leaking lookups. Re-enrichment is driven by `data_freshness_score` / `staleness_threshold_days` (default 90), never a blind re-crawl.

---

## 2. What to REUSE from the leadgen apps (specific repos/assets)

All five repos are **internal Magic Unicorn / CenterDeep** (proprietary or unlicensed) — reuse is a business decision, not a license blocker, but **none is OSS; do not treat as freely copyable**, and **none of their PII CSVs may enter Contact-Ops** (rotate/quarantine per your secrets-rotation memory).

**The single best lineage:** `osint/` + `email_finder` originated in **`CenterDeep/loopnet-leads`** and were ported into multistate, outreach, etc. Lift the **canonical copy from loopnet-leads** and treat the others as confirmation it generalizes.

| Asset | Source repo / path | Feeds Contact-Ops as |
|---|---|---|
| **`osint/` package** (`email_finder.py` pattern-detect/generate + MX/SMTP/catch-all, `harvester_service.py`, `sherlock_service.py`) | `CenterDeep/loopnet-leads` `backend/app/services/osint/` (canonical); also in multistate + `MagicUnicorn/magic-unicorn-outreach` | Layer-1 OSINT + email-verification engine, wrapped as MCP enrichment tools (gated behind hygiene/opt-in) |
| **`company_matching.py`** — 3-level entity resolution (exact domain → fuzzy name SequenceMatcher ≥0.85 → website similarity) | `CenterDeep/loopnet-leads` `backend/app/services/company_matching.py` | The **dedupe / find-or-upsert idempotency** engine for person+org merge |
| **`deduplication.py`** — multi-algo (rapidfuzz + jellyfish, EIN/phone/domain, merge-group, `merged_into_id`/`merge_source`) | `CenterDeep/multistate-retirement-leads` + `magic-unicorn-outreach` | Richer merge engine; the `merged_into_id` pattern is already in every model |
| **`enrichment.py`** — engine: `DomainDiscovery`, `WebsiteScraper` (team/about incl. **photo_url**), `EmailGenerator`, `SourceInfo`/`EnrichedContact` dataclasses w/ confidence ladder (VERIFIED=100…LOW=40) | `CenterDeep/multistate-retirement-leads` `backend/app/services/enrichment.py` | The **citation-tracking backbone** + the team-page **avatar** scraper |
| **`scrape_logos.py`** — standalone logo extractor (og:image → apple-touch-icon → sized favicons → favicon.ico), httpx+BS4, no browser | `CenterDeep/multistate-retirement-leads` (root) | Drop-in **logo chain step 4** (self-hosted favicon extraction) |
| **`data_providers.py`** — `DataProvider(ABC)` plug-in framework + standardized **`PersonInfo`** dataclass (`social_profiles: Dict`, confidence, provider) + usage/cost tracking + typed errors + registry | `Northwind` (northwind) `backend/app/services/data_providers.py` | **This is the `ResolverProvider` abstraction, pre-built.** Adopt wholesale; it's the cleanest provider-plug-in in the fleet — wire Hunter/PDL adapters into it for Layer-3 |
| **`search_providers.py`** — provider abstraction (SearXNG default + Bright Data/Serper fallback, round-robin) | `multistate-retirement-leads` (also Northwind) | Sovereign search layer for the agent + dorking |
| **`local_enrichment.py`** — LinkedIn-via-SearXNG + Nominatim geocode + libphonenumber | `Northwind` | Sovereign Free-tier enrichment (SearXNG+Nominatim already in ecosystem) |
| **`contact_enrichment.py`** — SERP-snippet → **local-LLM** (Granite/llama.cpp) structured extraction + `parse_permit_name()` messy-name normalizer | `Northwind` + `magic-unicorn-outreach` | The Layer-2 agent extraction step over your shared inference gateway |
| **`email_detection.py`** — free-vs-Workspace/M365/self-hosted classifier (curated MX/free-domain lists) | `magic-unicorn-outreach` | Standalone email-provenance signal per contact |
| **Per-field provenance schema** — `db/002`+`003` migrations: `{value, *_confidence, *_source, *_verified_at}` per field + **`EnrichmentHistory`** audit table (`search_queries`, `changes{field:{old,new}}`, raw_response) | `magic-unicorn-outreach` `db/` | **Port the schema, not the tables.** This is the single best architectural reuse — it IS your provenance model. Also `CompanyEnrichment`/`Contact` `photo_url`+`logo_url`+`sources` JSON + freshness fields from multistate |
| **`NEO4J_GRAPH_SCHEMA.md`** (64KB) — Company/Contact/**SocialProfile**/Organization nodes + `WORKS_AT`/`WORKED_AT`, Postgres-SoT→graph async-sync, Cypher indexes, multi-hop queries | `CenterDeep/loopnet-leads` | **Blueprint for the CO knowledge graph** (adapt to your stack) |
| **`rate_limiter.py` / `enrichment_cache.py` / `intelligence_cache_manager.py`** | `loopnet-leads` | Per-source rate limiting + shared-cache staleness ("don't re-enrich") for the background outbox |
| **Two-tier Intelligence-cache pattern** (per-tenant rows + shared cross-tenant `*Intelligence` cache w/ provenance JSON, `enrichment_steps_taken` audit array, `staleness_threshold_days`, `is_blocked` GDPR flag, lookup/user counts) | `loopnet-leads` `company_intelligence.py`/`contact_intelligence.py` | **Directly mirrors your "identity centralizes / domain federates" doctrine** — the model for cross-tenant shared enrichment |
| **Compliance primitives** — `is_suppressed`/`do_not_contact`/`is_blocked`, `bounce_status`, `suppression.py`, email role-based/disposable flags | loopnet + multistate + Northwind | Consent hygiene; carry into CO as-is |

**Quarantine behind a compliance gate (do NOT surface as default customer features):** the people-search scrapers — `osint_search.py`/`browser_osint.py` (TruePeopleSearch/Whitepages w/ UA-rotation to evade bots, Northwind), and Sherlock/theHarvester SERP-dorking. ToS-violating + CCPA/scraped-PII exposure. Keep behind opt-in + rate-limit + treat as logged "agent action," consistent with your suite leadgen inventory (OSINT OK, scraped-gated sources gated/metered).

**The avatar/logo gap (critical):** *none* of these repos fetch person avatars or company logos as a graph asset (multistate's `enrichment.py` scrapes a team-page `photo_url` and `scrape_logos.py` pulls favicons — that's the closest, and it's partial). **The image-resolution layer (§1b/§1c) is net-new.** But the repos give you the *discovery plumbing that feeds it for free*: enrichment already resolves the **company domain** + **per-person social/LinkedIn URLs** — exactly the inputs the avatar/logo resolvers key on.

---

## 3. Avatar/logo wiring for the 3D graph nodes

The graph is **Postgres-as-SoT → graph store, async-synced** (your existing topology; loopnet's `NEO4J_GRAPH_SCHEMA.md` is the blueprint, adapt to your KG stack). Node types: **Person**, **Organization**, plus **SocialProfile** as a discovery substrate. Wiring:

- **Each node carries a resolved image pointer, not a hot-link.** Store on the node: `image_url` (a **Contact-Ops-served** URL into your Garage/S3 cache, never a third-party URL), `image_source`, `image_fetched_at`, `image_is_default` (bool). The graph renderer reads `image_url` only.
- **Normalize every asset on ingest** to a **square WebP/PNG at one or two sizes** (e.g. 64px + 256px) so the renderer never deals with raw SVG/transparency/aspect quirks. BIMI/Wikidata SVGs get **rasterized** at cache time (SVG Tiny P/S is script-free, ≤32KB — safe). Person avatars get circle-cropped; org logos get a padded square chip.
- **Guaranteed-render rule:** because on-import always generates a DiceBear avatar (Person) and a monogram chip (Org) **before** any async enrichment, **every node has a valid `image_url` from creation** — the graph is never blank, never shows broken-image. Real photos/logos *upgrade* the node when the background outbox resolves them (re-rank by source priority; flip `image_is_default=false`).
- **Person node:** avatar chain (§1b) → circle avatar. If terminal, DiceBear `initials` seeded by email/name (deterministic → same person always looks identical across sessions/devices).
- **Org node:** logo chain (§1c) → square chip. If terminal, monogram chip in the org's brand color (or a neutral). BIMI/Wikidata hits get a subtle "verified source" ring (you already have a ProvenanceBadge idiom — surface it on hover).
- **SocialProfile nodes feed, don't render:** Sherlock/LinkedIn-discovered social URLs populate SocialProfile nodes whose only graph job is to *supply keys* (GitHub username → GitHub avatar; nothing for LinkedIn since scraping is excluded). They can render as small satellite chips off a Person if you want, using the platform's own icon.
- **Edges:** `WORKS_AT`/`WORKED_AT` (Person→Org) drive the org-logo's gravitational clustering; this is where the "people avatars orbiting org logos" visual comes from.
- **Provenance on hover:** every node exposes `{image_source, fetched_at}` so a user (or DSAR responder) can see *where the face/logo came from* — same discipline as every other field.

---

## 4. Build-vs-Buy calls (costs + compliance, tuned to sovereignty/self-host)

| Capability | Call | Rough cost | Compliance flag |
|---|---|---|---|
| **Avatars — first-party (Google/MS)** | **BUILD** (reuse Keycloak broker tokens) | $0 | Cleanest. User-consented, no egress, no new OAuth client (`feedback_suite_sso_brokered_idp_tokens`). |
| **Avatars — Libravatar** | **BUILD/self-host** (Django node) | hosting only | Federated; lookup originates from your infra → blunts Gravatar leak. |
| **Avatars — Gravatar direct** | **BUY (free)** *only if not self-hosting Libravatar* | $0 (paid $100/mo = SLA only) | **Leaks email-hash to Automattic; hash is brute-forceable.** Opt-in + privacy-policy disclosure. Image endpoint is **unthrottled** but cache anyway. |
| **Avatars — terminal** | **BUILD/self-host DiceBear** | $0 | MIT core; **pick CC0 style or `initials`** (some styles are CC-BY → attribution). Zero egress. |
| **Logos — BIMI + Wikidata** | **BUILD** | $0 | **Only legally cacheable/self-hostable logo sources.** Wikidata CC-BY-SA → attribute. Best sovereign layer. |
| **Logos — broad coverage** | **BUY logo.dev** | Free=500k/mo **+ attribution link**; **Pro $1,260/yr to legally cache bytes** | Free tier = hotlink + visible "Logos provided by Logo.dev". Pro the moment you need offline/air-gapped or want the runtime dependency gone. |
| **Logos — Brandfetch** | **AVOID as backbone** | Free 500k/mo | **Forbids download/caching, hotlink-only** ("scraping → block"). Incompatible w/ sovereign cache. Optional hotlink only. |
| **Logos — Clearbit** | **DEAD** | — | Shut down Dec 8 2025. Purge refs. |
| **Firmographics — registries** | **BUILD** (SEC EDGAR, Companies House) | $0 (eng time) | Authoritative legal-entity data, free. Public-register ≠ free reuse for *personal* data — still apply GDPR. |
| **Firmographics — OpenCorporates** | **BUY license** if needed | from **£2,250/yr** | Free tier = 200/mo + **share-alike** (would force open-sourcing your product). License removes share-alike. |
| **People-data — first-party** | **BUILD** (Meeting/Customer-Ops federation) | $0 (already built) | Cleanest legal basis, zero redistribution risk. **Your moat.** |
| **Email find/verify** | **BUILD** (reuse `osint/email_finder`) | $0 (warmed IPs) | SMTP-RCPT can trip anti-abuse → rate-limit; catch-all caps confidence. |
| **People-data — long tail** | **BUILD agent** (Brigade, local LLM) | ~free inference + latency | ~24% hallucination → confidence-gate, `agent_inferred` never canonical. Capture source URLs (Art. 14). |
| **People-data — last-resort** | **BUY Hunter / opt-in PDL** | Hunter $49/mo (2k cr, ~$0.0245/email) → $299 Scale; PDL ~$0.20–0.55/match | **Opt-in flag (OFF for sovereign), per-record only, DPA on file, stamp `third_party:<vendor>`.** Never bulk. |
| **People-data — broker bulk** | **AVOID** | ZoomInfo $15k+/yr, Apollo bulk | Apollo/ZoomInfo "sell personal information" → redistributing = inherit data-broker liability + break sovereignty + GDPR. Hard no. |

**Cross-cutting (build once, all layers):** a real **DSAR/erasure pipeline** (opt-out purges across all layers incl. cached third-party images within 30 days / 24–48h for opt-outs — itself a *selling point* for a sovereignty brand); per-deployment **legal-basis + LIA config**; **Art. 14 source-disclosure** surfaced in UI; **collection hygiene as enforced code** (logged-out, robots/CNIL-exclusion, no gated platforms, no fabricated accounts).

---

## 5. Phased rollout (ship order — opinionated)

**Phase 0 — Provenance + abstraction spine (ship first, unblocks everything).**
Port the per-field `{value, confidence, source, verified_at}` schema + `EnrichmentHistory` audit table from `magic-unicorn-outreach` `db/002`+`003`. Stand up the `ResolverProvider` interface by lifting Northwind's `data_providers.py` + `PersonInfo`. Add the keying primitives (hash/domain) + the two-tier Intelligence-cache pattern (loopnet). **No external calls yet** — just the skeleton every field flows through.

**Phase 1 — Graph never blank (the visible win, all-sovereign, zero-egress).**
Ship the **terminal + free layers only**: DiceBear avatars + monogram logos generated on-import (guaranteed render), plus BIMI + Wikidata + self-hosted favicon (`scrape_logos.py`) + Libravatar self-host. Wire `image_url`/`image_source`/`image_is_default` onto Person/Org nodes; normalize to square WebP in Garage. **Result: a populated, on-brand 3D graph with zero third-party egress and zero spend.** This is demoable and fully sovereign.

**Phase 2 — Background-outbox enrichment engine.**
Stand up the Arq bounded/fair/rate-limited outbox (reuse the Meeting-Ops queue pattern + `rate_limiter.py`/`enrichment_cache.py`). Wire Layer-1 BUILD: `osint/email_finder` (email find/verify w/ catch-all downgrade), `email_detection.py`, registry firmographics (EDGAR/Companies House), `company_matching.py`+`deduplication.py` for find-or-upsert. First-party federation already feeds it. Re-enrichment driven by staleness. **Nodes start upgrading from defaults to real data.**

**Phase 3 — Agent-first long tail + first-party avatar/logo upgrade.**
Add the Brigade enrichment agent (Layer-2) via `contact_enrichment.py` SERP→local-LLM pattern, confidence-gated, `agent_inferred`. Wire the **Google People / MS Graph** avatar steps using existing Keycloak broker tokens (top of the avatar chain — real faces for the user's own network). Expose `enrich_person`/`enrich_org` as **both MCP tool + GUI button** (co-equal interfaces).

**Phase 4 — Opt-in commercial fallback + compliance hardening.**
Wire Hunter (+ optional PDL) adapters into the existing `ResolverProvider`, behind a **per-deployment env flag (default OFF)**, per-record only, DPA-gated, `third_party:<vendor>`-stamped. Add logo.dev (free→Pro if caching needed). Ship the **DSAR/erasure pipeline**, LIA config, Art. 14 disclosure UI, and the in-code collection-hygiene gate. This is what makes it safe to point at non-sovereign customers.

**Defer indefinitely / never:** people-search scrapers as default features (quarantine behind agent-action opt-in); any **bulk** broker ingest (Apollo/ZoomInfo); LinkedIn/Twitter photo scraping.

**One-line decision rule:** *Build the registry + first-party + OSINT + verification core and the all-sovereign avatar/logo layer (default-on); add a constrained, provenance-stamped live-web agent for the long tail; keep commercial APIs + paid logo caching as opt-in, per-field, DPA-gated fallbacks behind the abstraction — and never bulk-ingest or redistribute a broker's person graph. Cost and quality favor the hybrid; legal liability is what forces the layering.*

Reusable code lives in five internal repos (canonical OSINT lineage = `git.unicorncommander.ai/CenterDeep/loopnet-leads`; provider-abstraction = Northwind `data_providers.py`; provenance schema = `MagicUnicorn/magic-unicorn-outreach` `db/002`+`003`; logo/avatar discovery plumbing exists, image-resolution layer is net-new).


---

# Appendix A — Leadgen repo surveys (raw)


## Survey 1

Survey complete, temp dir cleaned up. Here is the synthesis.

---

# Survey: CenterDeep/loopnet-leads → reusable for Contact-Ops enrichment

Cloned via `git clone https://git.unicorncommander.ai/CenterDeep/loopnet-leads` over ssh on `magicunicorn` (auth succeeded with stored creds — no API fallback needed). Python/FastAPI + Celery + Postgres + Neo4j; React frontend. Inspected structure only, no large datasets pulled. `/tmp/survey-loopnet` removed.

This is a **commercial-real-estate lead-enrichment CRM**: ingest a LoopNet "Listing View Activity By Company Name" Excel (company names only) → AI agents + 100%-free OSINT discover decision-makers, emails, social profiles → verify → graph. It is essentially a **company-first contact-enrichment engine** — extremely close to what Contact-Ops needs.

## (1) PEOPLE + COMPANY data/fields it holds

**Two-tier model (this is the important architectural takeaway).** Per-tenant rows AND a shared cross-tenant intelligence cache — directly mirrors Contact-Ops' "identity centralizes / domain federates" doctrine.

- **Per-tenant** `Company` / `Contact` (`backend/app/models/company.py`, `contact.py`): user_id (Keycloak) + org_id scoping.
- **Shared cache** `CompanyIntelligence` / `ContactIntelligence` (`company_intelligence.py`, `contact_intelligence.py`): deduped-by-domain/email canonical records reused across all tenants, with provenance + freshness + GDPR-block flags.

**Person fields** (`ContactIntelligence`): email (unique) + email_normalized, full_name / first_name / last_name, role (job title), phone, linkedin_url, is_verified + email_verification_status + bounce_status (soft/hard), confidence_score (0–1), primary_source (website/linkedin/inferred/serp) + `source_details` JSON (e.g. `{source, page, pattern}`), discovery_date, last_verified_at, last_contacted_at, verification_attempts, user_contact_count, is_suppressed, is_primary_contact.

**Company fields** (`CompanyIntelligence`): canonical_company_name, domain (unique), website_url, industry, company_size, revenue_estimate, description, linkedin_url, enrichment_confidence, `enrichment_sources` JSON + `enrichment_steps_taken` ARRAY (auditable), first/last_enriched_at, last_verified_at, staleness_threshold_days (default 90) + staleness math, lookup_count/user_count, is_stale, is_blocked (GDPR). Per-tenant `Company` adds LoopNet-specific cruft (loopnet_company_id, property_type, Fortune-1000/Forbes-2000 flags, 30-day view metrics, priority_score) — LoopNet-specific, less reusable.

## (2) Enrichment SOURCES — all OSINT / scraped / public, ZERO purchased data

The repo's headline (`FREE-OSINT-COMPLETE.md`, `osint/__init__.py`) is explicit: **"100% Free/Open-Source tools only, no paid APIs."** Replaces Hunter.io/Clearbit with home-grown OSINT. Sources:

- **Website scraping** — `agents/website_scraper.py` (BeautifulSoup+regex), `llm_website_scraper.py` (LLM+Instructor structured extraction), `playwright_scraper.py` (JS-rendered sites). Scrapes company sites for emails/phones/people.
- **theHarvester** — `osint/harvester_service.py`: harvests emails from Google/Bing/LinkedIn/Yahoo public results.
- **Email pattern inference** — `agents/email_detective.py` + `osint/email_finder.py`: detect `{first}.{last}@domain` pattern from known emails, generate candidates (10 common patterns).
- **Email verification** — `agents/email_validator.py` + `email_finder.py`: syntax, disposable-domain reject, DNS/MX, SMTP deliverability, catch-all detection (no paid API).
- **Sherlock** — `osint/sherlock_service.py`: social profiles across 300+ platforms (LinkedIn/Twitter/GitHub/FB/IG) by username.
- **LinkedIn research** — `agents/enhanced_linkedin_researcher.py`: SERP-based (Serper.dev → CenterDeep/SearXNG → DirectSearch fallback chain) + LLM extraction.
- **Location-aware** — `osint/multi_source_osint.py`: GoogleMapsService (Places API), Twitter, Facebook business, state business directories.
- **Orchestration** — `agents/orchestrator.py` runs the full pipeline + dedup. **LLM is provider-agnostic via Ops-Center** (`services/ops_llm_client.py`, `smart_llm_client.py`) — already plugs into the UC inference gateway, not raw OpenAI.

Provenance verdict: **OSINT/scraped/public-record + inferred**. Nothing purchased; no proprietary data broker. Inputs are public web + DNS/SMTP + user-uploaded LoopNet exports.

## (3) DIRECTLY REUSABLE for Contact-Ops enrichment

**Tier 1 — lift almost verbatim (engine is generic, not LoopNet-coupled):**
- `backend/app/services/osint/` — entire dir: `email_finder.py` (pattern detect/generate + DNS/MX/SMTP/catch-all verify), `harvester_service.py`, `sherlock_service.py`, `multi_source_osint.py`.
- `backend/app/agents/` — `email_detective.py` (pattern engine), `email_validator.py`, `website_scraper.py` / `llm_website_scraper.py` / `playwright_scraper.py`, `enhanced_linkedin_researcher.py`, `osint_agent.py`, `orchestrator.py` (the pipeline + dedup coordinator).
- `backend/app/services/company_matching.py` — **the dedupe/entity-resolution engine** Contact-Ops needs: 3-level match (exact domain → fuzzy canonical-name via SequenceMatcher ≥0.85 → website similarity) with confidence+method. Maps onto CO's find_person/upsert idempotency.
- `services/enrichment_cache.py` + `intelligence_cache_manager.py` + `enrichment_service.py` — shared-cache hydration, staleness/freshness, "don't re-enrich" logic.
- `services/rate_limiter.py` — per-source rate limiting for OSINT calls.

**Tier 2 — schema/design to port, not code:**
- The **two-tier Intelligence-cache pattern** (provenance JSON, `enrichment_steps_taken` audit array, staleness_threshold, is_blocked GDPR flag, lookup/user counts). This is the model Contact-Ops should adopt for cross-tenant shared enrichment.
- `NEO4J_GRAPH_SCHEMA.md` (64KB) — **directly relevant to the CO knowledge graph**: Company / Contact / **SocialProfile** / Organization nodes + `WORKS_AT`/`WORKED_AT` relationships, Postgres-as-SoT → Neo4j async-sync model, full Cypher indexes/constraints, sample multi-hop queries. A ready blueprint for CO's people-graph.
- `LOCATION-AWARE-ENRICHMENT.md`, `OSINT-MCP-INTEGRATION-PLAN.md` (an MCP-exposure plan for the OSINT tools — aligns with CO's agent-first MCP ingest door).

**On the avatars + company-logos requirement (important gap):** the codebase captures **zero** avatar/logo/photo/favicon today — grep for `avatar|logo|photo|favicon|gravatar|clearbit|image_url|picture` across `backend/app` returns nothing. BUT the substrate is here: the Neo4j **SocialProfile** node + Sherlock/LinkedIn discovery already resolve per-person social URLs and per-company domains/LinkedIn. Avatars/logos are a thin enrichment layer on top of what's already discovered (person avatar ← LinkedIn/Gravatar-by-email; company logo ← domain favicon / Clearbit-logo-style derivation). So this repo gives you the discovery plumbing to feed the graph; the image-fetch step is net-new for CO.

## (4) Provenance + licensing / compliance flags

- **License: PROPRIETARY** — `LICENSE` = "Copyright Magic Unicorn Inc., all rights reserved, no derivative works without written consent." Same owner as Contact-Ops (Magic Unicorn / UnicornCommander), so internal reuse is a business decision, not an external-license blocker — but it is NOT OSS; do not treat as freely copyable.
- **Compliance is already first-class in the model** — reusable as-is: `is_suppressed` (global suppression / CAN-SPAM opt-out) on contacts, `is_blocked` ("Don't re-enrich, e.g. GDPR") on company intelligence, a `suppression.py` model, bounce_status tracking. Good hygiene to carry into CO.
- **Scraping/OSINT risk to flag for CO:** website scraping (incl. Playwright), theHarvester, and **Sherlock** social-profile enumeration are gray-area for a sovereign people-intelligence product — ToS-violation and privacy exposure (Sherlock especially is people-search-y). SMTP catch-all/deliverability probing can trip anti-abuse. Recommend gating these behind explicit per-source toggles + consent/audit (the `enrichment_steps_taken` audit array already supports provenance logging).
- **Data is OSINT/scraped/inferred, never purchased** — clean from a "no data-broker dependency" standpoint, consistent with the sovereign positioning.
- Secrets note: the repo root carries `.env.production` and `.env.backup.*` committed (Stripe/Lago/Serper/Ops-Center keys) — not relevant to reuse, but a rotation flag if any are live.

**Bottom line:** the `osint/` services, the `agents/` pipeline + orchestrator, `company_matching.py` (dedupe), the shared-Intelligence two-tier schema, and `NEO4J_GRAPH_SCHEMA.md` are the directly-portable assets for Contact-Ops enrichment + people-graph. Avatars/logos are not built but the social-profile/domain discovery that would source them is.

## Survey 2

Survey complete, repo cloned via the stored bigboy git credential (no auth fallback needed), and `/tmp/survey-multistate` cleaned up. Findings below.

---

# Survey: CenterDeep/multistate-retirement-leads — reuse for Contact-Ops enrichment

A FastAPI + Postgres (asyncpg/SQLAlchemy 2.0) 401(k) sales-intelligence platform. Core thesis: take DOL Form 5500 filers + state business registries, find companies *without* plans, then **enrich each company with decision-maker contacts via 100% OSINT/no-paid-API methods**. The enrichment half is directly transplantable to Contact-Ops; the lead-scoring/retirement-domain half is not.

## (1) People + Company data/fields it holds

**People — `contacts` table (`backend/app/models.py` `Contact`)** — this is the richest reusable asset:
- Identity: `name`, `title`, `email`, `phone`, `linkedin_url`, `twitter_url`, **`photo_url`** (scraped from team pages — your avatar requirement).
- **Per-field confidence (0–100)**: name/title/email/phone/linkedin/twitter `_confidence`.
- **Per-field source citation + verified-at**: `*_source` (URL where found) + `*_verified_at` for every field — i.e. column-level provenance.
- Email validation depth: `email_valid`, `email_deliverable`, `email_disposable`, `email_free_provider`, `email_mx_records[]`, `email_smtp_response`, `email_pattern`, `generated_emails[]`.
- Scoring/triage: `is_decision_maker`, `contact_score`, `contact_priority`, `is_primary`, `data_quality_score`, `quality_grade` (A–F), `is_garbage`, `quality_flags[]`, `outreach_strategy`.

**Company — `company_enrichments` table (`CompanyEnrichment`)**:
- `domain`, `website`, **`logo_url`** (favicon/og:image — your company-logo requirement), `description`, `employee_count`, `industry_discovered`.
- Full social set: `linkedin_url`, `twitter_url`, `facebook_url`, `instagram_url`, `youtube_url`, `github_url`, `tiktok_url`.
- `found_emails[]`, `sources` (JSON `[{field,url,scraped_at}]`), data-freshness fields (`data_freshness_score`, `last_verified_at`, `needs_refresh`) for re-enrichment decay.
- Domain-specific (NOT reusable): provider/payroll detection, job-intent/hiring signals, switch-candidate flags.

**Source business entities (domain-specific, skip)**: `Form5500Filer` (DOL plan filings — sponsor name/EIN/phone/address, participants, assets), `CABusiness` (SoS registries — entity name/type/agent/address). These are retirement-lead substrate, not contact enrichment.

## (2) Enrichment sources/APIs/scrapers — provenance class

Almost entirely **OSINT / scraped / public-record** — no purchased contact databases.
- **OSINT toolchain** (`backend/app/services/osint/`, ported from a prior "LoopNet-Leads" project): `harvester_service.py` (theHarvester over google/bing/linkedin/yahoo), `sherlock_service.py` (Sherlock — social profiles across 300+ platforms by username), `email_finder.py` (`FreeEmailFinder`: pattern detection + DNS/MX + SMTP-port-25 verify + catch-all detection). All shell out to OSS tools; "100% open source, no paid APIs."
- **Website scraping** (`enrichment.py`, `advanced_scraper.py`): httpx + BeautifulSoup; advanced path adds Playwright (JS render) + Trafilatura + pdfplumber. Crawls `/team`, `/about`, `/leadership`, `/contact` for names/titles/photos.
- **Search layer** (`search_providers.py`): self-hosted **SearXNG** (default, privacy-first) with optional **Bright Data SERP** and Serper (job-intent) as commercial fallbacks.
- **LLM extraction** (`llm_enrichment.py`): pluggable OpenRouter/Anthropic/Ollama/MLX to extract structured contacts from scraped HTML; local Granite 3.3 / NuExtract favored. Research doc `docs/LLM_ENRICHMENT_RESEARCH.md` flags NuExtract-2B as MIT + purely extractive (no hallucination).
- **Email verification**: an internal "Center Deep Verify" service (`VerificationStatus` enum + `verification_response`) — a self-hosted email-verify integration, not a paid API.
- **Public-record ingestion** (company side only): DOL Form 5500 bulk CSV, CA SoS / BizFile scraper, MD/NJ web scrapers, and **Socrata SODA API** (`sodapy`) for OR/CO/CT/NY open-data registries.

## (3) Directly reusable for Contact-Ops enrichment

High-value, near-drop-in (all paths under `backend/app/services/`):
- **`enrichment.py`** — the engine. `DomainDiscovery` (company-name→domain over TLD/suffix permutations), `WebsiteScraper` (team/about extraction incl. names+titles+**photos**), `EmailGenerator`, and the `SourceInfo`/`EnrichedContact`/`EnrichedEmail` dataclasses with the confidence-constant ladder (VERIFIED=100/HIGH=80/MEDIUM=60/LOW=40). This is the citation-tracking backbone.
- **`scrape_logos.py`** (root) — standalone company-**logo** extractor: og:image → apple-touch-icon → sized favicons → favicon.ico. Maps straight to your KG company-logo need; httpx+BeautifulSoup, no browser.
- **Contact **photo_url**/team-card scraping** in `enrichment.py` (`TEAM_PAGE_PATTERNS`, `find_team_members`, team-member card regex) — maps to your KG people-avatar need.
- **`osint/` package** (`email_finder.py`, `harvester_service.py`, `sherlock_service.py`) — self-contained, already abstracted; wrap as Contact-Ops MCP enrichment tools.
- **`email_patterns.py` + `email_validator.py`** — per-domain pattern learning/storage + syntax/MX/SMTP/disposable/free-provider validation. Reusable as-is for email enrichment + dedupe keys.
- **`linkedin_discovery.py`** — SearXNG-based LinkedIn-URL discovery for a (person, company) or company, with confidence + match-reason.
- **`contact_scoring.py`, `data_quality.py`, `quality_validation.py`** — decision-maker detection, quality grading (A–F), garbage/placeholder detection. Good for Contact-Ops quality gates.
- **`deduplication.py`** — multi-algorithm entity resolution (exact/fuzzy via **rapidfuzz**+**jellyfish**, EIN/address/phone/domain match, merge-group management). The `merged_into_id`/`merge_source` pattern is already in every model. Directly relevant to Contact-Ops person/company merge.
- **`search_providers.py`** — clean provider-abstraction (SearXNG/Bright Data, round-robin+fallback) Contact-Ops can adopt wholesale.
- **Schema patterns to copy, not the tables**: the per-field `{value, confidence, source_url, verified_at}` shape on `Contact`, plus `sources` JSON arrays and freshness-decay fields — this is the provenance model Contact-Ops wants for "people intelligence."

**Datasets**: small reference exports exist (`data/exports/contacts.csv` ~24KB / `company_enrichments.csv` ~60KB / `schema.sql` ~28KB / `enrichment_data.sql` ~296KB) — useful as seed/test fixtures and to read the full DDL. The large files (Form 5500 CSVs ~245MB) are retirement-domain source data, **not** contact data — do not import. Sample contact row is real PII (e.g., `Gavin Fallon, SVP Solutions, REMOVED`, conf 60/70).

## (4) Provenance + licensing/compliance flags

- **No LICENSE/COPYING/NOTICE file anywhere** in the repo — code is unlicensed (default: all-rights-reserved). It's a CenterDeep/internal repo so fine to reuse internally, but there's no explicit grant; treat as proprietary-internal.
- **Ported code lineage**: the entire `osint/` package and `email_finder` are explicitly "Ported from LoopNet-Leads project" — confirm that origin is also internal/owned before lifting, and carry NuExtract's MIT note if that model path is adopted.
- **Data is real PII gathered via scraping/OSINT** (names, work emails, phones, photos, LinkedIn). The exported CSVs contain live personal data — handle under the same PII/retention rules as Contact-Ops, don't commit them into Contact-Ops, and they need rotation/cleanup like the other leaked-data items in memory.
- **Scraper compliance is self-flagged**: `md_nj_ingestion.py` header explicitly warns "use responsibly with rate limiting… for production consider purchasing bulk data" and `bizfile_scraper.py` deliberately hits a *gated* API's public search interface to bypass approval. SMTP port-25 probing (`email_finder`) and ToS-restricted scraping (LinkedIn via theHarvester/Sherlock, job boards) carry ToS/abuse risk — Contact-Ops should keep these behind the same OSINT-vs-restricted boundary noted in your suite leadgen inventory (OSINT OK, scraped-gated sources gated/metered).
- **Provider/source attribution is preserved** (every field carries its source URL + scraped_at), which is a compliance asset — Contact-Ops should retain this when ingesting.

**Bottom line for synthesis**: lift `enrichment.py` (engine + SourceInfo model), `scrape_logos.py` (logos), the team-page photo scraping (avatars), the `osint/` package, `email_patterns`/`email_validator`, `deduplication.py`, `search_providers.py`, and the per-field confidence+citation+freshness **schema pattern** from `Contact`/`CompanyEnrichment`. Leave behind Form 5500/retirement scoring, provider/payroll/job-intent detection, and the large DOL datasets. No license exists — internal reuse only; PII + gated-scraper compliance flags apply.

## Survey 3

Cleanup confirmed (the exit-2 is just `ls` reporting the now-absent dir). Survey complete.

---

# Northwind Lead-Gen survey — reusable assets for Contact-Ops contact enrichment

Clone succeeded via the bigboy stored credential (no API fallback needed). Repo = a B2C CRM for a Charleston SC remodeling contractor; FastAPI/SQLAlchemy backend + React frontend; ~770 leads in the export. `/tmp/survey-northwind` cleaned up.

## (1) PEOPLE + COMPANY data/fields it holds

**People** — two overlapping models:
- `backend/app/models/lead.py` (the rich one): name, email, phone, preferred_contact; **email verification block** (status/score 0-100/risk/deliverable/disposable/role-based/verified_at) and **phone verification block** (status/score/risk/type [mobile/voip/fixed]/carrier/E.164/verified_at) — both stamped "via CenterDeep Verify API"; address+geocode (lat/lon); source attribution (source, source_detail, source_url, source_id); scoring (score, JSON `score_breakdown`, priority_tier); **enrichment tracking** (enriched_at, enrichment_source, enrichment_confidence 0-1, enrichment_attempts, enrichment_last_error); contact-channel recommendation (call/email/text + reason + confidence); tags(JSON), notes.
- `backend/app/models/contact.py` (lighter, person-centric): first/last, email, phone, phone_secondary, **role** (homeowner/spouse/property_manager/contractor), address, contact-preference fields, `do_not_contact` bool, **`facebook_url` + `linkedin_url`**, tags, notes.

**Company / org-like data** — there is no real "company" entity. The closest is `backend/app/models/property.py` (owner_name, owner_occupied, owner_mailing_address, assessed/market value, last_sale, parcel_id, permit_history JSON, year_built→home_age) and, from the provider layer, person `employer` / `job_company_name` + `occupation`/`job_title` fields. The KG node `referral_source` is org-or-person but holds only a name + referral counts.

## (2) Enrichment SOURCES/APIs and provenance type

Two-tier design: **self-hosted OSINT-first, paid-API fallback.**

- **Self-hosted (OSINT/scraped/public):** `local_enrichment.py` — LinkedIn profiles via self-hosted **SearXNG** (`site:linkedin.com/in/` dorking), geocoding via **Nominatim/OSM**, phone validation/carrier/type via **libphonenumber (`phonenumbers`)**, spatial neighbor-clustering. `osint_search.py` scrapes free people-search sites — **TruePeopleSearch, FastPeopleSearch, SearchPeopleFree, Whitepages free tier** (BeautifulSoup + rotating user-agents). `browser_osint.py` = Playwright headless-browser fallback for the same when bot-blocked. `detect_social_intent.py` SERP-dorks Pinterest/Houzz/HGTV for remodel intent. Lead seed data itself is **public-record building permits** (the sample row is a pool permit; owner name parsed from permit text).
- **Paid/commercial (purchased) — pluggable, behind a clean abstraction in `data_providers.py`:** **PeopleDataLabs, Pipl, FullContact** (B2C identity resolution → emails/phones/social), **Shovel.ai + Batch.com** (property/owner), plus **Brightdata** SERP+residential-proxy (`brightdata_service.py`) and **Serper.dev** Google Search (`contact_enrichment.py`). Contact extraction from SERP snippets is done by a **local Granite 3.3 2B on llama.cpp** (dual Tesla P40s, round-robin), benchmarked 4x better than GPT-4o-mini at $0.
- **Verification:** `email_verification.py` calls an internal **"Center Deep Verify"** service (SMTP deliverability + cataloguing); phone via libphonenumber.

**No image/avatar/logo enrichment anywhere.** Social presence is captured as **URLs only** (LinkedIn/Facebook/Twitter strings) — no Gravatar, no profile-photo fetch, no Clearbit-style company-logo source.

## (3) DIRECTLY REUSABLE for Contact-Ops

- **`backend/app/services/data_providers.py`** — the single highest-value asset. A clean `DataProvider(ABC)` plug-in framework with a **standardized `PersonInfo` dataclass** (full/first/last, email_addresses[], phone_numbers[], addresses[], age, gender, occupation, employer, **`social_profiles: Dict[str,str]`**, relatives, confidence_score, provider, raw_data), per-provider `ProviderUsageStats`/cost tracking, typed errors (NotConfigured/RateLimit/APIError), TTL caching, and a `DataProviderManager` registry. Adapters for PDL/Pipl/FullContact/Shovel/Batch are ready to lift — Contact-Ops can drop these straight into its "domain federates enrichment" model and add providers without touching callers.
- **`backend/app/services/local_enrichment.py`** — sovereign/zero-cost LinkedIn-via-SearXNG + Nominatim geocode + libphonenumber validation. Aligns with Contact-Ops' sovereign/on-device Free-tier story; SearXNG + Nominatim already exist in the ecosystem.
- **`backend/app/services/osint_search.py` + `browser_osint.py`** — reusable people-search scrapers (HTTP + Playwright fallback) with UA rotation, caching, confidence scoring. **Compliance-flagged — see (4).**
- **`backend/app/services/contact_enrichment.py`** — SERP-snippet → **local-LLM contact extraction** pattern (Granite/llama.cpp, OpenAI-compatible, load-balanced). Reusable as Contact-Ops' agent-first extraction step over the shared inference gateway. Also has a genuinely useful `parse_permit_name()` / `build_search_name()` normalizer for messy "LASTNAME, FIRST & SPOUSE" inputs.
- **`backend/app/services/email_verification.py`** + the lead model's email/phone verification field set — a ready schema + client for deliverability/risk scoring to fold into Contact-Ops' person quality signals.
- **`backend/app/routers/knowledge_graph.py`** — a graph-builder (typed nodes lead/city/project/referral, edges, color-by-tier, TTLCache). Useful as a **graph-assembly pattern** for Contact-Ops' KG, but note it is **node-color/size only — no avatar or logo image fields**, so it does NOT solve the avatars/logos requirement; that capability would be net-new.
- **Dataset:** the 770-row `data-exports/leads_*.csv` is real enriched B2C people (permit-sourced) — possible Contact-Ops seed/test corpus. (contacts/properties/referrals/estimates CSVs in the export are **empty/0-byte**; only leads + activities carry data.)

## (4) Provenance + licensing/compliance flags

- **License = Proprietary, "Center Deep Intelligence LLC"** — internal-org-owned, so reuse across UC apps is fine, but it is NOT open-source; don't treat as MIT.
- **Scraped-PII risk (the big one):** `osint_search.py`/`browser_osint.py` scrape TruePeopleSearch/FastPeopleSearch/Whitepages and rotate user-agents specifically to **evade bot detection** — these almost certainly violate those sites' ToS and raise CCPA/scraped-PII exposure. The code's own docstrings lean on a "legitimate lead-gen" justification. For a customer-facing sovereign product, **gate or drop these scrapers**; prefer the licensed providers + SearXNG.
- **Mixed provenance per record:** a contact can blend public-record (permits), purchased (PDL/Pipl/FullContact — each with its own redistribution terms), and scraped people-search data. Contact-Ops already has a provenance/canonical-vs-provisional model — **carry a per-field source tag** (the `enrichment_source` + provider fields here map cleanly onto that).
- **Secrets/keys:** all provider keys are env-driven (`.env.example`, `.env.example.phase2`) — none committed; Brightdata/Serper/PDL/etc. would need their own keys.
- **`do_not_contact` + email role-based/disposable flags** exist — good consent-hygiene primitives to preserve.
- Stack deps (FastAPI/SQLAlchemy/BeautifulSoup/Playwright/phonenumbers/cachetools) are all permissive OSS.

**Bottom line for the synthesis:** the `data_providers.py` provider-abstraction + `PersonInfo` schema, the SearXNG/Nominatim/libphonenumber sovereign stack, the local-LLM SERP-extraction pattern, and the email/phone verification schema are the clean, directly-portable wins. The avatars-and-logos KG requirement is **not met by anything here** (social is URL-only, no image enrichment) — that is net-new for Contact-Ops. Quarantine the people-search scrapers behind a compliance gate.

## Survey 4

Clone succeeded (auth via stored bigboy credentials worked — no API fallback needed). Cleanup done. Findings below.

---

# Survey: MagicUnicorn/magic-unicorn-outreach → reusable for Contact-Ops enrichment

**What it is:** "LeadGen-Ops" — a sovereign, local-LLM B2B lead-gen/enrichment platform (FastAPI + Postgres16 + FalkorDB graph + SearXNG + MLX/Ollama). Single-tenant Magic Unicorn instance (~3,325 SC business leads, 508 contacts/403 with email). Branded "Magic Unicorn Outreach." This is the same lineage as the leadgen inventory in MEMORY (Charleston/multistate).

## (1) PEOPLE + COMPANY data/fields it holds

**People (`contacts` table — two variants exist):**
- **Final model** (`backend/app/models.py` `Contact`, FK→`leads`): `first_name/last_name/full_name`, `role` (enum: owner/co_owner/general_manager/marketing_manager/…), `title`, `email` + `email_verified` + `email_verification_date`, `phone`+`phone_type`+`phone_verified`, `linkedin_url/facebook_url/twitter_url`, `source` (enum), `source_url`, `confidence_score`, `is_primary`, `is_decision_maker`, engagement rollups (emails sent/opened, calls), DNC flags, `discovered_at`.
- **Richer enrichment model** (migration `db/002_add_enrichment_tables.sql` + `003_add_contact_enrichment_fields.sql`): **per-field confidence + per-field source URL + per-field verified-at** for name/title/email/phone/linkedin (e.g. `email_confidence`, `email_source`, `email_verified_at`), plus `email_pattern`, `generated_emails` (JSONB), `twitter_confidence/source/verified_at`, `enrichment_status` enum. **This per-field provenance triple (value/confidence/source) is the standout asset.**
- **NO person avatar/photo field anywhere** (see avatars note below).

**Company (`leads` + `charleston_businesses`):** business_name/dba, NAICS+SIC code+description, full address+mailing, phone/email (+confidence+source each), employee_count, license_status/dates, owner_name; web presence: website_url+quality enum, has_facebook/instagram/linkedin/twitter/youtube + URLs + follower counts + last_post_date, google/yelp ratings+review_counts, `uses_free_email`+`email_domain`, booking platform; digital-deficiency score components; `enrichment_sources`/`enrichment_methods` (JSONB arrays). `company_enrichments` table holds domain/website/linkedin_url + `found_emails[]` + `sources` JSONB.
- **`EnrichmentHistory` table** (audit trail): `sources_used[]`, `methods_used[]`, `search_queries[]`, `fields_updated[]`, `changes` JSONB `{field:{old,new,confidence,source}}`, tokens_used, raw_response. **Directly the provenance/audit log Contact-Ops would want.**

## (2) Enrichment SOURCES/APIs — all OSINT/scraped/public, zero purchased data

Positioned explicitly as "OSINT Tools (Replaces Paid Services)" — replaces Hunter.io/Clearbit/BuiltWith. Sources:
- **SearXNG meta-search** (self-hosted) driving **Google Dorks** (`osint_service.py`: `site:linkedin.com/in "owner" "{biz}"`, BBB, SOS, yellowpages/manta/yelp, FB/IG/Twitter/YT) — scraped SERP snippets.
- **DNS/MX** (`dnspython`) for email-provider classification (`email_detection.py`: free-vs-Workspace/M365/self-hosted) and email verification.
- **SMTP RCPT verification** + catch-all detection (`osint/email_finder.py`) — 100% free.
- **theHarvester** subprocess (`osint/harvester_service.py`) — emails from google/bing/linkedin/yahoo.
- **Sherlock** subprocess (`osint/sherlock_service.py`) — social handles across 300+ platforms from username permutations.
- **Email pattern inference + generation** ({first}.{last} etc.) from known domain emails.
- **LLM contact extraction** from scraped website/about/team pages (MLX/Ollama local, OpenRouter/Anthropic fallback; anti-hallucination = only emit text present in source).
- **Paid-but-optional API clients present** (keys optional): Google Places (`google_places_client.py`), Yelp (`yelp_client.py`), BrightData (`brightdata_search.py`). These return **business photos** (`photos[]`/`photo_urls`), not person/company-logo assets.
- **Public records:** Charleston Open Data (currently 404/dead — sample file is just API schema, no records), SC SoS, BBB, county portals — public-record/FOIA tier.
- **Center Deep Verify** (`centerdeep_verify.py`): posts contacts to `verify.centerdeep.online` for SMTP verification + cross-app cataloguing (returns is_deliverable/is_disposable/is_role_based/risk_level/score).

## (3) DIRECTLY REUSABLE for Contact-Ops enrichment (named)

1. **`backend/app/services/osint/` (the whole dir)** — `email_finder.py` (pattern detect/generate, MX, SMTP verify, catch-all), `harvester_service.py` (theHarvester wrapper), `sherlock_service.py` (social-handle discovery by name). Self-contained, "ported from LoopNet-Leads," easy to lift.
2. **`backend/app/services/email_detection.py`** — drop-in free-email/provider classifier (huge curated MX-pattern + free-domain/ISP lists). Useful standalone signal for any contact.
3. **`backend/app/services/osint_service.py`** — Google-Dork template library + SearXNG client + email/phone regex extractors + `full_osint_scan()` orchestration. The dork dict is the reusable IP.
4. **`backend/app/services/contact_enrichment.py`** — **person-level** orchestrator (LinkedIn+Twitter discovery, email validate+generate, per-field confidence/source dataclasses). Closest match to a Contact-Ops "enrich a person" call.
5. **`backend/app/services/centerdeep_verify.py`** — ready-made email-verification client; Contact-Ops can point at the same Center Deep Verify endpoint MEMORY notes are in the ecosystem.
6. **Schema/provenance model** — `db/002`+`003` migrations + `EnrichmentHistory` model: adopt the **value+confidence+source_url+verified_at per field** pattern and the audit table wholesale. This is the single best architectural reuse.
7. Supporting: `website_scraper.py`/`advanced_scraper.py` (trafilatura/playwright team-page scraping), `deduplication.py` (rapidfuzz/jellyfish fuzzy name match — useful for contact dedupe), `graph_service.py` (FalkorDB GraphRAG populate — relevant since Contact-Ops wants a KG, though Contact-Ops uses its own stack).

## (4) Provenance, licensing & compliance flags

- **Provenance model is excellent and directly portable** — per-field source URL + confidence + verified-at + an `EnrichmentHistory` audit log with search_queries and raw responses. Contact-Ops should copy this.
- **All data is OSINT/scraped/public-record — none purchased.** That's compliance-favorable for sovereignty, BUT: Google-Dork-via-SearXNG scraping of Google/LinkedIn/FB SERPs, Sherlock, and theHarvester violate those sites' ToS; **SMTP RCPT verification** can trip spam/abuse heuristics. Fine for internal lead-gen, **riskier to surface as a customer-facing Contact-Ops feature** — gate behind opt-in / rate-limit / treat as "agent action."
- **Email guessing generates unverified addresses** (confidence 60) — must be flagged unverified in Contact-Ops, never auto-promoted to canonical (consistent with the prov→canonical discipline in MEMORY).
- **License: "All rights reserved," Copyright Magic Unicorn Inc.** — internal repo, no OSS license. Reuse inside the suite is fine (same owner); do not treat any module as externally licensed.
- **Avatars/logos — NOT solved here (gap for Contact-Ops's KG requirement):** there is **no person-avatar enrichment and no company-logo fetch**. The only image fields are `Organization.logo_url` (manual tenant branding) and Yelp/Google **business photos** (`photo_urls`, gated behind paid API keys). For the "people avatars + company logos" KG need, Contact-Ops will have to add this net-new (e.g. Gravatar-by-email-hash for people, favicon/Clearbit-logo/`logo.dev` or og:image scrape for company domains). The reusable hook: enrichment already resolves the **company domain** and **LinkedIn/social URLs** per contact — those are exactly the inputs an avatar/logo resolver needs.

**Net:** lift the `osint/` dir + `email_detection.py` + `contact_enrichment.py` + the per-field-provenance schema/`EnrichmentHistory` model; reuse the Center Deep Verify client; build the avatar/logo resolver fresh (this repo doesn't have it). Treat scraping-based sources as opt-in agent actions for compliance.

---

# Appendix B — Enrichment source research (raw)


## Research 1

I now have all primary sources verified across every angle, including the adversarial privacy critique. I have enough to synthesize a complete, cited report. Let me also note one verified nuance: Gravatar's own pricing page says "default rate limits are generous" and paid starts at $100/mo, while the Profiles-API doc and support channels state image requests don't count toward the 100/1000-per-hour Profiles limits. I'll present that precisely.

Here is the synthesized research report.

---

# Enriching Contact/Person Records with a Photo/Avatar — Contact-Ops

Research report. Optimized for a privacy-respecting, sovereignty-minded, self-hostable people-intelligence app. Every provider below is rated on coverage, cost, ToS/privacy/GDPR, rate limits, and exactly how to key it to a contact.

## TL;DR recommendation

**Default keying primitive:** the contact's **email address**, normalized (trim + lowercase) and hashed with **SHA256**. This single hash drives Gravatar, Libravatar, and unavatar without ever sending the plaintext email off-box.

**Fallback chain (privacy/sovereignty-first):**

1. **First-party authorized sources** — Google People API / Microsoft Graph photos, *only* when the user has OAuth-connected their own Google/Microsoft account and the contact is in their directory/contacts. Highest trust, explicit consent, no third-party leak.
2. **Self-hosted Libravatar** (federated, you can run the server) → which itself transparently **redirects to Gravatar** if no Libravatar image exists. Keyed by SHA256(email).
3. **Gravatar directly** (only if you skip self-hosting Libravatar) with `d=404` so a miss returns 404 rather than a generic image — keyed by SHA256(email). *Caveat: this leaks the contact's email-hash to Automattic; see privacy note.*
4. **Domain logo** as a weak org-level fallback (Logo.dev), keyed by the email domain — represents the company, not the person.
5. **Terminal deterministic fallback: self-hosted DiceBear** (initials or `identicon`/`shapes` style), generated locally from the email/name. **Never fails, never leaks, MIT core.** This is the guaranteed last link.

LinkedIn/Twitter/X scraping for photos is **not recommended** — it violates their ToS and creates GDPR/legal exposure. Clearbit's free logo/enrichment APIs are **dead** (sunset Dec 2025).

---

## 1. Gravatar (Globally Recognized Avatar — Automattic)

**Coverage.** Largest single email→photo index on the web (anyone with a WordPress.com/Gravatar account; very high among developers, bloggers, tech professionals; low among general business contacts). No published coverage %, but realistically tens of millions of emails.

**How to key it.** Normalize then hash the email, then GET the image URL ([docs.gravatar.com/rest/hash](https://docs.gravatar.com/rest/hash/), [docs.gravatar.com/sdk/images](https://docs.gravatar.com/sdk/images/)):
1. Trim leading/trailing whitespace
2. Lowercase all characters
3. **SHA256** the result (MD5 is legacy/still-accepted for backward compat, but SHA256 is the documented standard)
4. `https://gravatar.com/avatar/{sha256hash}`

**Useful query params** (verified from the SDK docs):
- `s=` / `size=` — 1px to **2048px**
- `d=` / `default=` — behavior on a miss: `404` (return HTTP 404 — *use this to detect existence*), `mp` (mystery-person silhouette), `identicon`, `monsterid`, `wavatar`, `retro`, `robohash`, `blank`, `initials`, `color`, or a custom HTTPS image URL
- `r=` / `rating=` — `g` / `pg` / `r` / `x` content-rating ceiling
- `f=y` — force the default even if a real Gravatar exists

**Cost.** Free. Paid tier starts at **$100/mo** for SLAs/dedicated support/non-traditional use only ([docs.gravatar.com/pricing](https://docs.gravatar.com/pricing/)).

**Rate limits.** The **Avatar (image) endpoint requests do NOT count toward rate limits** — only the *Profiles* (JSON) API is limited (defaults cited as ~100/hr unauthenticated, ~1000/hr authenticated, raisable for free on request). The pricing page only says "default rate limits on the free plan are generous." For pure photo enrichment you are effectively unthrottled, but you should still cache aggressively (their docs explicitly recommend it).

**ToS / commercial use.** Permitted in commercial products under the free plan; governed by the Guidelines for Responsible Use ([support.gravatar.com/gravatar-user-guidelines](https://support.gravatar.com/gravatar-user-guidelines/)). The Gravatar ToS redirects to the WordPress.com/Automattic ToS ([gravatar.com/site/terms-of-service](https://gravatar.com/site/terms-of-service)). Gravatar is "by design" public: profile data is shared with every site that queries it ([support.gravatar.com/privacy-and-security](https://support.gravatar.com/privacy-and-security/)).

**⚠ Privacy / GDPR (this is the load-bearing caveat for Contact-Ops).** Querying Gravatar for a contact transmits that contact's **email-hash to Automattic's servers**, and the contact has no say in it. The hash is reversible for common domains via brute force (hashcat), enabling deanonymization/enumeration ([r1ch.net/blog/gravatar-considered-harmful](https://r1ch.net/blog/gravatar-considered-harmful)). Under GDPR, you are disclosing personal data (an email, even hashed, is personal data) to a US third party. iubenda's guidance treats Gravatar as a processor requiring disclosure/consent ([iubenda](https://www.iubenda.com/en/help/22853-gravatar-gdpr-how-to-be-compliant/)). **Mitigation for a sovereignty-minded app:** proxy Gravatar through your own Libravatar instance (below) so the lookup origin is your server, and/or disclose Gravatar use in your privacy policy, and/or make it opt-in.

---

## 2. Google — People API photos & Workspace Directory photos

Two distinct surfaces, both requiring the **user to OAuth-connect their own Google account**:

### a) People API (`people.connections.list`, `searchDirectoryPeople`, `otherContacts.list`)
- **Photo resource** ([developers.google.com/people/api/rest/v1/people#Person.Photo](https://developers.google.com/people/api/rest/v1/people)): fields are `url`, `metadata`, and a **`default` boolean** = *"True if the photo is a default photo; false if the photo is a user-provided photo."* **Use `default=false` to skip generic silhouettes and only ingest real photos.**
- `url` supports `?sz={size}` to resize.
- **Keying:** you don't key by raw email — you list the user's *connections / other-contacts / directory people*, then **match each returned person to your contact by email** (`emailAddresses` field) and read `photos[]`.
- **Scopes** ([developers.google.com/identity/protocols/oauth2/scopes](https://developers.google.com/identity/protocols/oauth2/scopes)):
  - `.../auth/contacts.readonly` — the user's saved contacts ("My Contacts")
  - `.../auth/contacts.other.readonly` — auto-saved "Other contacts" (people they've emailed)
  - `.../auth/directory.readonly` — their Google Workspace org directory
- **Coverage:** excellent for *the user's own network* — colleagues (directory) and anyone they correspond with (other-contacts). Zero coverage for strangers.

### b) Admin SDK Directory API (`users.photos.get`)
- Endpoint `GET admin.googleapis.com/admin/directory/v1/users/{userKey}/photos/thumbnail`; `userKey` = **primary email, alias email, or unique user ID** ([developers.google.com/.../users.photos/get](https://developers.google.com/workspace/admin/directory/reference/rest/v1/users.photos/get)).
- Returns a `UserPhoto` (base64 `photoData`, `mimeType`, dimensions).
- **Scopes:** `admin.directory.user` / `admin.directory.user.readonly` — **domain-admin only**, same-Workspace-domain only. Good for an *internal* deployment enriching your own org's employees; not for arbitrary external contacts.

**Cost.** Free (standard Google API quotas). **ToS/privacy:** Google API Services User Data Policy applies; data is the user's own consented data, so GDPR posture is clean (you process *their* contacts under *their* authorization). This is the **highest-trust source** and belongs at the top of the chain when available.

---

## 3. Microsoft — Graph profile photos

Endpoints ([learn.microsoft.com/.../profilephoto-get](https://learn.microsoft.com/en-us/graph/api/profilephoto-get?view=graph-rest-1.0)):
- `GET /me/photo/$value` (signed-in user, binary)
- `GET /users/{id | userPrincipalName}/photo/$value` — **key by email/UPN directly** ✅
- `GET /users/{id|UPN}/photos/{size}/$value` — fixed sizes: 48, 64, 96, 120, 240, 360, 432, 504, 648 px
- `GET /me/contacts/{id}/photo/$value` and `/users/{id|UPN}/contacts/{id}/photo/$value` — **the contact's own photo** (different permission, see below)
- Metadata (no binary): drop `/$value`. A **404** means no photo; a 1×1 GIF metadata response also indicates none uploaded.

**Permissions (verified from the Graph permission tables — note the important split):**
- To read a **user's** photo (directory user, keyed by UPN/email): delegated `ProfilePhoto.Read.All` (least), or `User.Read`/`User.ReadBasic.All`/`User.Read.All`; application `ProfilePhoto.Read.All` or `User.Read.All`.
- To read a **contact's** photo (a person in the signed-in user's Outlook contacts): `Contacts.Read` — *lower privilege*, works for personal Microsoft accounts too.

**License requirement.** Photos for `/me` and directory users live in the Exchange Online mailbox / Entra ID. In practice the target user generally needs an **Exchange/Microsoft 365 mailbox license** for `/photo` to return data (a recurring gotcha in MS Q&A threads). Photos stored in Entra ID can be any dimension.

**Limitations.** Not supported in **Azure AD B2C** tenants. Personal-MSA support is limited to `/me` and contacts (`User.Read`/`Contacts.Read`), not arbitrary `/users/{id}`.

**Coverage.** Excellent for *the connected tenant's* employees and the user's Outlook contacts; none for external strangers. **Cost:** free with Graph. **Privacy/GDPR:** user-consented, tenant-scoped — clean posture, second-highest trust after the user's own directory.

---

## 4. Social / profile avatars

### GitHub (recommended among social sources — permissive)
- `GET https://api.github.com/users/{username}` returns `avatar_url` (and a legacy `gravatar_id`) ([docs.github.com REST users](https://docs.github.com/en/rest/users/users?apiVersion=2022-11-28)).
- Stable derivable URL by numeric id: `https://avatars.githubusercontent.com/u/{id}`.
- **Gravatar bridge:** when a GitHub user has no uploaded image but their email has a Gravatar, GitHub serves that Gravatar through the `avatars.githubusercontent.com` URL; otherwise an identicon ([community discussion #53616](https://github.com/orgs/community/discussions/53616)). So GitHub partially overlaps Gravatar coverage.
- **Keying:** by **username** is reliable; there is **no documented email→user lookup** in the public REST API (email search is unreliable/unsupported). Key by username when you have a GitHub handle on the contact.
- **Rate limits** ([docs.github.com rate-limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api?apiVersion=2022-11-28)): **60/hr unauthenticated per IP**, **5,000/hr authenticated** (15,000/hr for Enterprise-owned apps). The `avatars.githubusercontent.com` image fetches are not the rate-limited API.
- **Cost:** free. **ToS:** public data via official API — acceptable. Good fit for developer-heavy contact bases.

### LinkedIn — DO NOT scrape (recommend against)
- The User Agreement and API Terms prohibit scraping, storing, displaying, or transferring LinkedIn content obtained outside the API, including profile photos ([User Agreement](https://www.linkedin.com/legal/user-agreement), [Prohibited software](https://www.linkedin.com/help/linkedin/answer/a1341387/prohibited-software-and-extensions)). Official photo access exists only via approved API partner programs with member consent. Scraping risks account bans, injunctions, and GDPR/privacy-law enforcement ([scrapingdog](https://www.scrapingdog.com/blog/linkedin-web-scraping/)). **Excluded from the chain.**

### Twitter/X
- No free, ToS-clean public avatar API since the API paywall. Aggregators (below) hit it but coverage is unreliable and ToS-gray. **Not recommended as a first-party integration.**

### unavatar (microlink) — useful as a *self-hosted* aggregator, not the hosted one
- Aggregates 70+ providers (Gravatar, GitHub, Twitter/X, Instagram, DuckDuckGo, Google, Microsoft, Mastodon, Bluesky, …) ([github.com/microlinkhq/unavatar](https://github.com/microlinkhq/unavatar)).
- **Keys by email, username, OR domain:** `/email/{addr}`, `/github/{user}`, `/domain/{domain}`. `?fallback=` sets a custom miss image or `false` for 404.
- **MIT-licensed, self-hostable** (Node). Hosted unavatar.io is heavily limited (25 req/day/IP anon; pro 50/day) — **self-host it** so you control privacy and limits. *Caveat:* when self-hosted it still calls out to third parties (incl. Gravatar/social) on your behalf, so it has the same leak profile as Gravatar unless you restrict its providers. Treat as an optional middleware layer, not the default.

### Company/domain logos (org-level fallback only)
- **Clearbit Logo API is DEAD:** free Logo API sunset **Dec 1, 2025** (shutdown ~Dec 8), free platform died Apr 30, 2025, after the HubSpot/Breeze acquisition ([developers.hubspot.com changelog](https://developers.hubspot.com/changelog/upcoming-sunset-of-clearbits-free-logo-api)). Do not build on it.
- **Replacement — Logo.dev** ([logo.dev/pricing](https://logo.dev/pricing)): `https://img.logo.dev/{domain}`, API token required. **Community/free = $0/yr, 500k CDN req/mo, commercial use requires a backlink/attribution**; Startup $280/yr (1M, no backlink); Pro $1,260/yr (5M); Enterprise $2,800+/yr. Keyed by the **email domain**. This is a *company* badge, a weak stand-in when no person photo exists — never the primary.
- **Even more sovereign:** just fetch the domain's `favicon` (e.g. via Google's favicon endpoint or self-fetch `/favicon.ico`), zero third-party dependency on a paid vendor.

---

## 5. Self-hostable / on-device options (the sovereignty core)

### Libravatar — federated, self-hostable Gravatar alternative (recommended layer 2)
- Open-source ([libravatar.org](https://libravatar.org/), [wiki.libravatar.org](https://wiki.libravatar.org/api/)). Base: `https://seccdn.libravatar.org/avatar/{hash}`.
- **Hash:** lowercase the email, then **MD5 or SHA256** (SHA256 mandatory for OpenID). Same `d=` params as Gravatar plus `pagan`.
- **Federation via DNS SRV:** a domain publishes `_avatars-sec._tcp.{domain}` (HTTPS) / `_avatars._tcp.{domain}` (HTTP) pointing at its own Libravatar server — so an org can **self-host avatars for its own staff** and you resolve them automatically. Falls back to `cdn.libravatar.org` if none.
- **Built-in Gravatar fallback:** *"if an image is not found in the Libravatar database, Libravatar will first redirect to Gravatar."* This is why it's the ideal layer-2: one call covers Libravatar + Gravatar, and **you can run the Libravatar node yourself** (Django app) so the lookup originates from your infrastructure, blunting the Gravatar email-leak concern.
- Cost: free / your hosting only.

### DiceBear — deterministic generated avatars (recommended TERMINAL fallback)
- ([dicebear.com](https://www.dicebear.com/), [github.com/dicebear/dicebear](https://github.com/dicebear/dicebear)). **Privacy-focused, runs entirely in your code — "no data leaves your servers."**
- **Deterministic:** same seed → same avatar forever. Seed with the email or `firstName+lastName`+a stable id so a contact always looks the same.
- **37 styles**; `initials` and `identicon`/`shapes`/`thumbs` are the safe professional choices for business contacts.
- **License nuance (important):** the **core is MIT**, but **each style has its own artist-chosen license** — some are CC0 1.0 (no attribution), others **CC BY 4.0 (attribution required)**. For a commercial app pick **CC0 styles or the `initials` style** to avoid attribution obligations, or comply with CC BY.
- Self-host via JS/PHP/Python libs or `npx dicebear` CLI; an HTTP API exists but use the library for full sovereignty.

### Other deterministic generators (all client-side, zero network, zero PII egress)
- **boring-avatars** (MIT, React) — abstract SVG, change colors/variant via props; great lightweight terminal fallback.
- **jdenticon** (MIT, JS/.NET) — GitHub-style identicons from a hash, fully local.
- **ui-avatars.com** — initials avatars, free, no login, no usage tracking, "only final images cached, no other info stored" ([ui-avatars.com](https://ui-avatars.com/)). Privacy-friendly but it's still an external HTTP call — prefer generating initials locally (DiceBear/boring-avatars) for true sovereignty; use ui-avatars only if you want zero generation code.

---

## Recommended architecture for Contact-Ops

**Keying primitive (compute once per contact, store it):**
```
email_norm   = email.strip().lower()
email_sha256 = sha256(email_norm)          # Gravatar + Libravatar key
email_md5    = md5(email_norm)             # legacy Gravatar/Libravatar compat
domain       = email_norm.split('@')[1]    # logo / favicon key
```

**Resolution order (stop at first hit; cache the result + a periodic re-check):**

| # | Source | Key | Why here / privacy posture |
|---|--------|-----|----------------------------|
| 1 | Google People / Workspace photo (`default=false`) | match contact by email within the user's connected directory/contacts | User-consented, no third-party leak. Highest trust. Only if user OAuth-linked Google. |
| 2 | Microsoft Graph `/users/{UPN}/photo` or `/contacts/{id}/photo` | UPN/email | User-consented, tenant-scoped. Only if user OAuth-linked Microsoft. |
| 3 | **Self-hosted Libravatar** (→ auto-redirects to Gravatar on miss) | `email_sha256` | Federated + self-hostable; lookup originates from *your* server, covers Libravatar+Gravatar in one call. |
| 3b | *(if not self-hosting Libravatar)* Gravatar `d=404` | `email_sha256` | Same coverage minus the proxy; **leaks email-hash to Automattic — disclose/opt-in.** |
| 4 | GitHub avatar | username (when known) | Public, permissive ToS, overlaps Gravatar. Skip if no handle. |
| 5 | Domain logo (Logo.dev free / favicon) | `domain` | Org badge only — not the person. Optional. |
| 6 | **DiceBear self-hosted** (`initials` or a CC0 style) | seed = email/name | **Terminal, never fails, never leaks, MIT core.** Guaranteed avatar. |

**Hard exclusions:** LinkedIn/Twitter/X scraping (ToS + GDPR exposure); Clearbit (dead); hosted unavatar.io and hosted ui-avatars as *primary* (external PII egress / tiny limits — self-host or generate locally instead).

**Privacy program notes for a sovereignty-minded product:**
- Treat any source that sends a contact's email/email-hash off-box (Gravatar, hosted unavatar, hosted Logo.dev) as a **sub-processor**: disclose in the privacy policy, and ideally make steps 3b/4/5 **opt-in per workspace**. Steps 1–2 (user's own consented data) and 6 (local generation) need no third-party disclosure.
- Cache fetched images on **your own storage** (your existing Garage/S3) rather than hot-linking, both for performance and to stop continuously re-leaking lookups to third parties.
- Store provenance per photo (`source`, `fetched_at`, `is_default`) so you can re-rank/refresh and prove where each avatar came from — consistent with Contact-Ops' provenance model.

---

## Sources

- Gravatar hashing: https://docs.gravatar.com/rest/hash/
- Gravatar image params/defaults: https://docs.gravatar.com/sdk/images/
- Gravatar pricing: https://docs.gravatar.com/pricing/
- Gravatar user guidelines / responsible use: https://support.gravatar.com/gravatar-user-guidelines/
- Gravatar privacy: https://support.gravatar.com/privacy-and-security/ ; GDPR: https://www.iubenda.com/en/help/22853-gravatar-gdpr-how-to-be-compliant/
- Gravatar privacy criticism (email-hash leak/deanonymization): https://r1ch.net/blog/gravatar-considered-harmful
- Gravatar ToS (redirects to WordPress.com): https://gravatar.com/site/terms-of-service
- Google People API Person.Photo + scopes: https://developers.google.com/people/api/rest/v1/people ; https://developers.google.com/identity/protocols/oauth2/scopes
- Google Workspace Admin Directory users.photos.get: https://developers.google.com/workspace/admin/directory/reference/rest/v1/users.photos/get
- Microsoft Graph profilePhoto (endpoints, permissions, sizes, limits): https://learn.microsoft.com/en-us/graph/api/profilephoto-get?view=graph-rest-1.0
- GitHub users API (avatar_url/gravatar_id): https://docs.github.com/en/rest/users/users?apiVersion=2022-11-28 ; rate limits: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api?apiVersion=2022-11-28 ; Gravatar bridge: https://github.com/orgs/community/discussions/53616
- LinkedIn User Agreement / scraping prohibition: https://www.linkedin.com/legal/user-agreement ; https://www.linkedin.com/help/linkedin/answer/a1341387/prohibited-software-and-extensions
- Clearbit Logo API sunset: https://developers.hubspot.com/changelog/upcoming-sunset-of-clearbits-free-logo-api
- Logo.dev pricing: https://logo.dev/pricing
- unavatar (self-hostable aggregator, MIT): https://github.com/microlinkhq/unavatar
- Libravatar protocol/federation: https://wiki.libravatar.org/api/ ; https://libravatar.org/
- DiceBear (license per style, deterministic, self-host): https://www.dicebear.com/why-dicebear/ ; https://github.com/dicebear/dicebear
- ui-avatars (privacy/no-tracking): https://ui-avatars.com/

## Research 2

The 403 is fine — I already have the authoritative Brandfetch usage terms from the docs.brandfetch.com fetch (which explicitly stated "Programmatic access to logo images is not permitted... Scraping logos will also lead to a block"). I have enough cross-verified data from primary sources to synthesize. Let me compile the final report.

---

# Fetching Organization Logos by Domain for Contact-Ops Graph Viz — Research Report

**Bottom line:** Clearbit's free Logo API (`logo.clearbit.com`) is **dead** as of Dec 8, 2025. For a graph visualization keyed on email domain, the strongest sovereign-friendly stack is: **self-hosted favicon/logo proxy (default)** → **logo.dev hotlink (broad coverage)** → **BIMI DNS (authoritative SVG when present)** → **Wikidata/Commons (CC-licensed, cacheable)** → **monogram fallback**. Rationale and per-option detail below.

---

## Option-by-option

### 1. Clearbit Logo API — DEAD, do not use

- **Status:** Acquired by HubSpot (Dec 2023). Free Logo API deprecation announced **Mar 18, 2025**; **shut down Dec 8, 2025** (HubSpot's notice cites Dec 1 in the title, Dec 8 as the hard cutoff). Calls to `logo.clearbit.com` now fail to connect. ([HubSpot changelog](https://developers.hubspot.com/changelog/upcoming-sunset-of-clearbits-free-logo-api), [HubSpot Community](https://community.hubspot.com/t5/Clearbit/Clearbit-Logo-API-Will-Be-Sunset-on-December-1-2025/td-p/1162963))
- **HubSpot's own recommended replacement is logo.dev**, with a literal swap: `https://logo.clearbit.com/:domain` → `https://img.logo.dev/:domain?token=YOUR-TOKEN`. ([HubSpot changelog](https://developers.hubspot.com/changelog/upcoming-sunset-of-clearbits-free-logo-api))
- **Action:** purge any `logo.clearbit.com` references from Contact-Ops.

### 2. logo.dev — best commercial default

- **Coverage:** Very broad domain-based coverage; the explicit Clearbit successor, recommended by HubSpot. CDN-backed (200+ edge locations, sub-50ms claimed). ([logo.dev](https://www.logo.dev/), [abstractapi guide](https://www.abstractapi.com/guides/company-enrichment/best-company-logo-apis))
- **Cost:** Community/Free = **500K CDN requests/month** but **requires visible attribution**: literally `<a href="https://logo.dev">Logos provided by Logo.dev</a>`, which must be on the production site. Startup **$280/yr** (1M req, no attribution). Pro **$1,260/yr** (5M req, **includes image self-hosting/caching**). Enterprise $2,800+/yr. ([pricing](https://www.logo.dev/pricing), [attribution doc](https://www.logo.dev/docs/platform/attribution))
- **ToS / caching:** Free tier is effectively **hotlink + attribution**; **self-hosting/caching the image bytes is a Pro-tier feature**. ToS disclaims all third-party IP/trademark liability and pushes responsibility to you (standard for logo APIs). ([pricing](https://www.logo.dev/pricing), [terms](https://www.logo.dev/legal/terms))
- **Quality:** Real brand logos (not just favicons). PNG/WebP/JPG, retina/size/grayscale, dark-mode variants. Square logo integration. ([logo.dev](https://www.logo.dev/))
- **Rate limits:** Monthly-count based, **no burst limits**; graduated/soft enforcement (email first, "okay to go a little over"). ([rate limits](https://www.logo.dev/docs/platform/rate-limits))

### 3. Brandfetch (Logo Link / Logo API) — broadest catalog, but hotlink-locked

- **Coverage:** Largest claimed catalog — **60M+ brand logos**; query by domain, ticker, ISIN, or crypto symbol. ([Logo API](https://brandfetch.com/developers/logo-api), [comparison](https://www.context.dev/blog/company-logo-api-comparison))
- **Cost:** Free up to **500K requests/month, no attribution required**. Throughput caps: 1,000 req/5min per IP, 2,400 req/5min per customer. ([docs overview](https://docs.brandfetch.com/logo-api/overview))
- **ToS / caching — the dealbreaker for a sovereign cache:** **"Programmatic access to logo images is not permitted... Scraping logos will also lead to a block."** Links **must be embedded directly** in `<img>` tags (hotlink-only). You cannot legitimately download → store → re-serve from your own infra without a custom/sales arrangement. ([docs overview](https://docs.brandfetch.com/logo-api/overview))
- **URL format:** `https://cdn.brandfetch.io/domain/nike.com?c=CLIENT_ID` (auto-detect works without `domain/`). Requires a free client ID. ([GitHub](https://github.com/Brandfetch/Logo-API), [docs](https://docs.brandfetch.com/logo-api/overview))
- **Quality:** Highest — WebP/PNG/JPG/SVG, light/dark themes, icon vs symbol vs logo variants, custom w/h. ([docs overview](https://docs.brandfetch.com/logo-api/overview))
- **Fit:** Excellent if you accept hotlinking and a third-party dependency at render time; poor fit if Contact-Ops needs an offline/air-gapped or self-cached graph (sovereignty goal).

### 4. Favicon services + BIMI

**Google favicon endpoint** (`https://www.google.com/s2/favicons?domain=DOMAIN&sz=256`, also `s2.googleusercontent.com`):
- **Coverage:** Nearly universal (any site with a favicon). Free, no key. ([derlin](https://blog.derlin.ch/get-favicons-from-any-domain-using-a-hidden-google-api/), [logo.dev docs mirror](https://docs.logo.dev/google-favicon-api))
- **Quality:** Weak for a graph — **favicon ≠ logo**, default 16×16, `sz` >256 often returns smaller, and **transparency is flattened to a white background**. PNG output. ([derlin](https://blog.derlin.ch/get-favicons-from-any-domain-using-a-hidden-google-api/), [erikmartinjordan](https://erikmartinjordan.com/get-favicon-google-api))
- **ToS:** **Undocumented/unofficial** — no SLA, no terms, "use at your own risk." Risky as a hard dependency. ([Jim Nielsen](https://blog.jim-nielsen.com/2021/displaying-favicons-for-any-domain/))
- **DuckDuckGo** (`https://icons.duckduckgo.com/ip3/DOMAIN.ico`) is the common unofficial fallback. ([codepen](https://codepen.io/djekl/pen/QWKNNjv))

**BIMI (DNS-based, brand-authoritative):**
- **Mechanism:** TXT record at `default._bimi.<domain>`; the `l=` tag points to a brand-hosted **SVG Tiny P/S** logo (optional `a=` VMC for verification). The logo is **owned/published by the brand itself** — the most authoritative source. ([BIMI Group VMC](https://bimigroup.org/verified-mark-certificates-vmc-and-bimi/), [Valimail](https://www.valimail.com/resources/guides/bimi-email/bimi-record/))
- **Coverage:** **Low — ~4.5–5.7% of top domains** (≈9,661 domains in the top-1M as of Jan 2025), but heavily skewed toward large/serious brands that gate behind a **$749–1,688/yr VMC**. So when present, it's a high-value, exact-brand hit. ([uriports](https://www.uriports.com/blog/bimi-2025-update/), [Validity](https://www.validity.com/blog/the-bimi-battle-an-analysis-on-bimi-adoption-and-implementation/), [ssl2buy](https://www.ssl2buy.com/wiki/bimi-certificate-cost-cmc-and-vmc-pricing))
- **Quality:** Native vector SVG (scales perfectly in a graph). SVG Tiny P/S forbids scripts/external refs/raster, ≤32KB — **safe to render and easy to rasterize**. ([BIMI Group SVG](https://bimigroup.org/creating-bimi-svg-logo-files/))
- **Cost/sovereignty:** **Free to read** (it's just DNS + an HTTPS GET to the brand's own host). Fully self-implementable, no third party. Best sovereign signal available.

### 5. Wikidata / Wikipedia — the sovereign, cacheable, licensed source

- **Mechanism:** Wikidata property **P154 "logo image"** links to a file on Wikimedia Commons. Resolve a domain → entity (via P856 "official website" in SPARQL), then read P154. ([P154](https://www.wikidata.org/wiki/Property:P154), [P856 pattern](https://wiki.openstreetmap.org/wiki/Key:brand:wikidata))
- **Coverage:** Good for notable/established orgs; sparse for SMBs and brand-new companies. (Complements, not replaces, domain APIs.)
- **Cost:** Free. SPARQL endpoint + Commons file serving.
- **ToS / caching — the key advantage:** Wikidata is **CC0**; Commons images are typically **CC-BY-SA (or PD)**. This is the **one source you can legally download, cache, self-host, and re-serve** indefinitely — ideal for a sovereign on-disk logo cache, subject to attribution for CC-BY-SA assets. ([Help:Copyrights](https://www.wikidata.org/wiki/Help:Copyrights), [SPARQL copyright](https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service/Copyright))
- **Quality:** Often official SVG/PNG; transparency preserved; variable but generally high for the brands it covers.

---

## Self-hostable / sovereign building blocks

- **unavatar.io (microlinkhq)** — MIT, **self-hostable** (Railway/Docker). Resolves avatars **and logos/favicons by email or domain** through a built-in provider fallback chain (Gravatar → DuckDuckGo favicon → etc.). Email-shaped input runs the same chain — directly matches Contact-Ops's "keyed on email domain." Run it on your own infra to avoid the hosted dependency. ([GitHub](https://github.com/microlinkhq/unavatar), [unavatar.io](https://unavatar.io/))
- **vemetric/favicon-api** — free, **self-hostable** (TypeScript/Hono/Bun), multi-format, intelligent fallbacks, proper HTTP caching. Clean primitive if you want to own the favicon path entirely. ([GitHub](https://github.com/vemetric/favicon-api))
- **Roll-your-own favicon extraction:** fetch the site, parse `<link rel="icon|apple-touch-icon|mask-icon">` (apple-touch-icon is usually 180×180+, far better than `/favicon.ico`), prefer largest. Fully sovereign, cacheable.

---

## Recommendation: default + fallback chain (keyed on email domain)

For Contact-Ops, where the goal is sovereignty + a cache you control + decent visual quality, run **one internal logo-resolver service** (fork unavatar or write a thin proxy) that owns this waterfall and **caches every resolved asset on disk/Garage with a content hash + TTL + per-domain negative cache**:

1. **BIMI DNS** (`default._bimi.<domain>` → `l=` SVG) — authoritative, brand-owned, vector, free, cacheable. Try first; ~5% hit but highest quality when it lands.
2. **Wikidata P154 → Commons** — CC-licensed, **legally cacheable/self-hostable**, often official SVG. Best sovereign hit for notable orgs.
3. **logo.dev hotlink** (`img.logo.dev/<domain>?token=…`) — the broad-coverage workhorse and the sanctioned Clearbit successor. On the free tier this is **hotlink + the required attribution link**; if you want to legally cache the bytes into your sovereign store, that's the **Pro plan ($1,260/yr)** — worth it the moment you need offline/air-gapped graphs or want to drop the runtime third-party dependency.
4. **Self-hosted favicon extraction** (apple-touch-icon → manifest icons → `/favicon.ico`), with Google/DuckDuckGo favicon endpoints as a *soft* fallback only (unofficial, no SLA, white-background transparency loss).
5. **Generated monogram** (first letter + brand-color chip) — deterministic, never-empty terminal fallback so every graph node always renders.

**Why this order over "just Brandfetch":** Brandfetch has the biggest catalog and best variants, but its terms **forbid programmatic download/caching and require hotlinking** ("scraping logos will lead to a block") — that conflicts with a sovereign, self-cached, potentially offline graph. Keep Brandfetch as an *optional* premium hotlink source if you ever need a node's logo and steps 1–3 miss, but don't make it the backbone. logo.dev (Pro, for caching rights) is the better commercial backbone; BIMI + Wikidata are the truly sovereign, cache-legal layers.

**Implementation notes:** key the cache on the **registrable/eTLD+1 domain** parsed from the email (strip subdomains; PSL-aware), maintain a **negative cache** (so repeated misses on SMB domains don't re-hit every provider), store a hash of the bytes to dedupe, and normalize everything to a square PNG/WebP at one or two sizes for the graph renderer. Honor logo.dev's attribution link on the free tier and CC-BY-SA attribution for Commons assets.

---

**Primary sources:** [HubSpot Clearbit sunset](https://developers.hubspot.com/changelog/upcoming-sunset-of-clearbits-free-logo-api) · [logo.dev pricing](https://www.logo.dev/pricing) / [attribution](https://www.logo.dev/docs/platform/attribution) / [terms](https://www.logo.dev/legal/terms) / [rate limits](https://www.logo.dev/docs/platform/rate-limits) · [Brandfetch Logo API docs](https://docs.brandfetch.com/logo-api/overview) / [Logo-API GitHub](https://github.com/Brandfetch/Logo-API) · [Google favicon writeup](https://blog.derlin.ch/get-favicons-from-any-domain-using-a-hidden-google-api/) / [Jim Nielsen on favicons](https://blog.jim-nielsen.com/2021/displaying-favicons-for-any-domain/) · [BIMI Group VMC](https://bimigroup.org/verified-mark-certificates-vmc-and-bimi/) / [SVG files](https://bimigroup.org/creating-bimi-svg-logo-files/) / [uriports BIMI 2025](https://www.uriports.com/blog/bimi-2025-update/) · [Wikidata P154](https://www.wikidata.org/wiki/Property:P154) / [Help:Copyrights](https://www.wikidata.org/wiki/Help:Copyrights) · [unavatar GitHub](https://github.com/microlinkhq/unavatar) · [vemetric favicon-api](https://github.com/vemetric/favicon-api)

## Research 3

Verification complete. Key corrections captured:
- **hiQ ended badly for the scraper**: LinkedIn *won* summary judgment on breach-of-contract (Nov 2022); Dec 2022 consent judgment = $500K against hiQ + permanent injunction to cease scraping and destroy all scraped data/algorithms. The CFAA "public data" point survived but ToS-breach + fake-profile conduct sank them. This is a critical nuance for the recommendation.
- **CNIL 19 June 2025 focus sheet**: legitimate interest *can* cover commercial prospecting via scraping, but only with mandatory safeguards (respect robots/opt-out, exclude objecting sites, freely-accessible data only, timely deletion, prior opt-out mechanism). This is the most authoritative recent EU signal and directly shapes the agent-scraping layer design.

I have robust, verified, primary-sourced coverage across all three approaches and all four axes. Synthesizing now.

---

# Contact & Company Data Enrichment for a Sovereign, Agent-First Product: Build vs. Buy vs. Agent

**Scope:** firmographics, job titles, social handles, email/phone discovery + verification, for a self-hostable contact-intelligence product (your Contact-Ops "people intelligence" SoT). Three sourcing strategies compared on **cost, ToS/legal compliance + liability, data quality/coverage/freshness, and sovereignty fit**, then a concrete hybrid architecture.

The headline tension: **the things that make commercial APIs valuable (a pre-built, redistributable contact graph) are exactly the things that are legally radioactive and sovereignty-hostile.** The things that are clean (public registries, your own first-party signals, on-demand agent research) are cheaper and defensible but require you to build the assembly layer. So the answer is a layered hybrid, and the layering is dictated more by **legal liability** than by cost or quality.

---

## 1. Commercial enrichment APIs

### Cost (verified pricing, 2025–2026)

| Vendor | Entry | Per-record economics | Notes |
|---|---|---|---|
| **Hunter.io** | Free 50 cr/mo; Starter $49/mo (2k cr), Growth $149 (10k), Scale $299 (25k) | 1 cr = find email, 0.5 cr = verify; ~$0.0245/email (Starter) down to ~$0.0084 (Scale annual) | Cheapest; email-finding/verification only, weak on phone/firmographics |
| **People Data Labs (PDL)** | Pro $98/mo (350 person-enrich credits + 1k company) | ~$0.28/enrich → $0.20 at annual volume; contact data (email/phone) is a *separate* Person-Identify call at ~$0.40–0.55/match | Developer-first API; also sells **bulk on-prem dataset licenses** (annual) |
| **Apollo.io** | $49–$119/user/mo (annual) | Credit system, no published per-credit rate; 224M contacts, claims 96% email accuracy (users report 65–80%) | Seat + credit hybrid; cheap but accuracy/compliance concerns |
| **Clearbit → HubSpot "Breeze Intelligence"** | ~$30/mo (100 cr) → ~$700/mo (10k) | ~$0.30/cr (low tier) → ~$0.07/cr (10k tier) | Now folded into HubSpot; credit-based |
| **Lusha** | ~$22/user/mo (Pro, 3k annual cr) | 1 cr = verified email, 5 cr = direct dial | Mobile-number focused |
| **FullContact** (Ziff Davis since Q4 2024) | Free dev 100 calls/mo; Growth $99/mo (10k calls); Basic $500 → Enterprise $10k+ | Identity-resolution graph, 900+ attributes | Now a Martech-division product post-acquisition |
| **ZoomInfo** | $15k+/yr, multi-year commit | Not publicly disclosed | Enterprise; largest DB (300M+) + intent data |
| **Cognism** | Custom | "Diamond Data" = manually phone-verified mobiles | Strongest EU compliance posture + phone accuracy |

Sources: [Hunter pricing](https://hunter.io/pricing), [PDL person pricing](https://www.peopledatalabs.com/pricing/person) / [fullenrich PDL](https://fullenrich.com/content/people-data-labs-pricing), [Cognism on Apollo pricing](https://www.cognism.com/blog/apollo-io-pricing), [Clearbit pricing](https://www.cognism.com/blog/clearbit-pricing), [Lusha alternatives](https://www.lusha.com/blog/best-zoominfo-alternatives/), [FullContact pricing](https://fullenrich.com/content/full-contact-pricing), [Starnus DB comparison](https://starnus.com/blog/best-b2b-data-providers-zoominfo-apollo-pdl).

### Data quality / coverage

Independent-ish bake-offs converge on **70–95% email accuracy, heavily geography- and segment-dependent**. A 1,000-contact 2026 test: Cognism ~90%, ZoomInfo ~85%, Apollo ~80%; single-database providers averaged ~82% email accuracy. Cognism's phone "Diamond Data" claims 98% on manually-verified mobiles vs ZoomInfo ~72% match in a head-to-head. Vendors' own marketing (Apollo "96%", ZoomInfo "90–98%") runs well above third-party measured rates. ([Cleanlist 15-best ranked](https://www.cleanlist.ai/blog/15-best-b2b-data-enrichment-providers-in-2025-ranked), [Mindcase accuracy report](https://mindcase.co/blog/b2b-data-accuracy-report-2026), [Cognism vs ZoomInfo](https://www.cognism.com/blog/zoominfo-competitors)). **Freshness:** PDL is pre-crawled cache "updated monthly by default" ([PDL docs](https://docs.peopledatalabs.com/docs/data-license.md)) — i.e., you're querying a snapshot, not live truth.

### ToS / redistribution restrictions (this is the sovereignty killer)

- **PDL**: bulk license lets you "use the data on premise in any way as long as it is compliant with [the] Acceptable Data Use Policy" ([PDL data license](https://docs.peopledatalabs.com/docs/data-license.md)). On-prem is sovereignty-friendly *operationally*, but the AUP gates resale/redistribution/people-search use (the AUP page is JS-gated; treat redistribution-to-end-users and consumer-facing lookup as **prohibited until you get the contract in writing**). API responses are "pre-crawled, cached data."
- **OpenCorporates**: free API **only for open-data projects** — "the product or database in which the data is incorporated [must] also be released under an open licence," with **share-alike + attribution**. Commercial use removes share-alike but costs **from £2,250/yr (Essentials)**; default free tier is throttled to **200 req/month, 50/day** ([OpenCorporates API ref](https://api.opencorporates.com/documentation/API-Reference), [API home](https://api.opencorporates.com/)). You cannot quietly fold OpenCorporates data into a closed-source commercial product on the free tier.
- **Apollo**: explicitly **"We sell personal information collected for our database to our customers"** — name, employer, title, email, phone, social/professional links, work/education history — "for B2B sales and marketing and recruiting." ([Apollo CCPA statement](https://www.apollo.io/privacy-policy/ccpa-privacy-statement)). You are buying **third-party personal data**, not a processing service.

### Legal / GDPR / CCPA + liability (the decisive axis)

The structural problem: when you ingest a vendor's pre-built person graph, **you become a controller of personal data you didn't collect from the data subject**, which triggers obligations the vendor's marketing glosses over:

- **GDPR Article 6(1)(f) legitimate interest** is the only workable basis for B2B enrichment, but it is "not a blanket permission" — you need a **documented Legitimate Interest Assessment (LIA)** per use, passing legitimacy + necessity + balancing ([derrick GDPR guide](https://derrick-app.com/en/gdpr-data-enrichment/), [unifyGTM](https://www.unifygtm.com/explore/b2b-data-compliance-gdpr-ccpa)).
- **GDPR Article 14** is the trap most enrichment buyers fail: when you obtain personal data from a third party (an enrichment vendor) rather than the person, you must **proactively notify the data subject** at first contact — your identity, purpose, legal basis, the **source**, and their rights ([derrick guide](https://derrick-app.com/en/gdpr-data-enrichment/)). "We got your details from a professional database" must be disclosable.
- **DPA is mandatory** with any vendor processing personal data on your behalf; absence of a DPA is "a direct compliance gap" and a red flag.
- **Data-subject rights propagate to you**: access, rectification, **erasure/objection within ~30 days** (24–48h for opt-outs). If a person opts out at Apollo, that doesn't scrub the copy you cached — you carry your own deletion obligation.
- **US: FCRA + data-broker rulemaking risk.** Contact data used for marketing is fine, but the moment it touches **hiring, promotion, credit, or tenancy** decisions it can become a "consumer report" under FCRA with a bright-line permissible-purpose regime ([CFPB circular 2024-06](https://www.consumerfinance.gov/compliance/circulars/consumer-financial-protection-circular-2024-06-background-dossiers-and-algorithmic-scores-for-hiring-promotion-and-other-employment-decisions/)). The CFPB's 2024 "Protecting Americans from Harmful Data Broker Practices" rulemaking ([Federal Register](https://www.federalregister.gov/documents/2024/12/13/2024-28690/protecting-americans-from-harmful-data-broker-practices-regulation-v)) signals tightening on selling identifiers for non-FCRA purposes — relevant if your customers do recruiting (your Tax/Wealth/CPA verticals likely don't, but Meeting-Ops/Customer-Ops attendees could).

**Bottom line on commercial APIs:** great as a *fallback enrichment* call behind an abstraction, **toxic as a bulk-ingested foundation** for a sovereign product. Buying Apollo/ZoomInfo bulk = inheriting their data-broker liability and breaking your "no third-party data egress / sovereign" promise (you'd be silently shipping customers a redistributed broker database). Avoid bulk-redistribution entirely.

---

## 2. OSINT / self-hosted tooling

### The toolset (all self-hostable, mostly MIT/GPL, pip/git installable)

- **theHarvester** (laramies) — emails, subdomains, names, IPs, URLs from many public sources; recon-stage OSINT. ([GitHub](https://github.com/laramies/theHarvester))
- **holehe** — checks whether an email is registered on **120+ platforms** via password-reset/login probing without logging in → social-footprint + account-existence signal. ([Apify listing](https://apify.com/anshumanatrey/holehe-email-osint))
- **Maigret** / **Sherlock** — username → presence across hundreds of sites (social-handle discovery).
- **SpiderFoot** — MIT, **200+ modules**, most needing **no API key**; self-hosted CLI + commercial "HX" cloud. Orchestrates and correlates entities; the open version covers the substance, HX adds convenience/scale modules. ([GitHub](https://github.com/smicallef/spiderfoot), [Intel471 modules](https://www.intel471.com/blog/15-new-modules-for-open-source-and-spiderfoot-hx))
- **Recon-ng** — modular recon framework (Metasploit-style) for structured collection.
- **Email permutation + layered verification** — generate `first.last@`, `f.last@`, `first@` etc., then verify in layers: syntax → MX → SMTP RCPT handshake (no send). The hard limit: **catch-all domains (15–28% of B2B domains) accept any address, so SMTP can't confirm a specific mailbox** ([prospeo](https://prospeo.io/s/how-to-find-domain-email-address)).
- **Public registries / authoritative firmographics** — **SEC EDGAR** (US public-company filings, free, full-text search), **Companies House** (UK, free API), **OpenCorporates** (220M+ entities, 140+ jurisdictions — but licensing as above), `company_dns` (free OSS combining Wikidata + SEC EDGAR + SIC). ([OpenCorporates](https://api.opencorporates.com/), [Medium open-firmographics](https://medium.com/@michaelhay_90395/a-case-for-api-based-open-company-firmographics-145e4baf121b))

### Cost
Software is free; cost is **engineering + infrastructure + proxy/IP reputation + ongoing maintenance** (OSINT modules break constantly as sources change anti-bot defenses). SMTP verification at scale needs warmed IPs and careful rate-limiting or you get blocklisted.

### Data quality / coverage / freshness
**Freshness is the win** — it's live, not a monthly snapshot. **Coverage is the loss** — no single tool gives you a clean firmographic record; you get fragments (an email here, a Twitter handle there) that you must correlate and de-dupe yourself. Catch-all domains cap email-verification confidence. Registry data is authoritative for *legal entity* firmographics (incorporation, officers, jurisdiction, filings) but says nothing about job titles or personal emails.

### Legal / GDPR / CCPA + liability
- **US (CFAA):** scraping **publicly accessible** pages is generally not "unauthorized access" — *hiQ v. LinkedIn* (9th Cir.) and *Meta v. Bright Data* (2024, summary judgment for Bright Data: ToS bind only logged-in users) ([IAPP](https://iapp.org/news/a/data-scraping-and-the-implications-of-the-latest-linkedin-hiq-court-ruling), [scraping court cases](https://sociavault.com/blog/web-scraping-legality-court-cases-public-vs-private-data)). **But the hiQ ending is a cautionary tale, not a green light:** LinkedIn *won* summary judgment on **breach of contract** (Nov 2022), and the Dec 2022 consent judgment hit hiQ with a **$500,000 judgment + permanent injunction to cease scraping and destroy all scraped data and derived algorithms** — driven by ToS breach + fake-profile use + spoliation, not pure public-data access ([Privacy World](https://www.privacyworld.blog/2022/12/linkedins-data-scraping-battle-with-hiq-labs-ends-with-proposed-judgment/), [Morgan Lewis](https://www.morganlewis.com/blogs/sourcingatmorganlewis/2022/12/linkedin-v-hiq-landmark-data-scraping-suit-provides-guidance-to-data-scrapers-and-web-operators)). Lesson: **stay logged out, respect robots/ToS, never fabricate accounts, never bulk-scrape a gated platform.**
- **EU (GDPR):** "public" ≠ "free to process." All personal data is protected regardless of source. The **CNIL 19 June 2025 focus sheet** is the most authoritative recent signal: legitimate interest *can* cover **even commercial prospecting** via scraping, **but only with mandatory safeguards** — limit to freely-accessible data, **exclude sites that object to scraping**, respect robots, exclude sensitive categories, **delete irrelevant data promptly**, and offer a **prior opt-out** ([CNIL focus sheet](https://www.cnil.fr/en/legal-basis-legitimate-interest-focus-sheet-measures-implement-case-data-collection-web-scraping), [Clifford Chance](https://www.cliffordchance.com/insights/resources/blogs/talking-tech/en/articles/2025/06/web-scraping-for-ai-development--the-cnil-builds-on-edpb-guidanc.html)). A Polish-register scraping fine was overturned on appeal but the **ban on scraping that public register's personal data was upheld** — public registry ≠ free reuse.
- **CAN-SPAM / outreach:** finding a public business email is legal in most jurisdictions; **using** it for outreach must carry an unsubscribe + honor opt-outs.

**Sovereignty fit: excellent.** Everything runs in your cell, no third-party data egress (beyond the public sources you query), no redistribution license to inherit. This is the natural home for a sovereign posture — *with disciplined collection hygiene baked in as code, not policy.*

---

## 3. LLM-agent live web research

An agent (Brigade DeepResearchAgent-style) that, on demand, searches + fetches the live web to assemble a firmographic/contact record per target.

### Cost
Per-lookup token + tool-call cost. Inference is ~free on your local stack (your topology already runs LLMs on bigboy 3090 / midboy P40); the marginal cost is latency + search-API calls. A **single-shot** LLM is cheap but caps at **~60–70% accuracy** on multi-step tasks; a verified **orchestrator-worker + reflexion loop runs 10–30s** per task ([Stevens, AI agent economics](https://online.stevens.edu/blog/hidden-economics-ai-agents-token-costs-latency/), [VentureBeat agentic runtime](https://venturebeat.com/resources/the-agentic-reckoning-enterprise-ai-organizations-have-a-runtime-problem-not-a-model-problem)). Prompt-caching the system/schema cuts input cost ~90%.

### Data quality / freshness
**Freshest possible** (live web at query time) and **best for the long tail** / firmographic narrative (what does this company do, recent news, leadership). **Weakest on precision contact fields** — emails/phones invented or stale. **Hallucination propagation ~24%**: an early wrong inference compounds across steps ([Maxim AI hallucination state](https://www.getmaxim.ai/articles/the-state-of-ai-hallucinations-in-2025-challenges-solutions-and-the-maxim-ai-advantage/)). Agents are "impressive one minute, fail the next" — **not reliable as an unsupervised system of record.**

### Legal
Same scraping/GDPR rules as §2 apply to whatever the agent fetches — **the agent is just an automated collector**, so it must inherit the same hygiene (logged-out, robots-respecting, no gated platforms, Art. 14 source-tracking). One added requirement: **provenance capture** — the agent must record *which URL* each fact came from, both for Art. 14 disclosure and to let you verify/refresh.

### Sovereignty fit
**Excellent** if the model is local (it is, in your stack). No data egress except the public fetches; nothing redistributed. Fits your "agentic + human co-equal interfaces, no wrappers" principle perfectly — the agent IS a first-class interface over the same enrichment engine.

---

## 4. Recommended architecture — hybrid, liability-layered

Build a **Contact-Ops Enrichment Service** with a **provider abstraction** (a `ResolverProvider` interface, exactly like your pluggable `BILLING_PROVIDER` pattern). Each field-type resolves through a **waterfall** of providers ordered by sovereignty + cost + reliability, with **provenance + confidence stamped on every field** (you already have ProvenanceBadge + a canonical/provenance distinction in Contact-Ops). Never store a value without `{source, method, fetched_at, confidence}`.

### Layer 1 — BUILD / self-host (the sovereign foundation; default-on)
This is your moat and your compliance story.
- **Firmographics from authoritative public registries**: SEC EDGAR + Companies House (free APIs) as the spine; add `company_dns`-style Wikidata/SIC join. Use **OpenCorporates only via a paid commercial license** if you need its 140-jurisdiction breadth (budget from £2,250/yr) — **do not** free-tier-and-redistribute it into a closed product.
- **First-party signal is your best data**: you already capture real attendees/speakers in Meeting-Ops and leads in Customer-Ops, federated into Contact-Ops as the SoT. *Enrichment derived from your own customers' interactions has the cleanest legal basis and zero redistribution risk* — lean into this hard; it's the thing brokers can't sell and competitors can't copy.
- **Email discovery + verification engine**: self-hosted permutation generator + layered MX/SMTP verification, with explicit **catch-all detection** that downgrades confidence rather than asserting validity. Run from warmed IPs in your cell.
- **OSINT correlation modules**: wrap SpiderFoot (self-hosted, 200+ no-key modules) + holehe (account existence) + Maigret/theHarvester behind the same interface for social-handle + footprint enrichment. Enforce collection hygiene **in code**: logged-out only, robots/opt-out respected, no gated-platform scraping, CNIL exclusion-list support.

### Layer 2 — AGENT (live web research; on-demand, supervised)
- A Brigade **enrichment agent** for the **long tail and the narrative**: firmographic descriptions, recent news, org charts, "who is this person at this company" — cases registries + APIs miss.
- **Constrain it for reliability**: structured-output schema, **mandatory per-field source URLs**, a verification/reflexion pass, and a **confidence gate** — anything below threshold returns "unverified, needs human review" rather than writing to the SoT. Treat agent output as **provenance=agent_inferred**, never **canonical**, until human- or cross-source-confirmed (mirror your Meeting-Ops speaker-fingerprint 0.80-threshold pattern and the FAKE-tag cleanup discipline you already practice).
- This is also where it satisfies your **agent-first** principle: enrichment is exposed as both an MCP tool (agents) and a GUI action (humans), co-equal over one engine.

### Layer 3 — BUY (commercial API; opt-in fallback, behind a flag, per-field, never bulk)
- Same `ResolverProvider` interface; wire **Hunter** (cheapest, clean for email find/verify) and optionally **PDL** (best developer API; consider its **on-prem bulk license** *only* if a customer's contract explicitly permits, since on-prem keeps egress in-cell) as **last-resort fallbacks** for fields Layers 1–2 couldn't resolve.
- **Hard rules:** (a) **opt-in per deployment** via env flag (sovereign customers leave it OFF — preserving "no third-party egress"); (b) **per-record API calls only, never bulk ingest/redistribute** a broker database; (c) **DPA on file** with every vendor enabled; (d) stamp `provenance=third_party:<vendor>` so you can honor erasure and Art. 14 source-disclosure. **Explicitly avoid** bulk-buying Apollo/ZoomInfo data into the product — they "sell personal information," and redistributing it would break both your sovereignty promise and GDPR.

### Cross-cutting compliance scaffolding (build once, applies to all layers)
- **Provenance + freshness on every field** (you're already wired for this).
- **A real DSAR/erasure pipeline**: a person's opt-out must purge across all layers including cached third-party values, within 30 days (24–48h for opt-outs) — this is non-negotiable and is itself a *selling point* for a sovereignty brand.
- **Per-deployment legal-basis config + LIA template** and **Article 14 source-disclosure** surfaced in the UI (so a customer can show a data subject where a field came from).
- **Collection hygiene as enforced code** for Layers 1–2: logged-out, robots/opt-out-respecting, CNIL exclusion-list, no gated platforms, no fabricated accounts (the hiQ lesson).

### One-line decision rule
**Build the registry + first-party + OSINT + verification core and run it in-cell (default-on, sovereign); add a constrained, provenance-stamped live-web agent for the long tail; keep commercial APIs as an opt-in, per-field, DPA-gated fallback behind the abstraction — and never bulk-ingest or redistribute a broker's person graph.** Cost and quality favor a hybrid; **legal liability is what forces the layering** — the cleanest, freshest, most defensible data is the data you build/collect yourself, and that is also the only data fully compatible with a sovereign, no-egress posture.

---

**Key sources:** [PDL data license](https://docs.peopledatalabs.com/docs/data-license.md) · [Apollo CCPA "we sell personal information"](https://www.apollo.io/privacy-policy/ccpa-privacy-statement) · [OpenCorporates API licensing](https://api.opencorporates.com/documentation/API-Reference) · [Hunter pricing](https://hunter.io/pricing) · [PDL pricing](https://fullenrich.com/content/people-data-labs-pricing) · [GDPR enrichment guide (Art 6/14, LIA, DPA)](https://derrick-app.com/en/gdpr-data-enrichment/) · [CNIL 2025 web-scraping focus sheet](https://www.cnil.fr/en/legal-basis-legitimate-interest-focus-sheet-measures-implement-case-data-collection-web-scraping) · [hiQ final consent judgment $500K + injunction](https://www.privacyworld.blog/2022/12/linkedins-data-scraping-battle-with-hiq-labs-ends-with-proposed-judgment/) · [Meta v. Bright Data / scraping case law](https://iapp.org/news/a/data-scraping-and-the-implications-of-the-latest-linkedin-hiq-court-ruling) · [SpiderFoot](https://github.com/smicallef/spiderfoot) · [theHarvester](https://github.com/laramies/theHarvester) · [holehe](https://apify.com/anshumanatrey/holehe-email-osint) · [catch-all/SMTP verification limits](https://prospeo.io/s/how-to-find-domain-email-address) · [data accuracy bake-offs](https://www.cleanlist.ai/blog/15-best-b2b-data-enrichment-providers-in-2025-ranked) · [AI agent latency/accuracy economics](https://online.stevens.edu/blog/hidden-economics-ai-agents-token-costs-latency/) · [agent hallucination state](https://www.getmaxim.ai/articles/the-state-of-ai-hallucinations-in-2025-challenges-solutions-and-the-maxim-ai-advantage/) · [CFPB data-broker rulemaking](https://www.federalregister.gov/documents/2024/12/13/2024-28690/protecting-americans-from-harmful-data-broker-practices-regulation-v) · [CFPB Circular 2024-06 (FCRA hiring dossiers)](https://www.consumerfinance.gov/compliance/circulars/consumer-financial-protection-circular-2024-06-background-dossiers-and-algorithmic-scores-for-hiring-promotion-and-other-employment-decisions/)
