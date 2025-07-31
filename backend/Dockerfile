# Use an official, slim Python image as the base
FROM python:3.12-slim

# Set the working directory inside the container
WORKDIR /app

# Set environment variables to prevent Poetry from creating a virtual env inside the container
# and to prevent it from asking interactive questions.
ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_CACHE_DIR='/var/cache/pypoetry'

# Install Poetry
RUN pip install poetry

# Copy only the dependency definition files to leverage Docker's layer caching.
# This layer only gets rebuilt if your dependencies change.
COPY pyproject.toml poetry.lock ./

# Install project dependencies
RUN poetry install --no-dev --no-root

# Copy the rest of your application's source code
COPY . .

# Command to run the Uvicorn server when the container starts
# It will be accessible on port 8000
CMD ["poetry", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]