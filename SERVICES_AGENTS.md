# Workspace Notes

- The actual git repo and all server code live in `Chatterbox-TTS-Server/`. Run git, Python, test, and Docker commands from that directory, not from the workspace root.
- The workspace root mainly holds experiment inputs and artifacts: `assets/`, `script.txt`, `script_long.txt`, and ad hoc logs.
- For deeper server-specific guidance, also read `Chatterbox-TTS-Server/AGENTS.md`.

# Canonical Commands

- Use a Python 3.10 environment.
- Entrypoints: `python lite_clone_server.py` or `python app.py` or `uvicorn app:app`.
- Repo quality-gate order:
  1. `python -m compileall app.py engine.py config.py models.py utils.py lite_clone_server.py routes.py run_orchestrator.py enums.py files.py job_store.py cpu_runtime.py`
  2. `python -m py_compile app.py`
  3. `node --check lite_ui/script.js`

# Structure That Matters

- App factory + static file serving: `Chatterbox-TTS-Server/app.py`
- Backward-compat entrypoint: `Chatterbox-TTS-Server/lite_clone_server.py`
- API route handlers: `Chatterbox-TTS-Server/routes.py`
- Model loading and synthesis: `Chatterbox-TTS-Server/engine.py`
- TTS job execution / chunk orchestration: `Chatterbox-TTS-Server/run_orchestrator.py`
- Config defaults and accessors: `Chatterbox-TTS-Server/config.py` + `config.yaml`
- Pydantic request/response models: `Chatterbox-TTS-Server/models.py`
- Audio encoding/processing/stitching: `Chatterbox-TTS-Server/audio/`
- Text chunking and normalization: `Chatterbox-TTS-Server/text/`
- Browser UI assets: `Chatterbox-TTS-Server/lite_ui/`
- In-memory async job state: `Chatterbox-TTS-Server/job_store.py`
- Reference audio validation and listing: `Chatterbox-TTS-Server/files.py`
- Shared enumerations: `Chatterbox-TTS-Server/enums.py`
- CPU/MPS fallback runtime: `Chatterbox-TTS-Server/cpu_runtime.py`
- Backward-compat re-exports: `Chatterbox-TTS-Server/utils.py`
- Docker deployment: `Chatterbox-TTS-Server/lite_runpod/`

# Repo-Specific Gotchas

- Dependencies live in `lite_runpod/requirements.txt` (CUDA 12.1 + NVIDIA). There are no separate `requirements-nvidia.txt` or `requirements-rocm.txt` files.
- `chatterbox-v2` is installed separately with `--no-deps` in the Dockerfile. Do not "clean up" the install order unless the task is specifically about dependency management.
- In this workspace copy, `config.yaml` currently defaults to `model.repo_id: chatterbox`, `tts_engine.device: cuda`, and `audio_output.save_to_disk: false`. Do not assume the README's broader default examples match the checked-in config.
- The engine supports three model types: `original` (ChatterboxTTS), `turbo` (ChatterboxTurboTTS), and `multilingual` (ChatterboxMultilingualTTS). Selection is via `model.repo_id` in config.
- API routes:
  - `GET /` — Lite UI (served from `lite_ui/index.html`)
  - `GET /api/model-info` — Model status and capabilities
  - `GET /api/reference-audio` — List reference audio files
  - `POST /api/reference-audio/upload` — Upload a reference audio file
  - `POST /api/chunks/preview` — Preview text chunking
  - `POST /tts` — Synchronous TTS generation
  - `POST /api/jobs` — Create async TTS job
  - `GET /api/jobs/{job_id}` — Poll async job status
- A `Successfully generated audio...` log line does not mean a file was written to `outputs/`; disk persistence only happens when `save_final_audio` / `save_chunk_audio` are true in the request.
- The root `assets/ghohor047_trimmed.mp3` exists, but the server reads from `Chatterbox-TTS-Server/reference_audio/`.

# Deployment Notes

- Docker builds from `lite_runpod/Dockerfile` for `linux/amd64` with NVIDIA CUDA 12.1 runtime.
- `docker-entrypoint.sh` is RunPod-aware: it symlinks `config.yaml`, `reference_audio/`, `outputs/`, `logs/`, `model_cache/`, and `hf_cache/` into `/workspace/...` when that directory exists.
- Docker Hub images: `shubh67678/chatterbox-lite-runpod:latest` (primary), `shubh67678/chatterbox-tts-server:latest` (alias).
- RunPod template: `chatterbox-lite` · port 8005 · Nvidia GPU · 25 GB container disk · ≥20 GB volume disk.
