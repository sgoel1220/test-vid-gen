FROM python:3.11-slim AS base

WORKDIR /srv

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install protocol package first (rarely changes → good cache layer).
COPY creepy_pasta_protocol /srv/creepy_pasta_protocol
RUN pip install --no-cache-dir /srv/creepy_pasta_protocol

# Install app dependencies.
COPY services/metadata-server/pyproject.toml /srv/metadata-server/pyproject.toml
WORKDIR /srv/metadata-server
RUN pip install --no-cache-dir -e ".[dev]" || pip install --no-cache-dir -e .

# Copy app source.
COPY services/metadata-server/ /srv/metadata-server/

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
