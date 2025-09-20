# -----------------------------
# Stage 1: builder
#
# This stage installs all dependencies, including those needed for building
# packages from source. The final runtime image will not contain these
# build-time-only tools, keeping it small and secure.
# -----------------------------
FROM python:3.11-slim AS builder

# Install build-essential which contains the C compiler (cc), make, and other
# tools required for compiling Python packages with native extensions (e.g., fastuuid).
# We update apt, install, and then clean up in a single layer to optimize image size.
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Poetry (exact version)
ENV POETRY_VERSION=2.1.1
RUN pip install "poetry==$POETRY_VERSION"

# Poetry: install into system site-packages (no venv)
RUN poetry config virtualenvs.create false

# Copy dependency files to leverage Docker layer cache
# This ensures we only re-run the lengthy install step if these files change.
COPY poetry.lock pyproject.toml README.md ./

# Install runtime dependencies only.
# This command failed previously due to the missing build tools.
RUN poetry install --no-root --no-interaction --no-ansi -vvv

# -----------------------------
# Stage 2: final runtime
#
# This is the final, lean image that will be used to run the application.
# It copies the pre-installed packages from the builder stage without
# including the build tools.
# -----------------------------
FROM python:3.11-slim AS final

# Create a non-root user for better security (VS Code-friendly)
RUN useradd -m -s /bin/bash -u 1000 appuser

WORKDIR /app

# Copy installed packages and binaries from the builder stage.
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source code into the container.
# The backend/.env file is read at runtime by the app via pydantic settings.
COPY --chown=appuser:appuser . .

# Do NOT bake runtime env variables here—Docker Compose supplies them via env_file
USER appuser

# Launch the FastAPI application using Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
