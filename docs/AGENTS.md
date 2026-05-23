# Contact-Ops Agent Fleet — Framework Guide

**Status**: Phase 3.0 Foundation (substrate only — actual agents land in 3.1+)
**Scope**: BaseAgent contract, trust ladder, cost guards, observability, DLQ,
outbox, circuit breaker. Plus the 7 admin MCP tools and the CLI.

Read this first if you are writing a new agent or operating the fleet.

---

## 30-second tour

Every Phase 3 agent inherits from `BaseAgent`, declares a frozen `AgentDef`
at module import time, and implements `_run(ctx)`. The base class wraps the
run in:

* an OpenTelemetry span (GenAI semantic conventions)
* the 5-layer cost guard
* the per-agent + fleet-wide circuit breaker
* the idempotent `propose_action` writer that lands into `action_event`
* the Beta(α, β) trust ladder lookup that decides auto-apply vs propose

A new agent is therefore ~200 lines of Python: schema declarations + the
`_run` body that calls `propose_action`. Everything else is inherited.

```python
from contact_ops.agents import (
    AgentClass, AgentContext, AgentDef, AgentResult, BaseAgent,
    Reversibility, TrustTier, register_agent,
)


class MyAgent(BaseAgent):
    async def _run(self, ctx: AgentContext) -> AgentResult:
        proposed = await self.propose_action(
            ctx=ctx,
            event_type="my.update",
            aggregate_type="person",
            aggregate_id=ctx.event_payload["person_id"],
            payload_before={...},
            payload_after={...},
            confidence=0.87,
            reversibility=Reversibility.REVERSIBLE,
            evidence={"source": "my-pipeline"},
            rationale="why I think this",
        )
        return AgentResult(proposals_emitted=1)


MY_AGENT = AgentDef(
    slug="my-agent",
    name="My Agent",
    version="1.0.0",
    agent_class=AgentClass.BATCH,
    description="What it does",
    cost_budget_monthly_cents=2000,
    initial_trust_tier=TrustTier.T0_PROBATION,
    triggers=("0 */6 * * *",),
    declared_capabilities=("contacts.list_recent",),
)
register_agent(MY_AGENT)
```

---

## The five contracts

### 1. `AgentDef` declaration

Static metadata, immutable per release. The frozen dataclass enforces
invariants at construction:

* `slug` must be alnum + `-_` only.
* `cost_budget_monthly_cents` must be positive.
* `BATCH` agents need a 5-field cron trigger or `"manual"`.
* `EVENT_DRIVEN` agents need at least one `"event:<channel>"` trigger.
* `CONTINUOUS` agents need a `"continuous"` trigger entry.

The registry rejects two `register_agent(...)` calls with the same slug
but different bodies — this catches accidental version drift at import.

### 2. `BaseAgent._run`

The subclass implementation. Returns an `AgentResult` reporting how many
proposals were emitted vs auto-applied. Raise exceptions to signal:

* `RetryableError` — Celery retries with backoff; on exhaustion the
  action lands in `agent_action_dlq`.
* `CostBudgetExceededError` — a layer-1..5 guard fired; no replay.
* `CircuitBreakerOpenError` — per-agent or fleet breaker is open.
* `AgentError` (any other subclass) — routes straight to DLQ.

Anything else (uncaught) becomes a DLQ entry with `error_class='other'`.

### 3. `propose_action`

The single entry point for any agent mutation. It writes one
`action_event` row with:

* `idempotency_key = sha256(slug || tenant || aggregate || type || content)`
* `decision_payload` — the cached decision (replay-safe retries)
* `reversibility_class` — gates auto-apply at every tier
* `trust_tier_at_creation` — the tier the agent had when it ran
* `triggered_by` — `"cron:..."` | `"event:..."` | `"manual"`
* `agent_version` — for forensic time-travel ("which v of dedup made this?")

Auto-apply is policy, not a flag. The combination of (tier, confidence,
reversibility) determines `status = 'applied'` vs `'proposed'`:

