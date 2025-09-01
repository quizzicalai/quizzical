# -----------------------------
# Build args (available at build time)
# -----------------------------
ARG APP_CONFIG_ENDPOINT
ARG APP_ENVIRONMENT
ARG SECRET_KEY
ARG DATABASE_PASSWORD
ARG OPENAI_API_KEY

# -----------------------------
# Stage 1: builder
# -----------------------------
FROM python:3.11-slim AS builder

WORKDIR /app

# Install Poetry (exact version)
ENV POETRY_VERSION=2.1.1
RUN pip install "poetry==$POETRY_VERSION"

# Poetry: install into system site-packages (no venv)
RUN poetry config virtualenvs.create false

# Copy dependency files to leverage Docker layer cache
COPY poetry.lock pyproject.toml README.md ./

# Install runtime deps only
RUN poetry install --no-root --no-interaction --no-ansi -vvv

# -----------------------------
# Stage 2: final runtime
# -----------------------------
FROM python:3.11-slim AS final

# Non-root user (VS Code-friendly)
RUN useradd -m -s /bin/bash -u 1000 appuser

WORKDIR /app

# Copy installed packages and binaries from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY --chown=appuser:appuser . .

# Promote selected build-time args to runtime ENV (values also provided by env_file at runtime)
ENV APP_CONFIG_ENDPOINT=${APP_CONFIG_ENDPOINT}
ENV APP_ENVIRONMENT=${APP_ENVIRONMENT}
ENV SECRET_KEY=${SECRET_KEY}
ENV DATABASE_PASSWORD=${DATABASE_PASSWORD}
ENV OPENAI_API_KEY=${OPENAI_API_KEY}

# Drop root
USER appuser

# Launch FastAPI via uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
