# backend/Dockerfile

# Stage 1: The "builder" stage to install dependencies
FROM python:3.11-slim as builder

WORKDIR /app

# Set and install the exact version of Poetry
ENV POETRY_VERSION=2.1.1
RUN pip install "poetry==$POETRY_VERSION"

# Tell Poetry not to create a virtual environment inside the container
RUN poetry config virtualenvs.create false

# Copy only the dependency files to leverage Docker's cache
COPY poetry.lock pyproject.toml ./

# Install dependencies, excluding development ones
RUN poetry install --no-dev --no-interaction --no-ansi


# Stage 2: The "final" runtime stage
FROM python:3.11-slim as final

# Create a non-root user for better security
RUN adduser --system --group appuser

WORKDIR /app

# Copy the installed Python packages from the builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

# Copy your application source code
COPY --chown=appuser:appuser . .

# Switch to the non-root user
USER appuser

# Command to run the FastAPI application
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]