| Tier | Auto-apply rule |
|------|-----------------|
| T0 Probation | Never auto-applies |
| T1 Trainee | Never auto-applies |
| T2 Trusted | Reversible + confidence ≥ 0.95 |
| T3 Senior | Reversible + confidence ≥ 0.85 |
| T4 Principal | All reversible actions |

`Reversibility.IRREVERSIBLE` and `Reversibility.SOFT_DELETE` **never**
auto-apply, regardless of tier or confidence.

### 4. Idempotency + replay

If two runs of the same agent see the same input, they must produce the
same `idempotency_key` and the second write is a no-op. The base class
detects key reuse and returns `ProposedAction.status = 'replayed'` with
the original `event_id`. Set `allow_replay=False` to raise
`IdempotencyKeyReusedError` instead — useful in tests.

### 5. Cost guards

Five layers, all checked before any LLM call. The agent code does:

```python
await ctx.cost_guard.check_and_record(
    agent_slug="dedup",
    tenant_id=ctx.tenant_id,
    estimated_tokens=512,
    estimated_cents=3,
)
... call LLM ...
ctx.cost_guard.record_call(cents=actual_cents)
```

Layers fire in order:

1. **Per-request tokens** — hard cap on `max_tokens_per_call`.
2. **Per-run turn counter** — `MAX_TURNS=25` default.
3. **Session budget** — `SESSION_BUDGET_CENTS=50` default.
4. **Daily tenant budget** — `DAILY_TENANT_BUDGET_CENTS=1000` default.
5. **Monthly tenant budget** — hard-stop at 100%; tiered alerts at
   75/90/95/100% routed to `cost_budget_violation`.

All violations write a row to `cost_budget_violation` for downstream
alerting (Slack + email).

---

## Trust ladder

Per (agent × tenant × visibility), Beta(α, β) where α = approvals + 1
and β = rejections + 1. Updated daily by the Calibration Daemon
(Phase 3.4) walking `action_event`.

The current tier is the **minimum** of the posterior-implied tier and the
stored tier — this honors sticky demotion. If drift triggered a demotion
yesterday, the agent stays demoted until the next daily recompute.

Promotion gates (see `agents/trust.py`):

* T0 → T1: ≥ 25 samples, mean ≥ 0.70, lower_ci_90 ≥ 0.60
* T1 → T2: ≥ 100 samples, mean ≥ 0.85, lower_ci_95 ≥ 0.78
* T2 → T3/T4: ≥ 500 samples, mean ≥ 0.95, lower_ci_95 ≥ 0.90 → T4
  (else T3)

Demotion fires on any of:

* PSI between 7d and 30d window ≥ 0.20
* Rolling 7d mean drops > 15pp below 30d baseline
* Three consecutive PSI ≥ 0.10 warning days

---

## Outbox + LISTEN/NOTIFY

Postgres `NOTIFY` is transactionally consistent but lossy if no listener
is connected. The `event_outbox` table is the durability backstop:

1. `publish(...)` inserts a row in the same transaction as the
   triggering domain change.
2. A table trigger fires `pg_notify(channel, payload)` after commit.
3. Subscribers run two loops:
   * `LISTEN` — fast path, catches NOTIFY as it arrives.
   * Polling sweeper — every 10s, claims any unprocessed event older
     than 30s via `FOR UPDATE SKIP LOCKED`.

Both paths share the same `claim_batch` lock, so each event is handled
exactly once.

---

## DLQ

Actions that exhaust Celery retries land in `agent_action_dlq` with an
`error_class` tag. Operators replay via the `drain_dlq` MCP tool or the
CLI. Replay handlers ship per-agent in 3.1+ — Phase 3.0 only provides
the substrate. `replayable=false` blocks future replays for entries that
need manual resolution.

---

## Circuit breakers

Two scopes:

* **Per-agent.** Opens when `revert_rate_24h ≥ 0.50` AND
  `total_outcomes ≥ 20` over a 1-hour window. The agent refuses to run
  until `resume_agent` is called.
* **Fleet-wide.** Opens when `revert_rate_24h ≥ 0.10` across **all**
  applied proposals in the last hour. Everything routes to manual review
  until Aaron acknowledges.

