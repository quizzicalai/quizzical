# -----------------------------------------------------------------------------
# Backend (FastAPI) - Multi-stage build
# Works for local dev (Compose) and Azure Container Apps
# -----------------------------------------------------------------------------

# ======== Stage 1: builder (installs build tools + Python deps) =========
FROM python:3.11-slim AS builder

# System deps for building native wheels (e.g., fastuuid). Keep lean.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \ 
    build-essential \
    libpq-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Use a consistent workdir (fixes prior /appa typo)
WORKDIR /app

# Poetry install (no venv → site-packages inside image)
ENV POETRY_VERSION=2.3.2 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}" && \
    poetry config virtualenvs.create false

# Leverage Docker layer cache for deps
COPY poetry.lock pyproject.toml README.md ./
RUN poetry install --no-root --no-interaction --no-ansi
# ^ Installs "main" dependencies into global site-packages

# ======== Stage 2: final (small runtime image) ==========================
FROM python:3.11-slim AS final

# Install minimal runtime libs only (e.g., libpq5 for psycopg/pgvector).
# curl is kept lightweight and used by HEALTHCHECK below.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Non-root for better security
RUN useradd -m -s /bin/bash -u 1000 appuser
WORKDIR /app

# Bring in site-packages and console scripts from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Drop build-time-only packages so their CVEs do not surface on the running
# image. These are no longer needed once the runtime image is assembled.
# We both pip-uninstall and rm the *.dist-info directories because some bundled
# wheels (e.g. the wheel package shipped with pip) leave metadata behind.
RUN set -eux; \
    pip uninstall -y --quiet poetry poetry-core poetry-plugin-export build wheel jaraco.context jaraco.text jaraco.functools jaraco.collections || true; \
    find /usr/local/lib/python3.11/site-packages -type d \
        \( -iname 'poetry-*.dist-info' -o -iname 'poetry_core-*.dist-info' \
           -o -iname 'poetry_plugin_export-*.dist-info' \
           -o -iname 'wheel-*.dist-info' -o -iname 'wheel' \
           -o -iname 'jaraco.context-*.dist-info' \
           -o -iname 'jaraco.classes-*.dist-info' \
           -o -iname 'jaraco.functools-*.dist-info' \
           -o -iname 'build-*.dist-info' -o -iname 'build' \) \
        -prune -exec rm -rf {} +; \
    # Stop Trivy from reading vendored copies that ship inside pip itself.
    rm -rf /usr/local/lib/python3.11/site-packages/pip/_vendor/wheel* \
           /usr/local/lib/python3.11/site-packages/pip/_vendor/distlib* || true

# App source — EXPLICIT allowlist (P0-4). Never `COPY . .`: that baked the
# build context's .env (API keys, OPERATOR_TOKEN, HMAC secrets) into an image
# layer recoverable by anyone with registry pull access. Copy only what the
# runtime needs: the application package and the non-secret YAML config that
# config.py reads from /app/appconfig.local.yaml. Secrets are injected at
# runtime via Key Vault / Container App env vars.
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser appconfig.local.yaml ./appconfig.local.yaml

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser

# Container Apps will route to this port; Compose maps 8000:8000
EXPOSE 8000

# OCI image labels for traceability (overridden via --label or build args).
LABEL org.opencontainers.image.title="quizzical-api" \
      org.opencontainers.image.source="https://github.com/quizzicalai/quizzical" \
      org.opencontainers.image.licenses="Proprietary" \
      org.opencontainers.image.documentation="https://github.com/quizzicalai/quizzical/blob/main/backend/README.md"

# Container-level liveness so orchestrators (incl. local Docker) can detect a
# stuck process even if the platform probe is misconfigured.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/readiness || exit 1

# Default prod command (Compose overrides with --reload for local).
#
# Run uvicorn UNDER a gunicorn master rather than as a bare process so a hung
# or OOM-killed worker is automatically respawned by the master (reliability
# win over the previous single bare `uvicorn` process). The worker class is
# uvicorn's ASGI worker, so request handling is byte-for-byte the same.
#
# Worker count comes from $WEB_CONCURRENCY and DEFAULTS TO 1. At -w 1 this is
# behaviour-equivalent to the old bare-uvicorn CMD (same single worker, same
# bind 0.0.0.0:8000) PLUS master-supervised auto-restart.
#
# WARNING: raising WEB_CONCURRENCY above 1 is NOT safe until the durable Redis
# checkpointer lands (parallel PR). Today agent/LangGraph state is held
# in-memory per worker; multiple workers would split that state across
# processes and a follow-up request could land on a worker that has never seen
# the in-flight quiz. Keep this at 1 until Redis-backed checkpointing is the
# default.
#
# Shell form (not exec/JSON form) is intentional so ${WEB_CONCURRENCY:-1} is
# expanded by the shell at container start; in exec/JSON form it would be passed
# to gunicorn as a literal string.
#
# `exec` is REQUIRED: shell-form CMD runs under `/bin/sh -c`, which would
# otherwise stay PID 1 and swallow signals. `exec` makes gunicorn REPLACE the
# shell so it becomes PID 1 and receives SIGTERM directly when Container Apps
# scales in / redeploys. Without it, SIGTERM hits `sh` (not gunicorn), breaking
# gunicorn's --graceful-timeout AND the FastAPI lifespan shutdown (which cancels
# the agent-recovery task and drains resources). `exec` does NOT prevent
# ${WEB_CONCURRENCY:-1} expansion — the shell parses/expands the line first,
# then exec's gunicorn with the already-expanded args.
STOPSIGNAL SIGTERM
CMD exec gunicorn -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-1} --bind 0.0.0.0:8000 --timeout 120 --graceful-timeout 30 app.main:app
