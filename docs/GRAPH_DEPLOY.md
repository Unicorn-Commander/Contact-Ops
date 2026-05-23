# Phase 4 Graph Deploy Runbook

## FalkorDB Bootstrap

1. Start or verify the shared FalkorDB instance from `FALKORDB_URL`.
2. Confirm each tenant row has `graph_mode='per_org_graph'` and
   `graph_name='contact_ops__<slug>'`.
3. Run the worker once, then start the continuous process separately from the
   API process.

## Backfill

```bash
cd backend
python -m scripts.backfill_graph --tenant=aaron-personal --dry-run
python -m scripts.backfill_graph --tenant=aaron-personal
python -m scripts.backfill_graph --tenant=aaron-personal
```

The second and third commands should produce identical node and edge counts
because all writes use `MERGE`.

## Monitoring

Alert when:

- `graph_sync_outbox` pending depth is above 1000 for more than 5 minutes.
- Any row reaches `graph_sync_dlq`.
- FalkorDB ping fails from `contact_ops.agents.graph_sync.health`.

## Tenant Deletion Gap

Phase 4 intentionally does not delete FalkorDB graphs when tenants are deleted.
The graph is retained for audit. A future ADMIN-only
`delete_tenant_graph(tenant_id, confirm_phrase)` tool must implement explicit
erasure.