State lives in Redis with a 1-hour TTL so forgotten breakers un-trip on
their own. Every open/close transition writes to `circuit_breaker_ack`
for the audit trail.

---

## Observability

Every agent run is one OTel span (`agent.<slug>`) with the GenAI semantic
conventions filled in when an LLM is called. The Laminar collector
(self-hosted, Apache-2.0) reads the OTLP gRPC stream at port 4317 and
provides the transcript view + SQL editor over traces. See
`docs/AGENTS_DEPLOY.md` for the deploy story.

Prometheus metrics exposed on `/metrics`:

* `contactops_agent_latency_seconds{agent,tenant,tier}` — histogram
* `contactops_agent_success_total{agent,tenant,outcome}` — counter
* `contactops_agent_revert_rate_24h{agent,tenant}` — gauge
* `contactops_agent_tokens_total{agent,model,tenant,kind}` — counter
* `contactops_agent_cost_cents_total{agent,model,tenant}` — counter
* `contactops_agent_queue_depth{agent,tier}` — gauge
* `contactops_agent_lag_seconds{agent}` — gauge
* `contactops_agent_trust_beta{agent,tenant,param}` — gauge

centerdeep Prometheus scrapes `/metrics` directly; dashboards live in
the existing ecosystem Grafana.

---

## CLI

```
python -m contact_ops.agents.cli list
python -m contact_ops.agents.cli trust --agent dedup --tenant <uuid>
python -m contact_ops.agents.cli promote --agent dedup --tenant <uuid>
python -m contact_ops.agents.cli demote --agent dedup --tenant <uuid>
python -m contact_ops.agents.cli dlq --limit 50
python -m contact_ops.agents.cli pause --agent dedup --reason "drift investigation"
python -m contact_ops.agents.cli resume --agent dedup --reason "fixed"
python -m contact_ops.agents.cli simulate-trust --alpha 51 --beta 1
```

The 7 MCP admin tools (`list_agents`, `get_agent_trust`,
`promote_agent_tier`, `demote_agent_tier`, `drain_dlq`, `pause_agent`,
`resume_agent`) provide the same operations to ADMIN-role JWT holders.
All require the `contactops:agents.admin` scope.

---

## Worked example: the EchoAgent

`tests/fixtures/echo_agent.py` ships a complete, runnable agent. It is
40 lines of Python: register, implement `_run`, return `AgentResult`.
The test file `tests/test_agent_base.py` exercises six end-to-end
scenarios against it — replay, auto-apply at T3, irreversible never
auto-applies, decision_payload capture, idempotency, validation errors.

Use it as the template when wiring 3.1 Dedup or 3.2 Voice Match.

---

## Phase 4 GraphSyncWorker

`GraphSyncWorker` is a CONTINUOUS agent registered as `graph-sync-worker`.
It is mechanical replication, so it does not write `action_event` and does not
call MCP tools. It claims `graph_sync_outbox` rows with
`FOR UPDATE SKIP LOCKED`, routes each row to the row's tenant graph
(`contact_ops__<slug>`), and writes idempotent `MERGE` Cypher through the async
FalkorDB wrapper.

Retries are best-effort. After five failed attempts a row is marked `dlq` and
copied into `graph_sync_dlq`; Postgres writes never block on FalkorDB.

---

## Phase 3.1+ checklist for new agents

When you write the next agent:

- [ ] Subclass `BaseAgent`.
- [ ] Declare an `AgentDef` and `register_agent(...)` at module top.
- [ ] Implement `_run` returning an `AgentResult`.
- [ ] Pre-flight every LLM call through `ctx.cost_guard.check_and_record`.
- [ ] Tag every external write with `reversibility=` (IRREVERSIBLE only
      when there is no rollback path).
- [ ] Set `confidence` honestly — the trust ladder is calibrated against
      the agent's own self-report.
- [ ] Write at least 6 contract tests (see `test_agent_base.py` as the
      template).
- [ ] Add a Grafana panel to the per-agent SLO dashboard (Section 8 of
      `Contact-Ops-Phase-3-Design.md`).
- [ ] Document the per-(action × reversibility) mapping in your agent's
      module docstring — future readers will need it.
