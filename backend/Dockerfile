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
ENV POETRY_VERSION=2.1.1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}" && \
    poetry config virtualenvs.create false

# Leverage Docker layer cache for deps
COPY poetry.lock pyproject.toml README.md ./
RUN poetry install --no-root --no-interaction --no-ansi -vvv
# ^ Installs "main" dependencies into global site-packages

# ======== Stage 2: final (small runtime image) ==========================
FROM python:3.11-slim AS final

# Install minimal runtime libs only (e.g., libpq5 for psycopg/pgvector)
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/*

# Non-root for better security
RUN useradd -m -s /bin/bash -u 1000 appuser
WORKDIR /app

# Bring in site-packages and console scripts from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# App source (copy the backend/ directory content when build context is ./backend)
# NOTE: In CI (Azure), set build context to ./backend so this copies only backend code.
COPY --chown=appuser:appuser . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser

# Container Apps will route to this port; Compose maps 8000:8000
EXPOSE 8000

# Default prod command (Compose overrides with --reload for local)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
