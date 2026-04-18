# Minimal TTS Server

This service is a stateless Chatterbox synthesis pod for RunPod. It exposes only health/readiness checks and single-shot synthesis; orchestration belongs to `services/creepy-brain`.

## Runtime Files

- `minimal_server.py` - FastAPI app and Chatterbox model loader
- `Dockerfile` - GPU image definition
- `voices/` - predefined reference voices copied into `/app/reference_audio/`

## Endpoints

| Method | Path | Response |
|--------|------|----------|
| `GET` | `/health` | `{"status": "ok"}` |
| `GET` | `/ready` | `{"ready": true}` when the model has loaded |
| `POST` | `/synthesize` | WAV bytes |

`POST /synthesize` accepts:

```json
{
  "text": "Hello world",
  "voice": "Gianna.wav",
  "seed": 1234,
  "exaggeration": 0.5,
  "cfg_weight": 0.5,
  "temperature": 0.8,
  "repetition_penalty": 1.2,
  "min_p": 0.05,
  "top_p": 1.0
}
```

## Local Syntax Check

```bash
python3 -m py_compile minimal_server.py
```

## RunPod

The image published by CI is `ghcr.io/sgoel1220/tts-server:main`. The container listens on port `8005`.
