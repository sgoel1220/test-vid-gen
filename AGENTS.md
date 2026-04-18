# Agent Instructions

This is the canonical repo instruction file. `CLAUDE.md` is a symlink to this file.

## Response Style

- Keep responses short by default.
- Expand only when explicitly asked.

## Serana LSP Tools

**Use Serana MCP tools for all code navigation and refactoring tasks.** These provide IDE-level precision.

### When to use Serana

- **Finding definitions**: `mcp__serana__go_to_definition` — jump to where a symbol is defined
- **Finding references**: `mcp__serana__find_references` — find all usages of a symbol
- **Renaming symbols**: `mcp__serana__rename_symbol` — safely rename across the codebase
- **Code actions**: `mcp__serana__code_actions` — get available refactoring options
- **Hover info**: `mcp__serana__hover` — get type info and documentation
- **Diagnostics**: `mcp__serana__get_diagnostics` — get errors/warnings for a file

### Prefer Serana over grep/glob

```python
# ❌ BAD - manual grep for finding usages
Grep(pattern="validate_chunk_audio")

# ✅ GOOD - precise LSP references
mcp__serana__find_references(file_path="app/audio/validation.py", line=36, character=4)

# ❌ BAD - guessing where a function is defined
Grep(pattern="def generate_text")

# ✅ GOOD - jump to definition
mcp__serana__go_to_definition(file_path="app/text/normalization.py", line=62, character=20)

# ❌ BAD - manual find-and-replace for renaming
Edit(old_string="old_name", new_string="new_name", replace_all=True)

# ✅ GOOD - LSP-aware rename that handles imports, references, etc.
mcp__serana__rename_symbol(file_path="...", line=..., character=..., new_name="new_name")
```

### Critical rules

- **ALWAYS use Serana for refactoring** — it understands scope, imports, and type relationships
- **ALWAYS use Serana for finding references** — grep misses dynamic references and aliases
- **Use go_to_definition before editing** — understand the code before changing it

## Python Typing Standards

**All Python code in this repo MUST be statically typed.** This is a non-negotiable requirement.

### Type Annotation Requirements

1. **All functions MUST have type hints:**
   ```python
   def process_audio(data: bytes, sample_rate: int) -> AudioChunk:
       ...
   ```

2. **All function parameters MUST be typed:**
   - No bare `def foo(x):` — always `def foo(x: str):`
   - Use specific types, not `Any` unless absolutely necessary

3. **All return types MUST be annotated:**
   - Include `-> None` for functions that don't return a value
   - Use `-> ReturnType` for all other functions

4. **All variables with non-obvious types MUST be annotated:**
   ```python
   chunks: list[AudioChunk] = []
   metadata: dict[str, Any] = {}
   ```

### Pydantic Models

**CRITICAL RULE: NEVER use `dict` as a return type. ALWAYS create a Pydantic model.**

1. **Use Pydantic for ALL data structures:**
   - API request/response models
   - Configuration objects
   - Domain models
   - Internal data transfer objects
   - **Function return values with structured data**

2. **Never use raw dicts or tuples for structured data:**
   ```python
   # ❌ BAD - dict return type
   def create_job(data: dict) -> dict:
       return {"id": 123, "status": "pending"}

   # ❌ BAD - dict[str, str] is also prohibited
   async def get_status() -> dict[str, str]:
       return {"status": "ok"}

   # ✅ GOOD - Pydantic model
   class StatusResponse(BaseModel):
       status: str

   async def get_status() -> StatusResponse:
       return StatusResponse(status="ok")

   # ✅ GOOD - JobResponse model
   def create_job(data: JobRequest) -> JobResponse:
       return JobResponse(id=123, status=JobStatus.PENDING)
   ```

3. **Pydantic model requirements:**
   - Use `BaseModel` for all structured data
   - Use `Field()` for validation and documentation
   - Use enums for constrained string values
   - Use `ConfigDict` for model configuration
   - Create response models even for simple endpoints

### Type-Checking Enforcement

1. **Run mypy on all new/modified code:**
   ```bash
   python3 -m mypy path/to/module.py --strict
   ```

2. **mypy must pass with no errors before commit**

3. **Prefer `--strict` mode settings:**
   - `disallow_untyped_defs = True`
   - `disallow_any_unimported = True`
   - `warn_return_any = True`
   - `strict_optional = True`

### Import Standards

