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
COPY poetry.lock pyproject.toml README.md ./

# Install dependencies, excluding development ones
RUN poetry install --no-root --no-interaction --no-ansi -vvv


# Stage 2: The "final" runtime stage
FROM python:3.11-slim as final

# Create a non-root user for better security
RUN adduser --system --group appuser

WORKDIR /app

# Copy the installed Python packages from the builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

# Copy the executables installed by the dependencies (like uvicorn)
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy your application source code
COPY --chown=appuser:appuser . .

# Switch to the non-root user
USER appuser

# Command to run the FastAPI application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]