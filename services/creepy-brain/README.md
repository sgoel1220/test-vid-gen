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
# 1. Create .env from example and fill in your LLM provider credentials
cp .env.example .env
# Anthropic: set LLM_PROVIDER=anthropic, ANTHROPIC_API_KEY, and LLM_MODEL=claude-opus-4-6
# OpenRouter: set LLM_PROVIDER=openrouter, OPENROUTER_API_KEY, and LLM_MODEL=anthropic/claude-opus-4-6

# 2. Start the service and postgres
docker-compose up

# The service will be available at http://localhost:8006
```

> Story generation requires credentials for the configured `LLM_PROVIDER` in `.env`.
> Without them the generate endpoint returns 202 but background generation will fail
> with status `failed`.

### Local Development

```bash
# Install dependencies
pip install -e .

# Copy environment file and set your API key
cp .env.example .env
# Edit .env: set LLM_PROVIDER plus ANTHROPIC_API_KEY or OPENROUTER_API_KEY
# .env.example uses POSTGRES_PORT=5433 to match the docker-compose host mapping

# Start postgres via docker-compose (listens on host port 5433)
docker-compose up postgres

# Run the app
uvicorn app.main:app --host 0.0.0.0 --port 8006 --reload
```

## API Endpoints

### Infrastructure
- `GET /` - Service info
- `GET /health` - Health check (`{"status": "ok"}`)
- `GET /metrics` - Prometheus metrics

### Voices
- `POST /api/voices` - Upload reference audio (multipart form)
- `GET /api/voices` - List all voices

### Runs
- `POST /api/runs` - Create a TTS run record
- `GET /api/runs` - List runs (supports `?limit=&offset=`)
- `GET /api/runs/{run_id}` - Get run by ID
- `PATCH /api/runs/{run_id}` - Update run status/result

### Blobs
- `GET /api/blobs/{blob_id}` - Download audio blob

### Stories
- `POST /api/stories/generate` - Start background story generation (returns 202)
- `GET /api/stories/{story_id}` - Poll story status and acts
- `GET /api/stories` - List stories (supports `?limit=&offset=`)

## Environment Variables

See `.env.example` for all available configuration options.

Key settings:
- `PORT` - Service port (default: 8006)
- `POSTGRES_HOST` - PostgreSQL host
- `POSTGRES_PORT` - PostgreSQL port
- `POSTGRES_DB` - Database name
- `POSTGRES_USER` - Database user
- `POSTGRES_PASSWORD` - Database password
- `LLM_PROVIDER` - `anthropic` or `openrouter`
- `ANTHROPIC_API_KEY` - Anthropic API key for story generation
- `OPENROUTER_API_KEY` - OpenRouter API key for story generation
- `LLM_MODEL` - Provider model name, e.g. `claude-opus-4-6` or `anthropic/claude-opus-4-6`
- `JSON_LOGS` - `true` for JSON logs (production), `false` for pretty logs (dev)

## Development Roadmap

### Phase 1: Foundation (✓ Complete)
- FastAPI service scaffold with PostgreSQL + SQLAlchemy async
- SQLAlchemy models and Alembic migrations (Voice, Run, Blob, Story, StoryAct, Workflow)
- Full metadata-server parity: voices, runs, blobs CRUD
- Full story-engine parity: generate, poll, list stories
- Structured logging (structlog), Prometheus metrics
- Docker Compose setup (service + postgres)

### Phase 2: Hatchet Integration (Next)
- Add Hatchet engine to Docker Compose
- GPU provider abstraction (RunPod + local dev)
- ContentPipeline workflow definition
- Step implementations: generate_story, tts_synthesis, image_generation, stitch_final
- Workflow API endpoints

### Phase 3: Hardening
- Recon cron job for orphaned GPU pods
- On-failure cleanup hooks
- Cost tracking and alerts
- Workflow-level timeouts
- Comprehensive tests

## Contributing

This service is part of the Chatterbox TTS ecosystem. See the main repository documentation for contribution guidelines.

## License

See LICENSE file in the root repository.