1. **Use modern type hint syntax:**
   ```python
   from typing import Protocol, TypeVar, Generic
   from collections.abc import Sequence, Mapping
   ```

2. **For Python 3.9+, prefer built-in generics:**
   ```python
   # ✅ GOOD (Python 3.9+)
   def process(items: list[str]) -> dict[str, int]:
       ...

   # ❌ BAD (old style)
   from typing import List, Dict
   def process(items: List[str]) -> Dict[str, int]:
       ...
   ```

### Validation Rules

1. **Never trust external input:**
   - Use Pydantic validation for all API inputs
   - Use type guards for runtime type checking when needed

2. **Make impossible states unrepresentable:**
   ```python
   # Use unions and enums instead of optional fields
   class JobComplete(BaseModel):
       status: Literal[JobStatus.COMPLETE]
       result: AudioResult

   class JobFailed(BaseModel):
       status: Literal[JobStatus.FAILED]
       error: str

   JobResult = JobComplete | JobFailed
   ```

### Critical Rules

- **NEVER merge code without type hints**
- **NEVER use `Any` without a comment explaining why**
- **NEVER use `# type: ignore` without a specific reason**
- **ALWAYS validate external input with Pydantic**
- **ALWAYS prefer Pydantic models over dicts/tuples**
- **ALWAYS run mypy before committing**

## Filing Beads During Implementation

**When working on a bead, you WILL discover issues that are out of scope for the current bead.** Do NOT ignore them or fix them inline — file them as beads immediately so they are tracked and can be prioritized.

### What to file

File a bead for **any** issue you discover, including:
- Out-of-scope bugs or gaps (different file area, different concern)
- Pre-existing errors surfaced by mypy, linters, or adversarial review
- Missing API contracts or integrations that future beads depend on
- Adversarial review findings that conflict with the current bead's intent
- Anything you noticed but cannot fix right now — no matter how small

### How to file

```python
mcp__beads__create(
    title="Short imperative title",
    issue_type="bug" | "task" | "chore",
    priority=1 | 2 | 3,   # 1=critical/blocker, 2=normal, 3=nice-to-have
    labels=["relevant", "tags"],
    description="""
    What the problem is, why it matters, and how to fix it.
    Include acceptance criteria if you know them.
    """,
)
```

### When to file

- **During implementation**: as soon as you discover the issue, before continuing
- **After adversarial review**: for any finding you cannot fix in the current bead
- **At session end**: sweep for anything you noticed but didn't act on

### Critical rule

**NEVER silently leave a known issue unfiled.** If you see a problem and don't fix it, it MUST become a bead. "I'll remember it" is not acceptable — the bead system is the memory.

## Bead Workflow

When implementing beads (work items tracked in the `.beads/` system), **ALWAYS** follow this workflow:

1. **Create worktree** - BEFORE any code changes, create an isolated worktree:
   ```bash
   # Use EnterWorktree tool to create isolated branch
   EnterWorktree(description="Implement bead XYZ")
   ```
2. **Pick a bead** - Choose a ready bead with "open" status (no blockers) using `mcp__beads__ready`
   - **ONLY pick beads with status "open"**
   - **NEVER pick beads with status "in_progress"** - another agent is working on them
3. **Review documentation** - Get high-level understanding BEFORE implementing:
   - Review the Architecture Overview section in this file
   - Review related documentation for the feature area
   - Understand the "why" behind the implementation
   - **ASK QUESTIONS** if anything is unclear - NEVER assume
4. **Implement** - Complete all required changes in the worktree
   - **Prioritize SIMPLICITY** - choose the simplest solution that works
   - Avoid over-engineering, premature abstractions, or unnecessary complexity
   - Write clear, straightforward code
5. **Test** - Thoroughly verify everything works
6. **Adversarial review** - MANDATORY: inside the worktree, run `/adversarial-review` in the **foreground** (never background):
   - Wait for the review to complete — do NOT proceed while it is still running
   - Implement every actionable finding in the worktree before moving on
   - If findings conflict with the bead's intent, stop and ASK — do not silently ignore them
   - Re-test after applying review fixes
7. **Commit** - Create a proper git commit with descriptive message (review fixes may be the same commit or a follow-up commit in the worktree)
8. **Merge** - Exit worktree and merge branch back to main:
   ```bash
   ExitWorktree(action="keep")
   git merge worktree-<name> --no-edit
   ```
   - If merge conflicts occur, resolve them carefully
   - Test again after resolving conflicts
   - Complete the merge before proceeding
