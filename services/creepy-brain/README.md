# Creepy Brain

Content Pipeline Workflow Orchestration Service for the Chatterbox ecosystem.

## Overview

Creepy Brain is the orchestration layer that coordinates the end-to-end content pipeline:
- Story Generation (story-engine)
- TTS Synthesis (tts-server)
- Image Generation (SDXL)
- Final Stitching

This service uses Hatchet for workflow orchestration and provides a unified API and dashboard for managing content creation workflows.

## Architecture

- **Framework**: FastAPI
- **Database**: PostgreSQL (via SQLAlchemy async)
- **Workflow Engine**: Hatchet (to be integrated)
- **Port**: 8006

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.11+ (for local development)

### Running with Docker

```bash
# Start the service and postgres
docker-compose up

# The service will be available at http://localhost:8006
```

### Local Development

```bash
# Install dependencies
pip install -e .

# Copy environment file
cp .env.example .env

# Start postgres separately or use docker-compose for just postgres
docker-compose up postgres

# Run the app
uvicorn app.main:app --host 0.0.0.0 --port 8006 --reload
```

## API Endpoints

- `GET /` - Service info
- `GET /health` - Health check endpoint (returns `{"status": "ok"}`)

## Environment Variables

See `.env.example` for all available configuration options.

Key settings:
- `PORT` - Service port (default: 8006)
- `POSTGRES_HOST` - PostgreSQL host
- `POSTGRES_PORT` - PostgreSQL port
- `POSTGRES_DB` - Database name
- `POSTGRES_USER` - Database user
- `POSTGRES_PASSWORD` - Database password

## Development Roadmap

### Phase 1: Scaffold (✓ Current)
- Basic FastAPI structure
- PostgreSQL setup
- Health endpoints

### Phase 2: Database Models
- SQLAlchemy models
- Alembic migrations
- Core entities (Workflow, Story, Run, etc.)

### Phase 3: Hatchet Integration
- Workflow definitions
- Step implementations
- GPU pod management

### Phase 4: API Layer
- Workflow CRUD endpoints
- Run execution endpoints
- Status and monitoring

### Phase 5: Observability
- Structured logging
- Prometheus metrics
- Slack/Discord alerts

## Contributing

This service is part of the Chatterbox TTS ecosystem. See the main repository documentation for contribution guidelines.

## License

See LICENSE file in the root repository.
