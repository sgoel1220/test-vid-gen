# Plan: Postgres-backed persistence for Chatterbox TTS via a Twingate-fronted metadata server

> **Trackers:**
> - Beads epic: `Chatterbox-TTS-Server-7mk` (DB at `Chatterbox-TTS-Server/.beads/beads.db`)
> - Workspace checklist: [`TODO.md`](./TODO.md)
> - Original plan file: `~/.claude/plans/fizzy-floating-simon.md`

## Context

Today every TTS run on the RunPod GPU pod is ephemeral. Job state lives in `Chatterbox-TTS-Server/job_store.py`'s in-memory dict; chunks and final audio are only written to `outputs/` if `save_chunk_audio` / `save_final_audio` happen to be true on that request, and even then they're scoped to the pod's persistent volume — there is no way to query "show me all the runs I generated for this script" or to build a real history UI.

Goal: stand up a small, durable persistence layer that the GPU pod talks to over Twingate. Every input text, every chunk, every per-chunk audio, and every stitched final audio gets a row + a stored audio blob on a long-running home server. The GPU pod stays stateless and disposable; the home server is the source of truth.

The architecture must hold up to:
- Replacing the RunPod pod with a different GPU host.
- Adding a future "orchestrator server" that picks a GPU, chunks scripts, and dispatches work.
- Migrating audio storage from local filesystem to MinIO/S3 without schema churn.
- Twingate or the home server being briefly unreachable during a long generation.

## Architecture

```
┌─────────────────────────┐    Twingate     ┌─────────────────────────────┐
│ RunPod GPU pod          │  ───────────►   │ Home server                 │
│ Chatterbox-TTS-Server   │                 │                             │
│                         │                 │  ┌───────────────────────┐  │
│  routes.py              │                 │  │ metadata-svc          │  │
│  run_orchestrator.py    │                 │  │ (FastAPI + asyncpg    │  │
│  persistence/           │                 │  │  + SQLAlchemy 2 async)│  │
│   ├ client.py  (httpx)  │                 │  └──────────┬────────────┘  │
│   ├ outbox.py  (sqlite) │                 │             │               │
│   └ config.py           │                 │  ┌──────────▼────────────┐  │
│           │             │                 │  │ Postgres 16           │  │
│           ▼             │                 │  └───────────────────────┘  │
│  creepy_pasta_protocol  │◄────────────────┤  creepy_pasta_protocol      │
│  (Pydantic v2 wire DTOs)│  shared package │  (Pydantic v2 wire DTOs)    │
│                         │                 │                             │
│  Twingate headless      │                 │  ┌───────────────────────┐  │
│  client (in entrypoint) │                 │  │ /var/lib/creepy_pasta/│  │
└─────────────────────────┘                 │  │   audio/  (blobs)     │  │
                                            │  └───────────────────────┘  │
                                            └─────────────────────────────┘
```

Three components, all versioned in this workspace:
- `Chatterbox-TTS-Server/` — existing GPU pod code. Gains a `persistence/` package + Twingate wiring. Depends on `creepy_pasta_protocol`.
- `metadata-server/` — new. Owns Postgres + audio filesystem. Deployed via docker-compose on the home server. Depends on `creepy_pasta_protocol`.
- `creepy_pasta_protocol/` — new. A tiny standalone Python package at the workspace root containing the **wire-format Pydantic v2 models only**. This is the single source of truth for every request/response DTO that crosses the Twingate boundary. Both sides install it (editable in dev, copied into each Docker image in prod). This is the "make code as static as possible" lever — the pod and the server cannot drift on schema, and any breaking change is caught at type-check time on both sides.

### Static-typing discipline (non-negotiable)

This is the spine that keeps the system from rotting under refactors:

- **Pydantic v2 everywhere**, with `model_config = ConfigDict(extra='forbid', frozen=True, str_strip_whitespace=True)` on every wire DTO. `extra='forbid'` catches drift between client and server; `frozen=True` makes them safe to pass around.
- **SQLAlchemy 2.x typed mappings** — `Mapped[X]` / `mapped_column(...)` style, no legacy `Column()` calls, no untyped `Any` columns. The ORM models live in `metadata-server/app/models.py` and are an internal concern, never exposed to FastAPI handlers directly.
- **No raw dicts on the wire or in the DB**. Every JSONB column is backed by a typed Pydantic model via a small `PydanticJSONB` `TypeDecorator`:

  ```python
  class PydanticJSONB(TypeDecorator):
      impl = JSONB
      cache_ok = True
      def __init__(self, model: type[BaseModel], **kw):
          self._model = model
          super().__init__(**kw)
      def process_bind_param(self, value, dialect):
          return None if value is None else value.model_dump(mode='json')
      def process_result_value(self, value, dialect):
          return None if value is None else self._model.model_validate(value)
  ```

  So `runs.settings`, `chunks.validation`, and `runs.warnings` are stored as JSONB but typed end-to-end as `ResolvedSettingsSnapshot`, `ChunkValidationSnapshot`, and `RunWarnings`.
- **Three layers, three model families, explicit conversion at the seams**:
  1. **Wire / DTO layer** (`creepy_pasta_protocol`) — Pydantic v2, frozen, validated. What the pod sends and what the server returns.
  2. **ORM layer** (`metadata-server/app/models.py`) — SQLAlchemy 2 typed mappings. Internal to the metadata server.
  3. **Domain layer** (small) — only where needed. The wire DTOs are good enough for most things; we add domain types only if a service-layer concern justifies it.

  Conversion functions (`to_orm()`, `to_dto()`) live in `metadata-server/app/converters.py` and are the **only** place the two layers touch.
- **mypy strict** on `metadata-server/`, `creepy_pasta_protocol/`, and `Chatterbox-TTS-Server/persistence/`. Added to the existing quality-gate command.
- **No `dict[str, Any]` in public function signatures.** Internal helpers may use them transiently, but anything exported is typed.
- **FastAPI handlers always use `response_model=`**, so responses are validated against the wire DTOs at the boundary and OpenAPI is accurate.

### Why this shape

**Why a thin HTTP service in front of PG, not direct asyncpg from the pod:**
- DB credentials never leave the home server. The pod only carries an API key.
- Schema migrations and storage layout live in one place.
- Audio bytes need a multipart upload path anyway — adding REST around the rest is a small additional cost.
- The future orchestrator server can reuse the exact same API surface.

**Why filesystem + paths-in-PG, not BYTEA / Large Objects:**
- Long stitched audio (many MB) makes PG rows slow and bloats backups.
- Audio is naturally streamable from disk via `FileResponse`.
- The `audio_blobs` table abstracts the storage backend. Migrating to MinIO later is one module change in `metadata-server/app/storage.py`, no schema churn.

**Why an outbox pattern on the pod, not direct synchronous writes:**
- A single `httpx` blip during a 20-minute generation should not lose work.
- Outbox decouples generation from upload — uploads can retry, the generation loop never blocks indefinitely.
- The same outbox mechanism transfers cleanly to the future orchestrator.

## creepy_pasta_protocol (new shared package) · `bd:gc9`

Layout:

```
creepy_pasta_protocol/
  pyproject.toml            # name="creepy-pasta-protocol", pure-python, no runtime deps beyond pydantic
  src/creepy_pasta_protocol/
    __init__.py             # public re-exports + PROTOCOL_VERSION = "1"
    common.py               # Frozen base, common StrEnums (StorageBackend, RunStatus, AudioFormat)
    settings.py             # ResolvedSettingsSnapshot — frozen mirror of run_orchestrator.ResolvedSettings
    validation.py           # ChunkValidationSnapshot
    scripts.py              # CreateScriptRequest, ScriptDTO
    voices.py               # VoiceDTO, CreateVoiceResponse
    runs.py                 # CreateRunRequest, PatchRunRequest, RunSummaryDTO, RunDetailDTO, RunWarnings
    chunks.py               # ChunkSpec (index+text), ChunkDTO
    audio.py                # AudioBlobDTO, UploadChunkAudioMetadata, UploadFinalAudioMetadata
  tests/
    test_roundtrip.py       # every DTO model_validate(model_dump()) round-trip
```