9. **Mark done** - Close the bead with `mcp__beads__close` ONLY after successful merge
10. **Push** - Push changes to remote with `git push origin main`
11. **Clean up the worktree** - ONLY after `git push` succeeds. Run ALL commands from the **repo root** (not inside the worktree):
    ```bash
    git worktree remove .claude/worktrees/<name>   # remove worktree directory first
    git branch -d worktree-<name>                   # then delete local branch
    git worktree prune                              # clean any stale worktree entries
    git status                                      # verify clean state
    ```
    - Use `-d` (safe delete), not `-D`, so git refuses if the branch isn't merged
    - If the branch has an upstream on origin, also delete it: `git push origin --delete worktree-<name>`
    - **IMPORTANT**: You cannot delete a branch while your shell is inside that worktree — always `cd` to repo root first or use `git -C /path/to/repo`

**CRITICAL RULES:**
- NEVER pick a bead with status "in_progress" - another agent is working on it
- ONLY pick beads with status "open"
- NEVER make code changes directly on main - ALWAYS use a worktree
- NEVER start implementing without understanding the context - review docs first
- NEVER assume - ASK QUESTIONS if anything is unclear or ambiguous
- ALWAYS prioritize SIMPLICITY over cleverness or premature optimization
- NEVER skip `/adversarial-review` - it is a required gate before merging
- NEVER run `/adversarial-review` in the background - it MUST run in the foreground so its findings are applied before commit/merge
- NEVER merge before applying adversarial-review findings
- NEVER mark a bead as done before committing, merging, and pushing
- NEVER delete the worktree branch before `git push origin main` succeeds
- Work is NOT complete until `git push` succeeds AND the worktree branch is deleted
- Test thoroughly before committing
- If merge conflicts occur, resolve them before closing the bead
- Only close the bead after all changes are successfully pushed to remote

## Current Product Shape

This repo is a **monorepo** for the Creepy Pasta audio/video production pipeline. It generates horror stories via LLM, synthesizes narration via TTS, generates scene images via SDXL, and stitches everything into a final audio/video artifact.

### Services

- **tts-server** (`services/tts-server/`) — Stateless TTS GPU pod (Chatterbox model). Single endpoint: `POST /synthesize`
- **image-server** (`services/image-server/`) — Stateless image generation GPU pod (SDXL Lightning). Single endpoint: `POST /generate`
- **creepy-brain** (`services/creepy-brain/`) — Central orchestrator: story generation, text processing, workflow engine, GPU lifecycle, audio stitching, API server

## Project Structure

