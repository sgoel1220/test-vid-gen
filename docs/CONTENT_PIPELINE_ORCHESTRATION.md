# Content Pipeline Workflow Orchestration

> **Project Codename:** creepy-brain
> **Status:** Planning
> **Created:** 2026-04-16
> **Last Updated:** 2026-04-16

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Problem Statement](#problem-statement)
3. [Goals & Non-Goals](#goals--non-goals)
4. [Architecture Overview](#architecture-overview)
5. [Technology Decisions](#technology-decisions)
6. [Implementation Strategy](#implementation-strategy)
7. [Project Structure](#project-structure)
8. [Database Schema](#database-schema)
9. [API Specification](#api-specification)
10. [Phase Breakdown](#phase-breakdown)
11. [Testing Strategy](#testing-strategy)
12. [Deployment](#deployment)
13. [Risk Mitigation](#risk-mitigation)

---

## Executive Summary

### What

Build an end-to-end workflow orchestration system that chains:
**Story Generation → TTS Synthesis → Image Generation → Final Stitching**

### Why

Currently, three services (tts-server, metadata-server, story-engine) operate independently with no cross-service orchestration. This creates:
- Manual intervention required between steps
- No automatic retry/resume on failures
- GPU cost leaks (pods not terminated on failures)
- No unified visibility into pipeline status

### How

Consolidate into a single **creepy-brain** service that:
- Uses **Hatchet** for workflow orchestration (self-hosted, Postgres-backed)
- Abstracts GPU providers (RunPod now, swappable later)
- Provides a web UI for monitoring and control
- Ensures zero cost leaks through automatic cleanup

---

## Problem Statement

### Current State

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  story-engine   │     │ metadata-server │     │   tts-server    │
│  (LLM stories)  │     │  (run storage)  │     │  (GPU - TTS)    │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         │   NO ORCHESTRATION    │   NO ORCHESTRATION    │
         │                       │                       │
         ▼                       ▼                       ▼
    Manual Steps            Manual Steps            Manual Steps
```

### Pain Points

1. **No Automation**: Each step requires manual triggering
2. **No Resilience**: Failures require manual restart from beginning
3. **Cost Leaks**: GPU pods may run indefinitely if errors occur
4. **No Visibility**: No unified dashboard for pipeline status
5. **Complex Deployment**: Three separate services to manage

### Desired State

```
┌─────────────────────────────────────────────────────────────────┐
│                   creepy-brain (CONSOLIDATED)                   │
│  - Hatchet worker (workflow execution)                          │
│  - Story generation (LLM calls - local, no HTTP)                │
│  - Metadata storage (Postgres + SQLAlchemy)                     │
│  - GPU provider abstraction (RunPod/Local/Modal)                │
│  - Audio blob storage                                           │
│  - Web UI dashboard                                             │
├─────────────────────────────────────────────────────────────────┤
│                   Hatchet Engine (self-hosted)                  │
│  - Workflow orchestration + dashboard UI                        │
│  - Runs alongside creepy-brain (same Docker Compose)            │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP (only for GPU work)
                   ┌─────────▼─────────┐
                   │    tts-server     │
                   │  (RunPod GPU pod) │
                   │  TTS + Image gen  │
                   └───────────────────┘
```

---

## Goals & Non-Goals

### Goals

| Goal | Rationale |
|------|-----------|
| **Single service consolidation** | Reduce deployment complexity, share DB |
| **Automatic retry/resume** | Recover from transient failures without manual intervention |
| **GPU cost protection** | Always terminate pods, timeout long-running tasks |
| **Web UI dashboard** | Monitor pipelines, costs, and artifacts |
| **Provider abstraction** | Swap RunPod → Modal → custom without code changes |
| **Self-hosted** | Full control, no external workflow SaaS |

### Non-Goals

| Non-Goal | Reason |
|----------|--------|
| Multi-tenant support | Single-user system for now |
| Real-time streaming | Polling is sufficient for this use case |
| Mobile app | Web UI is sufficient |
| Horizontal scaling | Single worker is enough for current volume |

---

## Architecture Overview

### Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Docker Compose                                  │
│                                                                             │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐  │
│  │      Postgres       │  │   Hatchet Engine    │  │    creepy-brain     │  │
│  │                     │  │                     │  │                     │  │
│  │  - Workflow state   │◀─│  - DAG scheduling   │◀─│  - FastAPI app      │  │
│  │  - Stories         │  │  - Retry logic      │  │  - Hatchet worker   │  │
│  │  - Runs/Blobs      │  │  - Dashboard UI     │  │  - Story pipeline   │  │
│  │  - GPU pod tracking│  │                     │  │  - GPU abstraction  │  │
│  └─────────────────────┘  └─────────────────────┘  └──────────┬──────────┘  │
│                                                               │             │
└───────────────────────────────────────────────────────────────┼─────────────┘
                                                                │
                                                    HTTP (polling)
                                                                │
                                              ┌─────────────────▼─────────────┐
                                              │         tts-server            │
                                              │        (RunPod GPU)           │
                                              │                               │
                                              │  - Chatterbox TTS model       │
                                              │  - SDXL image generation      │
                                              │  - Async job API              │
                                              └───────────────────────────────┘
```

### Data Flow

```
1. User triggers workflow via API/UI
                    │
                    ▼
2. Hatchet schedules ContentPipeline workflow
                    │
                    ▼
3. Step 1: generate_story (LOCAL)
   - Call LLM directly (no HTTP hop)
   - Save story to Postgres
   - Output: story_id, full_text
                    │
                    ▼
4. Step 2: tts_synthesis (GPU POD)
   - Spin up RunPod (idempotent)
   - Wait for pod ready
   - POST /tts, poll until complete
   - Save audio blobs
   - Output: run_id, chunk audio
                    │
                    ▼
5. Step 3: image_generation (SAME GPU POD)
   - Model swap on same pod
   - Generate images per chunk
   - Save image blobs
   - Output: image_blob_ids
                    │
                    ▼
6. Step 4: stitch_final (LOCAL)
   - Combine audio + images (ffmpeg)
   - Save final artifact
   - Terminate GPU pod
   - Output: final_video_url
                    │
                    ▼
7. Workflow complete, artifacts available in UI
```

---

## Technology Decisions

### Workflow Engine: Hatchet

| Criteria | Hatchet | Temporal | Custom |
|----------|---------|----------|--------|
| Python SDK | ✅ Native | ✅ Native | N/A |
| Self-hosted | ✅ Postgres only | ⚠️ Complex (3+ containers) | ✅ |
| DAG workflows | ✅ Built-in | ✅ Built-in | 🔨 Build yourself |
| Dashboard UI | ✅ Built-in | ✅ Built-in | 🔨 Build yourself |
| Retry/timeout | ✅ Declarative | ✅ Declarative | 🔨 Build yourself |
| Learning curve | Low | High | Low |
| License | MIT | Apache 2.0 | N/A |

**Decision**: Hatchet - simplest self-hosted option with all needed features.

### ORM: SQLAlchemy

**Why not Prisma?** Prisma 7 is JavaScript/TypeScript only - no Python support.

SQLAlchemy is already used in metadata-server and is the standard Python ORM.

### GPU Communication: Polling

**Why not webhooks?**

| Aspect | Polling | Webhooks |
|--------|---------|----------|
| Network complexity | Low | High (GPU needs to reach orchestrator) |
| Debugging | Easy (hit endpoint manually) | Hard (need to trace callbacks) |
| NAT/firewall | Works | Requires inbound connectivity |
| Implementation | Simple loop | Callback handlers, state machine |

**Decision**: Polling - simplest approach that works reliably.

### Blob Storage: Postgres

**Why not S3/MinIO?**

- Single database, no extra infra
- Transactional consistency with workflow state
- Simple backup/restore
- Fine for current scale (< 100 workflows/day)

**Decision**: Postgres BYTEA columns - revisit if scale requires.

---

## Implementation Strategy

### Key Principle: New Folder, Independent Code

**IMPORTANT**: This implementation will be in a **new folder** (`services/creepy-brain/`) that is **independent of current code**.

```
Chatterbox-TTS-Server/
├── services/
│   ├── tts-server/           # UNCHANGED - stays on GPU
│   ├── metadata-server/      # DEPRECATED after migration
│   ├── story-engine/         # DEPRECATED after migration
│   └── creepy-brain/         # NEW - consolidated service
```

### Migration Approach

1. **Copy, don't move**: Code is copied from existing services, not moved
2. **Parallel operation**: Both old and new can run simultaneously during transition
3. **Feature parity first**: Match existing functionality before adding new features
4. **Incremental validation**: Each phase has verification steps

### Why Independent Folder?

- **Safety**: Original code remains unchanged until new system is proven
- **Rollback**: Can revert to old services instantly
- **Testing**: Can run both systems in parallel for comparison
- **Clean slate**: No legacy baggage, clean architecture from start

---

## Project Structure

```
services/creepy-brain/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI factory, lifespan
│   ├── config.py                  # Pydantic settings (all config)
│   ├── db.py                      # SQLAlchemy engine, session
│   │
│   ├── models/                    # SQLAlchemy ORM models
│   │   ├── __init__.py
│   │   ├── base.py                # Base model, mixins
│   │   ├── workflow.py            # Workflow, WorkflowStep, WorkflowChunk
│   │   ├── story.py               # Story, StoryAct
│   │   ├── run.py                 # Run, Chunk (from metadata-server)
│   │   ├── voice.py               # Voice
│   │   └── gpu_pod.py             # GpuPod
│   │
│   ├── gpu/                       # GPU provider abstraction
│   │   ├── __init__.py
│   │   ├── base.py                # Abstract GpuProvider
│   │   ├── runpod.py              # RunPod GraphQL implementation
│   │   ├── local.py               # Dev provider (localhost:8005)
│   │   └── schemas.py             # GpuPodSpec, GpuPod dataclasses
│   │
│   ├── llm/                       # LLM client (from story-engine)
│   │   ├── __init__.py
│   │   ├── client.py              # OpenAI/Anthropic client
│   │   └── prompts.py             # Story generation prompts
│   │
│   ├── pipeline/                  # Story generation pipeline
│   │   ├── __init__.py
│   │   ├── orchestrator.py        # Main pipeline orchestrator
│   │   ├── architect.py           # Story outline generation
│   │   ├── writer.py              # Act writing
│   │   └── reviewer.py            # Quality review
│   │
│   ├── workflows/                 # Hatchet workflows
│   │   ├── __init__.py            # Hatchet client setup
│   │   ├── content_pipeline.py    # @hatchet.workflow ContentPipeline
│   │   ├── recon.py               # Pod cleanup cron job
│   │   └── steps/
│   │       ├── __init__.py
│   │       ├── story.py           # generate_story step
│   │       ├── tts.py             # tts_synthesis step
│   │       ├── image.py           # image_generation step
│   │       └── stitch.py          # stitch_final step
│   │
│   ├── services/                  # Business logic layer
│   │   ├── __init__.py
│   │   ├── workflow_service.py    # Workflow CRUD + trigger
│   │   ├── story_service.py       # Story CRUD
│   │   ├── run_service.py         # Run/Chunk CRUD
│   │   └── blob_service.py        # Blob storage
│   │
│   ├── routes/                    # FastAPI routes
│   │   ├── __init__.py
│   │   ├── workflows.py           # /api/workflows/*
│   │   ├── stories.py             # /api/stories/*
│   │   ├── runs.py                # /api/runs/*
│   │   ├── gpu_pods.py            # /api/gpu-pods/*
│   │   └── health.py              # /health, /metrics
│   │
│   └── schemas/                   # Pydantic request/response models
│       ├── __init__.py
│       ├── workflow.py
│       ├── story.py
│       └── common.py
│
├── web/                           # Next.js dashboard
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx
│   │   │   ├── page.tsx           # Dashboard home
│   │   │   ├── workflows/
│   │   │   │   ├── page.tsx       # Workflow list
│   │   │   │   └── [id]/page.tsx  # Workflow detail
│   │   │   ├── gpu-pods/
│   │   │   │   └── page.tsx       # GPU pod monitoring
│   │   │   └── settings/
│   │   │       └── page.tsx       # Configuration
│   │   ├── components/
│   │   │   ├── ui/                # shadcn components
│   │   │   ├── workflow-card.tsx
│   │   │   ├── workflow-detail.tsx
│   │   │   ├── step-progress.tsx
│   │   │   └── pod-status.tsx
│   │   └── lib/
│   │       ├── api.ts             # API client
│   │       └── hooks.ts           # SWR hooks
│   ├── package.json
│   ├── tailwind.config.ts
│   └── next.config.ts
│
├── alembic/                       # Database migrations
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial_schema.py
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                # Fixtures
│   ├── unit/
│   │   ├── test_gpu_provider.py
│   │   ├── test_workflow_service.py
│   │   └── test_story_pipeline.py
│   └── integration/
│       ├── test_content_pipeline.py
│       └── test_api.py
│
├── docker-compose.yml             # brain + hatchet + postgres
├── docker-compose.dev.yml         # Dev overrides
├── Dockerfile
├── pyproject.toml
├── README.md
└── .env.example
```

---

## Database Schema

### Entity Relationship Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Database Schema                                 │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────┐       ┌─────────────────┐       ┌─────────────────┐
│  workflows  │──────<│ workflow_steps  │       │   gpu_pods      │
├─────────────┤  1:N  ├─────────────────┤       ├─────────────────┤
│ id (PK)     │       │ id (PK)         │       │ id (PK)         │
│ type        │       │ workflow_id(FK) │──────>│ workflow_id(FK) │
│ status      │       │ step_name       │       │ provider        │
│ input_json  │       │ status          │       │ endpoint_url    │
│ result_json │       │ input_json      │       │ status          │
│ error       │       │ output_json     │       │ cost_cents      │
│ created_at  │       │ error           │       │ created_at      │
│ completed_at│       │ gpu_pod_id      │       │ terminated_at   │
└─────────────┘       │ started_at      │       └─────────────────┘
      │               │ completed_at    │
      │               └─────────────────┘
      │
      │         ┌───────────────────┐       ┌─────────────────┐
      └────────<│ workflow_chunks   │──────>│ workflow_blobs  │
          1:N   ├───────────────────┤  N:1  ├─────────────────┤
                │ id (PK)           │       │ id (PK)         │
                │ workflow_id (FK)  │       │ workflow_id(FK) │
                │ chunk_index       │       │ blob_type       │
                │ chunk_text        │       │ data (BYTEA)    │
                │ tts_audio_blob_id │       │ mime_type       │
                │ tts_duration_sec  │       │ created_at      │
                │ image_prompt      │       └─────────────────┘
                │ image_blob_id     │
                └───────────────────┘

┌─────────────┐       ┌─────────────┐
│   stories   │──────<│ story_acts  │
├─────────────┤  1:N  ├─────────────┤
│ id (PK)     │       │ id (PK)     │
│ title       │       │ story_id(FK)│
│ premise     │       │ act_number  │
│ outline     │       │ content     │
│ full_text   │       │ word_count  │
│ status      │       │ created_at  │
│ created_at  │       └─────────────┘
└─────────────┘

┌─────────────┐       ┌─────────────┐
│   voices    │       │    runs     │
├─────────────┤       ├─────────────┤
│ id (PK)     │       │ id (PK)     │
│ name        │       │ story_id(FK)│
│ description │       │ voice_id(FK)│
│ audio_path  │       │ status      │
│ is_default  │       │ ...         │
└─────────────┘       └─────────────┘
```

### SQL Schema

```sql
-- Workflow orchestration
CREATE TABLE workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_type VARCHAR(50) NOT NULL,      -- 'content_pipeline'
    input_json JSONB NOT NULL,               -- {premise, voice_id, ...}
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
        -- pending | running | completed | failed | cancelled
    current_step VARCHAR(50),                -- Current step name
    result_json JSONB,                       -- Final output
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_workflows_status ON workflows(status);
CREATE INDEX idx_workflows_created ON workflows(created_at DESC);

-- Step execution tracking
CREATE TABLE workflow_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    step_name VARCHAR(50) NOT NULL,          -- generate_story | tts | image | stitch
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    input_json JSONB,
    output_json JSONB,                       -- Step result
    error TEXT,
    gpu_pod_id VARCHAR(100),                 -- Pod used for this step
    attempt_number INT NOT NULL DEFAULT 1,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    UNIQUE(workflow_id, step_name, attempt_number)
);

CREATE INDEX idx_workflow_steps_workflow ON workflow_steps(workflow_id);

-- Chunk-level progress
CREATE TABLE workflow_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    chunk_text TEXT NOT NULL,
    -- TTS
    tts_status VARCHAR(20) DEFAULT 'pending',
    tts_audio_blob_id UUID,
    tts_duration_sec DECIMAL(10, 4),
    tts_completed_at TIMESTAMPTZ,
    -- Image
    image_status VARCHAR(20) DEFAULT 'pending',
    image_prompt TEXT,
    image_blob_id UUID,
    image_completed_at TIMESTAMPTZ,
    UNIQUE(workflow_id, chunk_index)
);

CREATE INDEX idx_workflow_chunks_workflow ON workflow_chunks(workflow_id);

-- Binary blob storage
CREATE TABLE workflow_blobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID REFERENCES workflows(id) ON DELETE SET NULL,
    blob_type VARCHAR(20) NOT NULL,          -- chunk_audio | final_audio | image
    data BYTEA NOT NULL,
    mime_type VARCHAR(50) NOT NULL,
    size_bytes INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_workflow_blobs_workflow ON workflow_blobs(workflow_id);

-- GPU pod tracking (cost monitoring)
CREATE TABLE gpu_pods (
    id VARCHAR(100) PRIMARY KEY,             -- Provider's pod ID
    provider VARCHAR(20) NOT NULL,           -- runpod | local | modal
    workflow_id UUID REFERENCES workflows(id) ON DELETE SET NULL,
    endpoint_url VARCHAR(500),
    status VARCHAR(20) NOT NULL,             -- creating | running | ready | terminated | error
    gpu_type VARCHAR(50),                    -- RTX 4090, A100, etc.
    cost_per_hour_cents INT,
    total_cost_cents INT DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ready_at TIMESTAMPTZ,
    terminated_at TIMESTAMPTZ,
    termination_reason VARCHAR(100)
);

CREATE INDEX idx_gpu_pods_status ON gpu_pods(status);
CREATE INDEX idx_gpu_pods_workflow ON gpu_pods(workflow_id);

-- Stories (from story-engine)
CREATE TABLE stories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID REFERENCES workflows(id) ON DELETE SET NULL,
    title VARCHAR(500),
    premise TEXT NOT NULL,
    outline JSONB,                           -- Structured outline
    full_text TEXT,
    word_count INT,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    llm_model VARCHAR(100),
    total_tokens_used INT DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE story_acts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    story_id UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    act_number INT NOT NULL,
    title VARCHAR(200),
    content TEXT NOT NULL,
    word_count INT,
    revision_count INT DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(story_id, act_number)
);

-- Voices
CREATE TABLE voices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    audio_path VARCHAR(500) NOT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- TTS runs (simplified from metadata-server)
CREATE TABLE runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID REFERENCES workflows(id) ON DELETE SET NULL,
    story_id UUID REFERENCES stories(id) ON DELETE SET NULL,
    voice_id UUID REFERENCES voices(id) ON DELETE SET NULL,
    input_text TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    final_audio_blob_id UUID,
    total_duration_sec DECIMAL(10, 4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE run_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    chunk_text TEXT NOT NULL,
    audio_blob_id UUID,
    duration_sec DECIMAL(10, 4),
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(run_id, chunk_index)
);
```

---

## API Specification

### Workflows API

```yaml
# Start a content pipeline workflow
POST /api/workflows
Request:
  {
    "workflow_type": "content_pipeline",
    "input": {
      "premise": "A family moves into a house with a dark history...",
      "voice_name": "Gianna",
      "generate_images": true,
      "stitch_video": true
    }
  }
Response: 201 Created
  {
    "id": "uuid",
    "status": "pending",
    "created_at": "2026-04-16T10:00:00Z"
  }

# List workflows
GET /api/workflows?status=running&limit=20
Response: 200 OK
  {
    "items": [
      {
        "id": "uuid",
        "workflow_type": "content_pipeline",
        "status": "running",
        "current_step": "tts_synthesis",
        "progress": {"completed_steps": 1, "total_steps": 4},
        "created_at": "2026-04-16T10:00:00Z"
      }
    ],
    "total": 1
  }

# Get workflow detail
GET /api/workflows/{id}
Response: 200 OK
  {
    "id": "uuid",
    "workflow_type": "content_pipeline",
    "status": "running",
    "current_step": "tts_synthesis",
    "input": {...},
    "steps": [
      {
        "name": "generate_story",
        "status": "completed",
        "output": {"story_id": "uuid"},
        "duration_sec": 120
      },
      {
        "name": "tts_synthesis",
        "status": "running",
        "gpu_pod_id": "pod-abc123"
      },
      ...
    ],
    "chunks": [
      {
        "index": 0,
        "text": "It was a dark...",
        "tts_status": "completed",
        "tts_duration_sec": 5.2,
        "image_status": "pending"
      },
      ...
    ],
    "gpu_pod": {
      "id": "pod-abc123",
      "status": "running",
      "cost_cents": 120
    }
  }

# Retry workflow from failed step
POST /api/workflows/{id}/retry
Response: 200 OK

# Cancel workflow
DELETE /api/workflows/{id}
Response: 204 No Content
```

### GPU Pods API

```yaml
# List active pods
GET /api/gpu-pods?status=running
Response: 200 OK
  {
    "items": [
      {
        "id": "pod-abc123",
        "provider": "runpod",
        "workflow_id": "uuid",
        "status": "running",
        "endpoint_url": "https://...",
        "cost_cents": 120,
        "created_at": "2026-04-16T10:05:00Z"
      }
    ],
    "total_cost_cents": 120
  }

# Manually terminate pod
DELETE /api/gpu-pods/{id}
Response: 204 No Content
```

### Stories API

```yaml
# Generate story (direct, for testing)
POST /api/stories/generate
Request:
  {
    "premise": "A family moves into...",
    "max_revisions": 3,
    "target_word_count": 5000
  }
Response: 202 Accepted
  {
    "story_id": "uuid",
    "status": "generating"
  }

# Get story
GET /api/stories/{id}
Response: 200 OK
  {
    "id": "uuid",
    "title": "The House on Maple Street",
    "premise": "...",
    "status": "completed",
    "acts": [
      {"act_number": 1, "title": "The Arrival", "word_count": 1200},
      ...
    ],
    "total_word_count": 5000
  }
```

---

## Phase Breakdown

### Phase 1: Service Scaffold & Migration (Week 1)

**Goal**: Create creepy-brain service with existing functionality from metadata-server and story-engine.

**Stories**:
1. Create service scaffold (folder structure, pyproject.toml, Dockerfile)
2. Set up SQLAlchemy models and Alembic migrations
3. Migrate metadata-server code (db, models, services, routes)
4. Migrate story-engine code (llm, pipeline)
5. Create Docker Compose with Postgres
6. Verify feature parity with existing services

**Verification**:
- [ ] `docker-compose up` starts creepy-brain + postgres
- [ ] `/health` returns 200
- [ ] Story generation works via API
- [ ] Run/chunk CRUD works via API

### Phase 2: Hatchet Integration & GPU Provider (Week 2)

**Goal**: Add workflow orchestration with Hatchet and GPU provider abstraction.

**Stories**:
1. Add Hatchet engine to Docker Compose
2. Install hatchet-sdk, configure client
3. Create GPU provider abstraction (base class)
4. Implement RunPod provider (GraphQL API)
5. Implement Local provider (dev mode)
6. Create basic workflow registration

**Verification**:
- [ ] Hatchet dashboard accessible at localhost:8080
- [ ] RunPod provider can list/create/terminate pods
- [ ] Local provider returns localhost:8005
- [ ] Simple test workflow runs successfully

### Phase 3: Content Pipeline Workflow (Week 3)

**Goal**: Implement the full content pipeline with all steps.

**Stories**:
1. Create ContentPipeline workflow definition
2. Implement generate_story step
3. Implement tts_synthesis step with polling
4. Implement image_generation step
5. Implement stitch_final step
6. Add on_failure cleanup hook
7. Create recon cron job for orphaned pods
8. Add workflow API endpoints

**Verification**:
- [ ] Full pipeline runs end-to-end with Local provider
- [ ] Chunk progress updates during TTS
- [ ] Pod terminated on failure
- [ ] Recon job cleans orphaned pods

### Phase 4: Web UI Dashboard (Week 4)

**Goal**: Build user-facing dashboard for monitoring and control.

**Stories**:
1. Set up Next.js + shadcn scaffold
2. Create dashboard home page (workflow list)
3. Create workflow detail page (steps, chunks, logs)
4. Create GPU pods page (cost tracking)
5. Add SWR polling for live updates
6. Create settings page

**Verification**:
- [ ] Dashboard shows workflow list with status
- [ ] Detail page shows step progress
- [ ] Pod costs displayed correctly
- [ ] Manual workflow trigger works from UI

### Phase 5: Production Hardening (Week 5)

**Goal**: Make the system production-ready.

**Stories**:
1. Add structured logging (structlog)
2. Add Prometheus metrics endpoint
3. Configure Slack/Discord alerts
4. Add workflow-level timeout
5. Implement cost tracking and alerts
6. Write comprehensive tests
7. Documentation and runbooks

**Verification**:
- [ ] Logs are JSON formatted
- [ ] /metrics returns Prometheus format
- [ ] Alert fires on workflow failure
- [ ] Timeout fires on long-running workflow
- [ ] All tests pass

---

## Testing Strategy

### Unit Tests

```python
# tests/unit/test_gpu_provider.py
def test_runpod_provider_create_pod():
    """Test RunPod pod creation with mock GraphQL"""

def test_local_provider_returns_localhost():
    """Test local provider returns localhost:8005"""

# tests/unit/test_workflow_service.py
def test_create_workflow():
    """Test workflow creation"""

def test_workflow_status_transitions():
    """Test valid status transitions"""
```

### Integration Tests

```python
# tests/integration/test_content_pipeline.py
def test_full_pipeline_with_local_provider():
    """Run full pipeline with local GPU provider"""

def test_pipeline_retry_on_failure():
    """Test retry from failed step"""

def test_pod_cleanup_on_failure():
    """Test pod is terminated when step fails"""
```

### Manual Verification

1. **Local dev**: `docker-compose up`, run workflow with LocalProvider
2. **RunPod test**: Deploy to server, test with real RunPod
3. **Resume test**: Kill worker mid-workflow, verify resume
4. **Failure test**: Force error, verify pod cleanup
5. **Recon test**: Create orphan pod, verify cleanup

---

## Deployment

### Development

```bash
cd services/creepy-brain
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up
```

### Production

```bash
# Build
docker buildx build --platform linux/amd64 \
  -f services/creepy-brain/Dockerfile \
  -t shubh67678/creepy-brain:latest \
  --push .

# Deploy (on server)
docker-compose -f docker-compose.yml up -d
```

### Environment Variables

```bash
# .env.example
DATABASE_URL=postgresql://user:pass@localhost:5432/creepy_brain
HATCHET_CLIENT_TOKEN=...
RUNPOD_API_KEY=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
SLACK_WEBHOOK_URL=...
```

---

## Risk Mitigation

### Cost Leak Prevention

| Risk | Mitigation |
|------|------------|
| Pod not terminated on failure | `on_failure` Hatchet hook |
| Orphaned pods from crashes | Recon cron job every 5 min |
| Long-running workflows | Workflow-level 1h timeout |
| Expensive story generation | Token budget cap (500k) |

### Reliability

| Risk | Mitigation |
|------|------------|
| Hatchet engine down | Worker auto-reconnects |
| Postgres down | Connection retry with backoff |
| RunPod API errors | Retry with exponential backoff |
| Network timeouts | Configurable timeouts per step |

### Security

| Risk | Mitigation |
|------|------------|
| API keys exposed | Environment variables only |
| Unauthorized access | API authentication |
| SQL injection | SQLAlchemy ORM |
| Blob access | UUID-based, no directory traversal |

---

## References

- [Hatchet Documentation](https://docs.hatchet.run/v1)
- [Hatchet Python SDK](https://pypi.org/project/hatchet-sdk/)
- [RunPod GraphQL API](https://docs.runpod.io/reference/graphql-api)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)
- [shadcn/ui Components](https://ui.shadcn.com/)
