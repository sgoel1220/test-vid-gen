FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY services/creepy-brain/pyproject.toml ./

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Copy application code
COPY services/creepy-brain/app/ ./app/
COPY services/creepy-brain/alembic/ ./alembic/
COPY services/creepy-brain/alembic.ini ./
COPY services/creepy-brain/entrypoint.sh ./
COPY services/creepy-brain/static/ ./static/

RUN chmod +x entrypoint.sh

# Expose the application port
EXPOSE 8006

# RUN_MIGRATIONS=true (default): run alembic upgrade head before uvicorn.
# Set RUN_MIGRATIONS=false to skip (e.g. migrations run as a separate job in prod).
ENTRYPOINT ["./entrypoint.sh"]
