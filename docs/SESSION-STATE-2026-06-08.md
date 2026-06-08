# Contact-Ops — session state & resume point (2026-06-08)

Branch: `feat/csv-import-gui` (PR #5, off `main`). All code committed + pushed.
Deployed on **bigboy** (`/home/muut/contact-ops`, `docker-compose.prod.yml`).
Public: app `contacts.magicunicorn.dev`, MCP `mcp.contacts.magicunicorn.dev`.

## What shipped this session
1. **CSV import in the GUI** (`/import` page now accepts `.csv` — Google Contacts + LinkedIn, header auto-detect) via the **propose-only Review-Queue** path, + **existing-person dedup** (re-import skips people you already have, by email/phone). MCP twin `propose_csv_records`. 18 tests green. Files: `services/csv_import.py`, `services/import_propose.py`, `api/import_csv.py`, `mcp/tools/import_csv.py`, refactored `services/vcard_import.py`; frontend `useVcardImport.ts` + `Import.tsx`.
2. **Knowledge graph — now populated + visible.** Was scaffolded but 0 nodes. Fixed:
   - Backfill `backend/scripts/graph_backfill.py` (env `BACKFILL_TENANT_ID/UC_UID/GRAPH_NAME`). Ran for MUI: **823 people + 135 orgs + 188 WORKS_AT edges** in FalkorDB graph `contact_ops__magic_unicorn_inc`.
   - New `graph_overview` MCP tool (whole-tenant subgraph).
   - Fixed 3 latent bugs: FalkorDB client `--compact` reader bug (now decodes `[Person]` label strings + null-safe floats); the `contactops:graph.read` **scope trap** (was never granted → 403; now CLIENT + `person:read`, same as People); ego graph now always includes the center person.
   - Viewer defaults to the overview + person-search to focus.
3. **Frontend caching fix**: `index.html` now `no-cache` (was heuristic-cached → deploys didn't land on refresh; `frontend/nginx.conf`).

## CURRENT STATE (where we stopped)
- **Graph renders, but only in 2D.** The 3D/WebGL canvas (`react-force-graph-3d`) **does not render on Aaron's machine** (blank). Forced `renderMode` default → `2d` (SVG, no WebGL). Overview now lays out **employer clusters** (org + its people, grid); ego/focused view = center+ring. Panel is collapsible ("Hide"). **Aaron's verdict: graph + review queue "look shitty" → GUI refactor needed.**
- **Review Queue**: **1,032 pending** person.create proposals for MUI = **839 from a Gmail/M365 connector pull** + **193 from CSV**. None duplicate existing contacts by email. They have **NULL confidence** + are all "complete" (name+email/phone) → the confidence-approver (>=0.95) and quality-filter (archives empty/generic) both no-op. Feature-complete UI (bulk approve/reject, filters, keyboard, detail) but cluttered by volume.

## NEXT — the GUI refactor (the ask)
1. **Graph viz = real rebuild, not patches.** Use a **force-directed canvas** layout that renders without WebGL (add `react-force-graph-2d`, swap the 2D render path) so it clusters properly + looks good; clean up the control panel. (The current 2D = hand-rolled SVG ring/cluster — a stopgap.)
2. **Review Queue = UX cleanup** (not a rebuild): calmer rows, obvious bulk bar, handle 1000+ volume. `frontend/src/routes/pages/Inbox.tsx` (1264 lines).
3. **BLOCKER on doing GUI well:** Claude can't see the rendered page (Chrome extension not connected) → all visual work has been blind off Aaron's Desktop screenshots, which is why it's slow/rough. **Unblock = connect the Claude Chrome extension** (Aaron trying his other Claude account) so Claude can iterate live.

## OPEN DECISIONS (awaiting Aaron)
- **Review-queue disposition** for the 1,032: (1) approve the 193 CSV only [recommended], (2) approve all 1,032, (3) clear the 839 connector pull. Run server-side + dedup after.
- **Ongoing graph auto-sync** (so new/approved contacts hit the graph without re-running the backfill): build the transactional-outbox worker. Spec: `~/Documents/Contact-Ops-Graph-Sync-Codex-Prompt.md`. (Project-Ops follow-up.)
- **Workspace**: 3 tenants — `magic-unicorn-inc` (823 contacts + graph), `aaron-personal` (0), `centerdeep` (0). Confirm contacts belong in MUI vs a personal workspace.

## Landmines (for whoever continues)
- 3D/WebGL graph mode is blank on Aaron's machine → use 2D / canvas (`react-force-graph-2d`), not `-3d`.
- Graph read tools gate on **CLIENT + person:read** (not the un-seeded `contactops:graph.read`).
- FalkorDB client must NOT use `--compact` (readers expect decoded scalars + `[Label]` strings).
- Deploys: rebuild image (`docker compose -f docker-compose.prod.yml build <svc>` → `up -d`); migrations are NOT auto-run; `index.html` is now no-cache so refreshes land.
- python-jose (not PyJWT); RLS via `SET LOCAL app.tenant_id` (re-bind after commit in loops); single Alembic head.