Rules:
- All models inherit from a `Frozen` base with `model_config = ConfigDict(extra='forbid', frozen=True, str_strip_whitespace=True, populate_by_name=True)`.
- Enums are real `enum.StrEnum` subclasses, not literal unions.
- Versioning: include a `PROTOCOL_VERSION = "1"` constant; the metadata server returns it in `/healthz` and the pod logs a warning if they mismatch.
- This package is **pure schemas + enums**. No HTTP code, no SQLAlchemy, no FastAPI imports. Both consumers can `pip install -e ../creepy_pasta_protocol` in dev and `COPY` it into their Docker images for prod.

## Data model (Postgres, owned by metadata-server) · `bd:n2u`

All tables use `uuid` primary keys (`gen_random_uuid()` from pgcrypto) and `created_at timestamptz default now()`.

- **`scripts`** — original input text.
  - `id uuid pk`, `text text not null`, `text_sha256 char(64) unique`, `char_count int`, `created_at`
  - sha256 lets the client dedupe naturally (POST returns existing row if hash matches).

- **`voices`** — reference voice metadata. One row per uploaded reference audio file.
  - `id uuid pk`, `filename text not null`, `audio_blob_id uuid fk → audio_blobs`, `duration_sec float`, `created_at`
  - `unique(filename)` so the pod can upsert by filename.

- **`runs`** — one row per TTS generation request (mirrors the current async job).
  - `id uuid pk`, `script_id uuid fk`, `voice_id uuid fk nullable`
  - `run_label text`, `status` → typed `RunStatus` enum (queued|running|completed|failed) backed by a Postgres enum type
  - `settings jsonb not null` → `Mapped[ResolvedSettingsSnapshot]` via `PydanticJSONB`
  - `output_format` → typed `AudioFormat` enum
  - `source_chunk_count int`, `selected_chunk_indices int[]`
  - `normalized_text text nullable`
  - `warnings jsonb` → `Mapped[RunWarnings]` via `PydanticJSONB`
  - `error text nullable`
  - `final_audio_id uuid fk → audio_blobs nullable`
  - `created_at`, `started_at nullable`, `completed_at nullable`
  - `pod_run_id text` — original `run_id` minted by the pod, useful for cross-referencing logs.

- **`chunks`** — one row per (run, chunk index).
  - `id uuid pk`, `run_id uuid fk on delete cascade`, `chunk_index int not null`
  - `text text not null`, `audio_blob_id uuid fk nullable`
  - `attempts_used int`
  - `validation jsonb nullable` → `Mapped[ChunkValidationSnapshot | None]` via `PydanticJSONB`
  - `unique(run_id, chunk_index)`

- **`audio_blobs`** — one row per stored audio file. Storage-backend-agnostic.
  - `id uuid pk`, `storage_backend text not null` (`'local'` for now, `'s3'` later)
  - `storage_key text not null` (opaque, e.g. `2026/04/15/<uuid>.wav`)
  - `sha256 char(64)`, `byte_size bigint`, `mime_type text`, `format text`
  - `sample_rate int`, `duration_sec float nullable`
  - `created_at`
  - Indexed by sha256 for dedupe.

Migrations are managed by **Alembic** from day one. The first migration creates everything above.

## metadata-server (new)

Layout (`metadata-server/` at workspace root, sibling of `Chatterbox-TTS-Server/`):

```
metadata-server/
  app/
    __init__.py
    main.py            # FastAPI app factory + lifespan          [bd:hct]
    config.py          # pydantic-settings env config (typed)    [bd:hct]
    db.py              # async SQLAlchemy engine + session       [bd:hct]
    auth.py            # Bearer API-key middleware               [bd:hct]
    storage.py         # AudioStore protocol + LocalFilesystem*  [bd:hct, bd:5qj]
    types.py           # PydanticJSONB TypeDecorator + helpers   [bd:t9j]
    models.py          # SQLAlchemy 2.x typed ORM models         [bd:t9j]
    converters.py      # ORM ↔ DTO conversion (the only seam)    [bd:5ff]
    services/          # service layer — typed business logic    [bd:5ff]
      scripts.py
      voices.py
      runs.py
      chunks.py
      audio.py
    routes/
      health.py        # GET /healthz, GET /readyz (PROTOCOL_VERSION)
      scripts.py       # POST /v1/scripts                        [bd:byq]
      voices.py        # POST /v1/voices, GET /v1/voices         [bd:byq]
      runs.py          # POST/PATCH/GET /v1/runs                 [bd:s7c]
      chunks.py        # POST /v1/runs/{id}/chunks               [bd:s7c]
      audio.py         # /v1/runs/.../audio + /v1/audio/{id}     [bd:5qj]
  alembic/
    env.py
    versions/
      0001_initial.py                                            [bd:n2u]
  alembic.ini                                                    [bd:n2u]
  pyproject.toml       # FastAPI, SQLAlchemy[asyncio], asyncpg, alembic, pydantic, pydantic-settings,
                       # creepy-pasta-protocol (path dep), pytest, mypy, ruff
  mypy.ini             # strict = True
  Dockerfile
  docker-compose.yml   # postgres:16 + metadata-svc + named volume for /var/lib/creepy_pasta/audio
  .env.example
  README.md            # operator notes (Twingate resource setup, backup, restore)
```