```
chatterbox-tts-lite/
├── services/
│   ├── tts-server/                  # Stateless TTS GPU pod
│   │   ├── minimal_server.py        # FastAPI: /synthesize, /health, /ready
│   │   ├── voices/                  # Reference voice .wav files
│   │   └── Dockerfile
│   │
│   ├── image-server/                # Stateless SDXL GPU pod
│   │   ├── server.py                # FastAPI: /generate, /health, /ready
│   │   └── Dockerfile
│   │
│   └── creepy-brain/                # Orchestration service
│       ├── app/
│       │   ├── main.py              # FastAPI factory + lifespan
│       │   ├── config.py            # Pydantic Settings
│       │   ├── db.py                # SQLAlchemy async engine + session
│       │   ├── engine/              # Custom workflow engine
│       │   │   ├── engine.py        # WorkflowEngine: trigger, retry, pause/resume, cancel
│       │   │   ├── runner.py        # WorkflowRunner: topo-sort steps, execute DAG
│       │   │   ├── scheduler.py     # CronScheduler: periodic workflows (recon)
│       │   │   ├── models.py        # StepDef, WorkflowDef, StepContext
│       │   │   └── db_helpers.py    # DB utility functions for engine
│       │   ├── workflows/           # Workflow definitions + step implementations
│       │   │   ├── content_pipeline.py  # Main pipeline: story → TTS → image → stitch → cleanup
│       │   │   ├── recon.py         # Orphaned GPU pod cleanup (cron)
│       │   │   ├── db_helpers.py    # DB utility functions for workflows
│       │   │   ├── test_workflow.py # Test/dev workflow
│       │   │   ├── steps/
│       │   │   │   ├── story.py     # LLM story generation step
│       │   │   │   ├── tts.py       # Per-chunk TTS synthesis + retry
│       │   │   │   ├── image.py     # Per-scene image generation
│       │   │   │   ├── stitch.py    # Audio concat + MP3 encode + video creation
│       │   │   │   └── cleanup.py   # GPU pod termination
│       │   │   ├── schemas.py       # Workflow input/output schemas
│       │   │   └── types.py         # Shared workflow types
│       │   ├── pipeline/            # LLM story generation pipeline
│       │   │   ├── orchestrator.py  # run_pipeline(): architect → writer → reviewer loop
│       │   │   ├── architect.py     # Story outline generation
│       │   │   ├── writer.py        # Act-by-act writing
│       │   │   ├── reviewer.py      # Quality review + scoring
│       │   │   ├── formatting.py    # Output formatting
│       │   │   └── models.py        # Pipeline data models
│       │   ├── llm/                 # LLM client abstraction
│       │   │   ├── client.py        # AnthropicProvider, OpenRouterProvider
│       │   │   ├── prompts.py       # Story generation prompts
│       │   │   └── image_prompts.py # Image prompt generation
│       │   ├── text/                # Text processing
│       │   │   ├── normalization.py # LLM-based text normalization for TTS
│       │   │   ├── chunking.py      # Sentence-based text chunking
│       │   │   └── scene_grouping.py # Group chunks into scenes for images
│       │   ├── audio/               # Audio processing
│       │   │   ├── validation.py    # RMS, peak, voiced-ratio checks (numpy)
│       │   │   └── encoding.py      # WAV → MP3 encoding (soundfile)
│       │   ├── gpu/                 # GPU provider abstraction
│       │   │   ├── base.py          # GpuProvider protocol, GpuPodSpec, GpuPod
│       │   │   ├── runpod.py        # RunPod implementation
│       │   │   └── lifecycle.py     # DB-tracked pod create/wait/terminate
│       │   ├── models/              # SQLAlchemy ORM models
│       │   │   ├── base.py          # Base model, mixins
│       │   │   ├── enums.py         # WorkflowStatus, StepStatus, ChunkStatus, etc.
│       │   │   ├── workflow.py      # Workflow, WorkflowStep, WorkflowChunk
│       │   │   ├── story.py         # Story, StoryAct
│       │   │   ├── run.py           # Run, RunChunk
│       │   │   ├── voice.py         # Voice
│       │   │   ├── gpu_pod.py       # GpuPod
│       │   │   ├── json_types.py    # JSON column type helpers
│       │   │   └── json_schemas.py  # JSON schema definitions
│       │   ├── schemas/             # Pydantic request/response models
│       │   │   ├── workflow.py      # Workflow API schemas
│       │   │   ├── story.py         # Story API schemas
│       │   │   ├── run.py           # Run API schemas
│       │   │   ├── voice.py         # Voice API schemas
│       │   │   ├── blob.py          # Blob API schemas
│       │   │   └── common.py        # Shared schemas
│       │   ├── routes/              # FastAPI route handlers
│       │   │   ├── workflows.py     # /api/workflows/*
│       │   │   ├── stories.py       # /api/stories/*
│       │   │   ├── runs.py          # /api/runs/*
│       │   │   ├── voices.py        # /api/voices/*
│       │   │   ├── blobs.py         # /api/blobs/*
│       │   │   ├── costs.py         # /api/costs/*
│       │   │   └── health.py        # /health, /metrics
│       │   ├── services/            # Business logic layer
│       │   │   ├── workflow_service.py
│       │   │   ├── story_service.py
│       │   │   ├── run_service.py
│       │   │   ├── blob_service.py
│       │   │   ├── voice_service.py
│       │   │   ├── cost_service.py
│       │   │   ├── errors.py        # Domain error types
│       │   │   └── http_errors.py   # HTTP error handlers
│       │   ├── logging.py           # Structured logging (structlog)
│       │   ├── metrics.py           # Prometheus metrics
│       │   ├── middleware.py         # Request middleware
│       │   └── validation_limits.py # Audio validation thresholds
│       ├── alembic/                 # DB migrations (10 versions)
│       ├── tests/                   # Unit tests
│       ├── Dockerfile
│       └── pyproject.toml
│
├── AGENTS.md                        # This file (canonical)
├── CLAUDE.md -> AGENTS.md
└── README.md
```

