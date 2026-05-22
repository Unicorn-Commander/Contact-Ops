# Agent Fleet Deploy

How to bring the Celery worker pool + beat scheduler + Laminar collector
online on bigboy (or any equivalent host).

## docker-compose (dev / single-host prod)

Three new services in `docker-compose.yml`:

* **`contact-ops-redis-dev`** — broker + circuit-breaker cache. Lives on
  Redis db 5 (existing app) + db 9 (agent fleet).
* **`contact-ops-celery-worker`** — runs the task surface. Subscribes to
  queues `agents.batch.high`, `agents.batch.low`, `agents.calibration`.
* **`contact-ops-celery-beat`** — cron scheduler. Builds the schedule
  from the in-process registry on startup.
* **`contact-ops-laminar`** (profile: `observability`) — OTLP collector
  with the Laminar UI. Opt-in to avoid pulling the ~1GB image by default.

```bash
docker compose up -d postgres redis migration contact-ops-celery-worker contact-ops-celery-beat
docker compose --profile observability up -d contact-ops-laminar  # optional
```

The worker connects to Redis at `redis://redis:6379/9` (Celery broker)
and Postgres at the dev DSN. Override via env var
`CONTACT_OPS_AGENTS_BROKER_URL` if you need a different Redis.

## bigboy production (Helm / Compose)

For the bigboy single-host deploy the recommended layout is a separate
`docker-compose.prod.yml` overlay that:

1. Points `REDIS_URL` at the shared `unicorn-redis` container on db 9.
2. Points `DATABASE_URL` at `unicorn-postgresql:5432/contact_ops_db`
   with the `contact_ops_app` role.
3. Pins `OTEL_EXPORTER_OTLP_ENDPOINT` at the centerdeep Laminar instance
   (or a node-local Laminar pod if you keep traces close to the worker).
4. Runs two `contact-ops-celery-worker` replicas with
   `--concurrency=4 --queues=agents.batch.high,agents.batch.low` and a
   third replica with `--queues=agents.calibration --concurrency=1`.
5. Runs exactly one `contact-ops-celery-beat` replica.

```yaml
# docker-compose.prod.override.yml
services:
  contact-ops-celery-worker:
    deploy:
      replicas: 2
      resources:
        limits:
          memory: 1G
          cpus: '1'
    environment:
      REDIS_URL: redis://unicorn-redis:6379/5
      CONTACT_OPS_AGENTS_BROKER_URL: redis://unicorn-redis:6379/9
      OTEL_EXPORTER_OTLP_ENDPOINT: http://centerdeep-laminar.unicorncommander.net:4317
      DATABASE_URL: postgresql+asyncpg://contact_ops_app:${DB_PASSWORD}@unicorn-postgresql:5432/contact_ops_db
```

Stop the existing app stack, apply the override, bring it back up:

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.prod.override.yml up -d
docker compose logs --tail=100 contact-ops-celery-worker
```

## Laminar deployment

Apache-2.0, self-hosted, OTel-native. The dev compose runs the single
container; for production deploy the Helm chart on the centerdeep
Kubernetes cluster or use the same compose pattern with a persistent
volume for Postgres-backed trace storage.

The collector listens on:

* `4317` — OTLP/gRPC (what the agents emit to)
* `4318` — OTLP/HTTP (Laminar UI's own scrapes)
* `5667` — Laminar web UI

Aaron uses the UI to: open a `action_event` row's `trace_id`, see the
full agent run as a transcript, drill into per-LLM-call spans with the
GenAI attributes (input/output tokens, model, finish reason).

## Prometheus scrape

centerdeep Prometheus is the canonical observability stack (per
`project_observability_canonical_centerdeep` memory). Add a scrape job:

```yaml
- job_name: contact-ops-agents
  scrape_interval: 30s
  metrics_path: /metrics
  static_configs:
    - targets:
        - contact-ops-backend:8501
        - contact-ops-celery-worker:8501
```

The Phase 3 dashboards (Fleet overview, Per-agent SLO, Inbox health,
Cost burn, Trust ladder movements) live in the existing ecosystem
Grafana. Their JSON definitions are out of scope for 3.0 Foundation;
they ship with 3.3 (Inbox UI) when there is real data to render.

## Smoke test

```bash
# 1. Bring up the stack
docker compose up -d postgres redis migration

# 2. Verify the new tables exist
docker compose exec postgres psql -U contact_ops_dev -d contact_ops_db_dev -c \
  "\\dt agent_*"

# 3. Bring up the workers
docker compose up -d contact-ops-celery-worker contact-ops-celery-beat
sleep 5

# 4. The worker should log "ready" with the queues it subscribes to.
docker compose logs contact-ops-celery-worker | grep ready

# 5. Smoke-test the CLI inside the backend container.
docker compose exec contact-ops-backend python -m contact_ops.agents.cli list

# 6. Confirm Prometheus can scrape.
curl http://localhost:8501/metrics | grep contactops_agent_
```

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Worker exits with `kombu redis<=5.2.1` error | Wrong redis version | `redis==6.0.0` is pinned; verify `pip show redis` |
| Beat fires nothing | No agents registered in this process | Confirm 3.1+ agent modules are imported on startup |
| `/metrics` returns 401 | JWT middleware not exempting | `/metrics` is in both middlewares' SKIP_PATHS |
| Span tree empty in Laminar | `OTEL_EXPORTER_OTLP_ENDPOINT` unset | Default is `http://contact-ops-laminar:4317`; override for prod |
| Outbox piles up | LISTEN consumer disconnected | The sweeper picks up rows older than 30s automatically; verify the `event_outbox_unprocessed_idx` index exists |
| DLQ depth > 100 | Persistent downstream failure | `python -m contact_ops.agents.cli dlq --limit 100` + investigate `error_class` distribution |