The split into `routes/` (HTTP-only) + `services/` (typed business logic) + `converters.py` (ORM↔DTO seam) is what gives the codebase room to grow without sprouting random helper modules. Routes parse + validate requests, call a service function with typed args, get back a typed result, and return it as the wire DTO. Services never touch FastAPI; they take typed inputs and return typed outputs. This is the "good level of abstraction" — three layers, each with one job, with the seams in named files.

API surface (all paths under `/v1`, all require `Authorization: Bearer <api_key>`):

- `POST /v1/scripts` → `{text}` returns `{id, text_sha256, created}` (idempotent on sha256).
- `POST /v1/voices` → multipart `{filename, audio}` returns `{id}`. Idempotent on filename.
- `POST /v1/runs` → body is the full run intent (script_id, voice_id, settings, run_label, output_format, source_chunk_count, selected_chunk_indices, pod_run_id, status='queued'). Returns `{id}`.
- `PATCH /v1/runs/{id}` → partial update for status, started_at, completed_at, error, warnings, normalized_text.
- `POST /v1/runs/{id}/chunks` → `[{chunk_index, text}, ...]` bulk-creates chunk rows up front. Idempotent (upsert on `(run_id, chunk_index)`).
- `POST /v1/runs/{id}/chunks/{chunk_index}/audio` → multipart `{audio, format, sample_rate, duration_sec, attempts_used, validation_json}`. Creates audio_blob, links to chunk, returns `{audio_blob_id}`.
- `POST /v1/runs/{id}/final_audio` → multipart `{audio, format, sample_rate, duration_sec}`. Creates audio_blob, sets `runs.final_audio_id`, returns `{audio_blob_id}`.
- `GET /v1/runs?limit=&offset=` → run list with summary fields (for history UI).
- `GET /v1/runs/{id}` → full run + nested chunks + audio metadata.
- `GET /v1/audio/{audio_blob_id}` → streams audio bytes via `FileResponse` with the right `Content-Type`.

Storage layout on disk (`storage.py`):
```
/var/lib/creepy_pasta/audio/{yyyy}/{mm}/{dd}/{audio_blob_id}.{ext}
```
The pod never sees this path. It only ever knows `audio_blob_id`. Backend swap = rewrite `storage.py`.

## GPU-pod-side persistence package · `bd:4qt`

New package: `Chatterbox-TTS-Server/persistence/`

```
persistence/
  __init__.py           # public surface: PersistenceClient, get_client(), is_enabled()
  config.py             # pydantic-settings env config (METADATA_API_URL, METADATA_API_KEY, ENABLED flag)
  client.py             # httpx.AsyncClient wrapper, typed methods, retries with tenacity
  outbox.py             # sqlite-backed outbox: enqueue, drain loop, retry-with-backoff   [bd:58a]
  errors.py
```

DTOs are imported from `creepy_pasta_protocol`, NOT redefined here. That's the whole point of the shared package: the pod and the server type-check against the same models.

Key behaviors:
- **Singleton client** initialized in `app.py` lifespan. Disabled cleanly when `METADATA_API_URL` is unset (so the pod still works standalone for dev).
- **Outbox**: a single sqlite file in `outputs/persistence_outbox.sqlite`. One table `outbox(id, kind, payload_json, blob_path nullable, attempts, last_error, created_at)`. Kinds: `create_script`, `create_run`, `patch_run`, `create_chunks`, `upload_chunk_audio`, `upload_final_audio`. A background asyncio task drains the outbox on a 5s tick with exponential backoff (cap 60s). On startup, the drain runs immediately so a restarted pod ships any pending work. The outbox enforces causal ordering by kind so causally-dependent rows never ship before their parent.
- **Failure semantics**: persistence failures NEVER fail the synthesis. If the metadata server is unreachable, the chunk/run is enqueued in the outbox and the pod keeps generating audio. The local `outputs/` write path stays as a belt-and-suspenders fallback (see "Local saves" below).

