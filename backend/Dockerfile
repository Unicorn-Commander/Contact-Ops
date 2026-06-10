# Backend Dockerfile for Contact-Ops
# Python 3.12-slim-bookworm base with production-quality practices

# Stage 1: Build dependencies
FROM python:3.12-slim-bookworm AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt

# Stage 2: Runtime
FROM python:3.12-slim-bookworm AS runtime

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --gid 1000 contact_ops \
    && useradd --uid 1000 --gid contact_ops --shell /bin/bash --create-home contact_ops

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

COPY --from=builder /app/wheels /wheels
COPY --from=builder /app/requirements.txt .
RUN pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

COPY --chown=contact_ops:contact_ops contact_ops/ ./contact_ops/
COPY --chown=contact_ops:contact_ops alembic/ ./alembic/
COPY --chown=contact_ops:contact_ops alembic.ini .

USER contact_ops

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8501/health || exit 1

CMD ["python", "-m", "contact_ops.ops.prestart"]
