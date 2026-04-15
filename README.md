# Chatterbox TTS Server (Lite)

Self-hosted [Chatterbox](https://github.com/resemble-ai/chatterbox) TTS server with an OpenAI-compatible API and a minimal web UI. Designed for RunPod GPU deployment.

## Features

- OpenAI-compatible `/v1/audio/speech` API
- Voice cloning via reference audio upload
- 27 built-in voices
- Async job queue for long generations
- Minimal `lite_ui/` frontend

## Quick Start (Local)

```bash
pip install -r lite_runpod/requirements.txt
python lite_clone_server.py
# UI at http://localhost:8005
```

## Deploy on RunPod

Build from `lite_runpod/Dockerfile`. The server starts on port 8005.

```bash
docker build -f lite_runpod/Dockerfile -t chatterbox-lite .
docker run --gpus all -p 8005:8005 chatterbox-lite
```

See `lite_runpod/README.md` for full RunPod setup.

## Configuration

Edit `config.yaml` (or `lite_runpod/config.yaml` for Docker). Key settings:

| Key | Default | Description |
|-----|---------|-------------|
| `device` | `auto` | `auto`, `cuda`, `mps`, or `cpu` |
| `model` | `original` | `original`, `multilingual`, or `turbo` |
| `port` | `8005` | Server port |

## API

```bash
curl -X POST http://localhost:8005/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"tts-1","input":"Hello world","voice":"Emily"}' \
  --output speech.wav
```

## License

MIT
