# Lite RunPod Bundle

This folder defines a minimal NVIDIA/RunPod deployment for the lite clone UI and backend.

The image copies only the runtime files required by the lite flow:

- `app.py`, `lite_clone_server.py`
- `engine.py`, `cpu_runtime.py`
- `config.py`, `enums.py`
- `files.py`, `job_store.py`, `models.py`
- `routes.py`, `run_orchestrator.py`
- `utils.py`
- `audio/` (audio encoding, processing, stitching)
- `text/` (text chunking and normalization)
- `lite_ui/`
- `lite_runpod/config.yaml`

## Build

Run this from the repo root. **Always specify `--platform linux/amd64`** for RunPod compatibility:

```bash
docker buildx build --platform linux/amd64 \
  -f lite_runpod/Dockerfile \
  -t chatterbox-lite-runpod .
```

## Local run

```bash
docker run --rm --gpus all -p 8005:8005 chatterbox-lite-runpod
```

Open:

- UI: `http://127.0.0.1:8005/`
- Docs: `http://127.0.0.1:8005/docs`

## RunPod notes

- Container port: `8005`
- Persistent base inside RunPod: `/workspace/chatterbox-lite-runpod`
- Persisted directories: `model_cache/`, `reference_audio/`, `outputs/`, `logs/`, `hf_cache/`
- Persisted config: `/workspace/chatterbox-lite-runpod/config.yaml`

Upload clone reference files into `/workspace/chatterbox-lite-runpod/reference_audio/`.

The default lite config uses:

- `model.repo_id: chatterbox`
- `tts_engine.device: cuda`
- `generation_defaults.temperature: 0.6`
- `generation_defaults.exaggeration: 0.3`
- `generation_defaults.cfg_weight: 0.5`
- `generation_defaults.seed: 1234`
