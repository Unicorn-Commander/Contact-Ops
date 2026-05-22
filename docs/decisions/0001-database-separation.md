# ADR-0001: Separate `contact_ops_db` from `dataintel_db`

**Status**: Accepted
**Date**: 2026-05-21
**Deciders**: Aaron Stransky, Claude (orchestrator)

## Context

Contact-Ops forks from `centerdeep-data-intel`. The natural shortcut is to extend the existing `dataintel_db` schema in place: rename `catalogue_contacts` → `persons`, `catalogue_companies` → `organizations`, add the missing tables additively, ship.

The original brief explored this. After surveying the surface area (~30 new tables, RLS policies, append-only `action_event` with role-level UPDATE/DELETE revocation, HIPAA fence triggers, per-tenant graph naming, etc.), we landed on a different shape: Contact-Ops and Data Intel are peer services in the LinkedIn + Apollo pattern. Contact-Ops is the active management layer; Data Intel is the passive intelligence database. Both consume infrastructure (Postgres instance, Keycloak realm, Garage, Qdrant) but have separate codebases, separate audit logs, separate operational lifecycles, and separate billing models.

## Decision

Contact-Ops uses a new database `contact_ops_db` on the same `unicorn-postgresql` instance as `dataintel_db`. Same instance, separate database.

The existing `dataintel_db` is untouched by Phase 0 migrations. The `dataintel-backend` container at `verify.centerdeep.online` keeps running, keeps owning its tables, keeps serving its API.

For backward compatibility on the contact-finder browser extension flow (which submits to Data Intel via `/catalogue/submit`), Data Intel continues to be the receiver. When Contact-Ops needs canonical contact records that came in via Data Intel, the federation Bridge agent pulls them across (design doc §7).

## Consequences

**Enables:**
- Independent lifecycle: Contact-Ops can be backed up, restored, dropped, white-labeled without touching Data Intel.
- Independent RLS policies, role grants, and HIPAA fence triggers, without retrofitting them onto Data Intel's existing tables.
- Clean separation for white-label customer tenants — a customer's Contact-Ops install does NOT pull from the shared Data Intel by default.
- The two products can ship at different rates without one blocking the other.

**Costs:**
- Two databases on the same instance means two connection pools, two `pg_hba.conf` lines (if needed), two migration directories.
- Federation traffic between them (Bridge agent + Data Intel Publisher client + Data Intel Bridge-Inbound client) is real, observable, and needs to be designed carefully (see ADR-0003 for the client topology that makes this work).
- A single `unicorn-postgresql` instance is a shared blast radius. If Postgres dies, both products die. Acceptable for now; a future split (Contact-Ops on its own Postgres) is a backlog item, not a Phase 0 requirement.

**Reversibility:**
- Reversible. A future decision to collapse the schemas into one DB would require an offline cutover (read-only window, dump + restore, FK rewire), but the data model is compatible — `persons` ≈ `catalogue_contacts` superset, `organizations` ≈ `catalogue_companies` superset.

## Alternatives considered

**1. Extend `dataintel_db` in place.** Rejected: would mix Data Intel's append-only verification job tables with Contact-Ops's RLS-fenced tenant tables, complicating RLS policy and the role-grant story. Also: white-label customers shouldn't get the verification job rows for free.

**2. Move Data Intel into Contact-Ops as a feature.** Rejected: Data Intel has its own customers (lead generation, intelligence search) who don't need or want Contact-Ops's contact-management surface. The two products have different go-to-market.

**3. Different Postgres instances entirely.** Premature. The shared instance is already running, has the extensions we need (pgvector, pg_trgm, etc.), and is operationally cheap. A split is reversible later.

## Implementation notes

- Migration `0017_grant_app_role.py` (Track B's work) grants `contact_ops_app` on the new DB only — Data Intel's role grants are independent.
- Connection strings: `DATABASE_URL` points at `postgresql+asyncpg://contact_ops_app:...@unicorn-postgresql:5432/contact_ops_db`.
- The migration superuser (for Alembic runs) is `unicorn` (already exists). `contact_ops_app` is a non-superuser role created in migration 0001.
- For backward-compat reads of legacy data-intel routes (if any consumer needs them during the transition), the `catalogue_contacts` and `catalogue_companies` views in migration 0003 of Contact-Ops's own DB provide a familiar shape — but these are read-only views over the new `persons` / `organizations` tables, not links back to `dataintel_db`.