## Architecture Overview

### Workflow Engine

The project uses a **custom workflow engine** (`app/engine/`):

- `WorkflowEngine` — top-level API: trigger, retry_step, pause, resume, cancel
- `WorkflowRunner` — executes a DAG of steps with topological ordering, persists step state to DB
- `CronScheduler` — runs periodic workflows (e.g., recon pod cleanup)
- Supports **pause/resume** at the workflow level and **step-level retry**
- Each step receives a `StepContext` with DB session, workflow row, GPU provider, and prior step outputs

### Content Pipeline (main workflow)

Steps executed in order:
1. **story** — Generate story via LLM (architect → writer → reviewer loop)
2. **tts** — Normalize text, chunk into sentences, synthesize each chunk via TTS pod with retry
3. **image** — Group chunks into scenes, generate image prompts via LLM, synthesize via image pod
4. **stitch** — Concatenate WAV chunks → MP3, optionally create video with images
5. **cleanup** — Terminate all GPU pods associated with the workflow

### GPU Pod Lifecycle

- `GpuProvider` protocol in `gpu/base.py` (RunPod implementation in `gpu/runpod.py`)
- `gpu/lifecycle.py` — DB-tracked pod creation, readiness polling, termination
- Recon cron (`workflows/recon.py`) — terminates orphaned pods on a schedule

## GPU Server Endpoints

### TTS Server (port 8005)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check |
| GET | `/ready` | Readiness check (model loaded?) |
| POST | `/synthesize` | `{text, voice, seed}` → WAV bytes |

### Image Server (port 8006)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check |
| GET | `/ready` | Readiness check (200 if loaded, 503 if loading) |
| POST | `/generate` | `{prompt, width, height}` → PNG bytes. Optional: `negative_prompt`, `steps`, `guidance_scale`, `seed` |

## creepy-brain Key Modules

| Module | Purpose |
|--------|---------|
| `engine/` | Custom workflow engine with DAG execution, pause/resume, step retry |
| `pipeline/` | LLM story generation (architect → writer → reviewer) |
| `llm/client.py` | Anthropic + OpenRouter providers with structured output |
| `text/normalization.py` | LLM-based text normalization for TTS |
| `text/chunking.py` | Sentence-based text chunking |
| `text/scene_grouping.py` | Group chunks into scenes for image generation |
| `audio/validation.py` | RMS, peak, voiced-ratio checks (numpy) |
| `audio/encoding.py` | WAV → MP3 encoding |
| `workflows/steps/tts.py` | Per-chunk synthesis with seed-increment retry |
| `workflows/steps/image.py` | Per-scene image generation via image pod |
| `workflows/steps/stitch.py` | Audio concatenation + optional video creation |
| `workflows/recon.py` | Orphaned GPU pod cleanup cron |
| `gpu/lifecycle.py` | DB-tracked pod create/wait/terminate |
| `gpu/runpod.py` | RunPod GPU provider |

## Commands

```bash
# TTS server
cd services/tts-server && python3 minimal_server.py
cd services/tts-server && python3 -m py_compile minimal_server.py && echo OK

# Image server
cd services/image-server && python3 -m py_compile server.py && echo OK

# creepy-brain
cd services/creepy-brain && pip install -e .

# Run tests
cd services/creepy-brain && python3 -m pytest tests/ -v

# Type checking
cd services/creepy-brain && python3 -m mypy app/ --strict
```

## GPU Rules

- **Always use CUDA directly.** Never use `device_map="auto"` or `accelerate` — GPU pods run on a single RunPod GPU. Load models with `.to("cuda")`.
- Models cannot coexist in VRAM. TTS and SDXL run on **separate pods**.

## Deploy on RunPod

**NEVER build Docker images locally.** Push to GitHub — GitHub Actions builds and pushes images on push to `main`, `docker-release`, tags, and PRs.

### Images
GitHub Container Registry images (built by CI):
- `ghcr.io/sgoel1220/tts-server:main` — TTS server
- `ghcr.io/sgoel1220/image-server:main` — Image generation server
- `ghcr.io/sgoel1220/creepy-brain:main` — Creepy brain orchestrator

### RunPod Settings
- Use **community cloud** with spot instances for cost savings
- **No volume** — models download fresh each start
- Container disk: 20-25 GB
- Ports: 8005 (TTS), 8006 (image-server)

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