### Integration points · `bd:cn9`

In `Chatterbox-TTS-Server/run_orchestrator.py:execute_lite_clone_run` (lines 223–430). All persistence calls go through enqueue (the outbox), so they NEVER block synthesis on a network round trip.

| Step | Existing code | New persistence call |
|------|---------------|----------------------|
| After `synthesis_text` is finalized (post-normalization), around line 246 | `synthesis_text = normalized` | `script_id = await persist.enqueue_create_script(text=synthesis_text)` |
| After `preview` is built and `selected_entries` computed, around line 251 | `selected_entries = build_selected_chunk_entries(...)` | `await persist.enqueue_create_run(script_sha256, voice_filename, settings_snapshot, run_label, output_format, source_chunk_count, selected_chunk_indices, pod_run_id=run_id_str)`; then `await persist.enqueue_create_chunks(pod_run_id, selected_entries)` |
| Before the chunk loop starts | `progress_callback(...)` | `await persist.enqueue_patch_run(pod_run_id, status='running', started_at=now())` |
| Inside chunk loop, after `raw_chunks.append(best_audio)` at line 346 | (audio is in memory) | `chunk_bytes = encode_audio(best_audio, chunk_sr, settings.output_format, settings.target_sample_rate)`; `await persist.enqueue_upload_chunk_audio(pod_run_id, chunk_idx, chunk_bytes, settings.output_format, sample_rate, duration_sec, attempts_used, validation_result)` |
| After `final_audio = post_process_final_audio(...)` at line 383 | (final stitched audio in memory) | encode and `await persist.enqueue_upload_final_audio(pod_run_id, final_bytes, ...)` |
| At the very end of `execute_lite_clone_run` | returns LiteCloneRunResponse | `await persist.enqueue_patch_run(pod_run_id, status='completed', completed_at=now(), warnings=warnings, normalized_text=normalized_text)` |
| In `run_lite_clone_job` (line 437) `except` branches | sets job_store FAILED | `await persist.enqueue_patch_run(pod_run_id, status='failed', error=msg, completed_at=now())` |

### Async refactor · `bd:57q`

`execute_lite_clone_run` is currently sync. Two clean options to call async persistence from it:
1. Make the function `async def` and update the two callers (`/tts` route at routes.py:124 and `run_lite_clone_job`). The `/tts` route is already `async`. The job worker uses `threading.Thread` — switch it to spawn an `asyncio.Task` (or call `asyncio.run` inside the thread).
2. Pass an `asyncio.Loop`-backed sync facade into the orchestrator.

**Pick option 1.** It's a real cleanup that pays off long-term and has no behavior change for clients.

The encode-then-upload pattern reuses the existing `audio/encoding.py:encode_audio()` (lines 145–177). Per-chunk encoding does add CPU overhead, but the existing `save_chunk_audio` path already does the same encode — we're just sending bytes over the wire instead of (or in addition to) writing them to disk.

## Local saves on the pod (the safety net)

Behavior:
- The existing `save_chunk_audio` / `save_final_audio` writes to `outputs/lite_clone_runs/<run_id>/` stay in place, **unchanged**.
- Persistence is layered on top, not bolted in series. The order in code is: encode → upload (best-effort, via outbox) → write to disk (existing path).
- This means: if the metadata server is down, the user still has a working pod with files on the persistent volume. If the pod is destroyed, the home server still has everything.
- `outputs/lite_clone_runs/<run_id>/manifest.json` gains an extra field `metadata_run_id` so the disk artifacts can be reconciled to the home-server row.

## Twingate wiring · `bd:6wf` (pod) and `bd:d28` (home server)

Pod side:
- Add the Twingate headless client to `lite_runpod/Dockerfile`:
  ```
  RUN curl -fsSL https://binaries.twingate.com/client/linux/install.sh | bash
  ```
