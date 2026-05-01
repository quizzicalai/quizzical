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

# App source (copy the backend/ directory content when build context is ./backend)
# NOTE: In CI (Azure), set build context to ./backend so this copies only backend code.
COPY --chown=appuser:appuser . .

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

# Default prod command (Compose overrides with --reload for local)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
