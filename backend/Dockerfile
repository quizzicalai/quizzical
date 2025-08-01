# backend/Dockerfile
FROM python:3.12-slim-bookworm

# ---- system deps ----
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl && \
    rm -rf /var/lib/apt/lists/*

# ---- Poetry ----
ENV POETRY_VERSION=2.1.1 \
    POETRY_HOME=/opt/poetry \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_CACHE_DIR=/var/cache/pypoetry
RUN curl -sSL https://install.python-poetry.org | python3 - --version $POETRY_VERSION \
 && ln -s $POETRY_HOME/bin/poetry /usr/local/bin/poetry

# ---- Python runtime tweaks ----
ENV PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100

WORKDIR /app

# Only copy dependency metadata first for better layer‑caching
COPY pyproject.toml poetry.lock* README.md /app/
RUN poetry install --no-interaction --no-ansi --no-root

# Then copy the rest of the source
COPY . /app

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