- New env vars on the pod (set in the RunPod template):
  - `TWINGATE_NETWORK` — Twingate network slug
  - `TWINGATE_SERVICE_KEY` — JSON service account key file content (mounted as a secret or pasted as env)
  - `METADATA_API_URL` — `https://metadata-svc.<network>.twingate.io` (or the LAN address resolved through Twingate)
  - `METADATA_API_KEY` — bearer token for the metadata server
- `lite_runpod/docker-entrypoint.sh` is updated to start the Twingate client *before* exec'ing the FastAPI server. Wait for the Twingate tunnel to come up (poll resolution of the metadata URL) with a hard timeout; on failure, log loudly and continue starting the server (persistence will simply enqueue everything to the outbox).

Home-server side:
- A Twingate Connector container runs on the home server (separate compose service, or the official `twingate/connector` image).
- The metadata-svc and Postgres are exposed only to the Twingate network, **never** to the public internet. Bind to 127.0.0.1 inside the LAN, expose only via the Twingate Resource.
- Backup strategy notes belong in `metadata-server/README.md`: `pg_dump` of the database, plus `rsync`/`restic` of the audio storage volume.

## UI: history panel (small, scoped) · `bd:fl2` + `bd:a2c`

Currently `lite_ui/index.html` only shows the latest run. Add a History tab next to the existing form/results panes. The UI work itself (`bd:a2c`) is a follow-up that doesn't block MVP, but the proxy endpoints (`bd:fl2`) ship with MVP so the UI can land cleanly later.

New pod-side proxy routes (so the browser only ever talks to the pod, not to the metadata-svc directly):
- `GET /api/history?limit=&offset=` → proxies to `GET /v1/runs`
- `GET /api/history/{run_id}` → proxies to `GET /v1/runs/{id}`
- `GET /api/history/audio/{audio_blob_id}` → streams `GET /v1/audio/{id}` back to the browser

These proxies live in a new `routes_history.py` (or a section of `routes.py`) and use the same `PersistenceClient` instance.

## Critical files to modify

- `Chatterbox-TTS-Server/run_orchestrator.py` — make `execute_lite_clone_run` async, add persistence calls at the points listed above.
- `Chatterbox-TTS-Server/routes.py:122-132` — `/tts` and `/api/jobs` adapt to the async signature; `run_lite_clone_job` switches from `threading.Thread` to an asyncio task (or `asyncio.run` inside a thread).
- `Chatterbox-TTS-Server/app.py` — add lifespan setup/teardown for `PersistenceClient` and the outbox drain task.
- `Chatterbox-TTS-Server/config.py:93-98` — delete the unused `story_history` block and any DEFAULT_STORY_HISTORY_* constants. (`bd:1r2`)
- `Chatterbox-TTS-Server/lite_runpod/Dockerfile` — install Twingate client; `COPY` the `creepy_pasta_protocol/` package and `pip install` it; copy new `persistence/` package.
- `Chatterbox-TTS-Server/lite_runpod/docker-entrypoint.sh` — start Twingate client; create persistence outbox dir.
- `Chatterbox-TTS-Server/lite_runpod/requirements.txt` — add `httpx`, `tenacity`, `aiosqlite`, `pydantic-settings`, `mypy`.
- New: `creepy_pasta_protocol/` package (workspace root, ~10 files).
- New: `Chatterbox-TTS-Server/persistence/` package (six files).
- New: `metadata-server/` service (entire tree as scoped above).
- Updated: workspace root `TODO.md` and `Chatterbox-TTS-Server/AGENTS.md` quality-gate command.

## Verification · `bd:uqr`

End-to-end sequence after implementation:

