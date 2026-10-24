# Contributing

Thanks for helping improve Contact-Ops. Keep changes focused, explain the behavior they change, and avoid committing real contact data or credentials.

## Local setup

Use the [source setup](README.md#run-from-source) in the README for the development services and environment. The self-host compose cell is the quickest way to exercise the complete Keycloak-backed deployment.

## Checks

Run backend checks from `backend/`:

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
ruff check contact_ops/
pytest tests/ -v --tb=short
```

Run frontend checks from `frontend/`:

```bash
npm ci
npm run build
npm run lint
npm run test
npm run test:e2e
```

## Pull requests

- Describe the user-visible behavior and the verification you performed.
- Keep tenant boundaries, approval gates, audit events, and authentication behavior intact unless the change explicitly addresses them.
- Use synthetic fixtures in tests and screenshots.
- Do not include `.env` files, access tokens, OAuth secrets, connector credentials, or personal contact data.
- Update the README when a supported command, configuration contract, or agent surface changes.