1. **metadata-server unit tests**: spin Postgres in a docker-compose test profile; pytest covers each route, the storage layer, and an audio round-trip.
2. **metadata-server stand-up**: bring up `docker compose up` on the home server; hit `GET /healthz` and `GET /readyz` from the LAN, then again through Twingate from a laptop.
3. **Pod local dev** (no Twingate): unset `METADATA_API_URL` → server starts, generation works, `outputs/` gets files, no persistence calls. Sanity that the new code is properly opt-in.
4. **Pod local dev** (mocked metadata-svc): point `METADATA_API_URL` at a local `metadata-server` instance, run a small generation through the UI, then query Postgres directly to confirm: 1 script row, 1 run row with status=completed, N chunk rows with audio_blob_ids, 1 final audio_blob, and the audio files exist on disk under the configured storage root.
5. **Outbox resilience**: stop the metadata server, start a generation, confirm chunks land in the sqlite outbox; restart the metadata server; confirm the drain task ships everything within ~30s and the run row reaches status=completed.
6. **RunPod end-to-end**: build & push the new image; pod starts, Twingate tunnel comes up, a job runs from the UI, and the home server shows the rows. Confirm the audio plays back via the proxy URL `/api/history/audio/<id>`.
7. **Schema migrations**: drop the volume, run alembic from scratch — migration 0001 succeeds. Then add a no-op migration 0002 to confirm the upgrade path works.

## Execution tracking

This is too big for a single session. Two parallel trackers are maintained:

1. **Beads** — the structured queue at `Chatterbox-TTS-Server/.beads/beads.db`. Use `mcp__beads__ready` to find the next claimable issue and `mcp__beads__claim` before starting work. Always set the workspace context first:
   ```
   mcp__beads__context(action='set', workspace_root='/Users/shubham/Desktop/services/side_business/creepy_pasta/Chatterbox-TTS-Server')
   ```
2. **`TODO.md` at workspace root** — flat human-readable checklist that mirrors the beads. Every line carries its bead id (`[bd:xxx]`). Update both when status changes.

### Beads created

Epic: **`Chatterbox-TTS-Server-7mk`** — `Postgres-backed persistence for Chatterbox TTS via Twingate metadata server`

| # | Bead id | Title | Depends on |
|---|---------|-------|------------|
| 0 | `gc9` | Bootstrap creepy_pasta_protocol shared package | — |
| 1 | `n2u` | Design metadata-server schema + Alembic 0001 migration | 0 |
| 2 | `hct` | Scaffold metadata-server FastAPI app | 1 |
| 3 | `t9j` | PydanticJSONB TypeDecorator + ORM models | 2 |
| 4 | `5ff` | converters.py + service layer skeleton | 3 |
| 5 | `byq` | /v1/scripts and /v1/voices endpoints | 4 |
| 6 | `s7c` | /v1/runs CRUD + /v1/runs/{id}/chunks bulk create | 4 |
| 7 | `5qj` | Audio upload endpoints + /v1/audio/{id} streaming + LocalFilesystemAudioStore | 4 |
| 8 | `d28` | Stand up metadata-server on home server behind Twingate | 5, 6, 7 |
| 9 | `4qt` | Pod: persistence/ package — typed config, errors, httpx client | 0, 5, 6, 7 |
| 10 | `58a` | Pod: sqlite outbox + drain task with tenacity retries | 9 |
| 11 | `57q` | Pod: make execute_lite_clone_run async; switch job worker to asyncio | 9 |
| 12 | `cn9` | Pod: integrate persistence calls into execute_lite_clone_run + run_lite_clone_job | 10, 11 |
| 13 | `6wf` | Pod: Twingate headless client in Dockerfile + entrypoint wait + new env vars | 9 |
| 14 | `fl2` | Pod: history proxy routes (/api/history, /api/history/{id}, /api/history/audio/{id}) | 12 |
| 15 | `1r2` | Delete unused story_history config block + DEFAULT_STORY_HISTORY_* constants | — |
| 16 | `rdu` | Add mypy strict to quality-gate command | 0, 2, 9 |
| 17 | `uqr` | End-to-end verification on RunPod + outbox resilience test | 8, 12, 13 |
| 18 | `a2c` | (Follow-up, not blocking MVP) lite_ui history panel + audio playback through proxy | 14 |

All bead ids in this table are short suffixes; the full ids are `Chatterbox-TTS-Server-<suffix>`.

## Out of scope for this plan

- The future "orchestrator server" that selects a GPU and dispatches work. The metadata API is designed so it can plug in without changes; building it is a separate plan.
- Migrating audio storage to MinIO/S3. The `audio_blobs.storage_backend` column is the seam; flipping it is a separate plan.
- Authentication beyond a single shared API key. If/when multi-user access matters, add JWT or per-pod keys behind the same middleware.
