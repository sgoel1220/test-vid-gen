# Chatterbox TTS Server (Lite)

Self-hosted [Chatterbox](https://github.com/resemble-ai/chatterbox) TTS server with an OpenAI-compatible API and a minimal web UI. Designed for RunPod GPU deployment.

## Features

- Voice cloning via reference audio upload
- 27 built-in voices
- Three model variants: original, turbo (with paralinguistic tags), and multilingual
- Sentence-aware text chunking with smart audio stitching
- Async job queue for long generations with progress polling
- Chunk validation and retry logic
- Post-processing: speed adjustment, DC removal, silence trimming, peak normalization
- Minimal `lite_ui/` frontend
- Output formats: WAV, MP3, Opus

## Quick Start (Local)

```bash
pip install -r lite_runpod/requirements.txt
python lite_clone_server.py
# UI at http://localhost:8005
```

## Deploy on RunPod

Build from `lite_runpod/Dockerfile`. Always specify `--platform linux/amd64` for RunPod.

```bash
docker buildx build --platform linux/amd64 \
  -f lite_runpod/Dockerfile \
  -t chatterbox-lite .

docker run --gpus all -p 8005:8005 chatterbox-lite
```

See `lite_runpod/README.md` for full RunPod setup.

## Configuration

Edit `config.yaml` (or `lite_runpod/config.yaml` for Docker). Key settings:

| Key | Default | Description |
|-----|---------|-------------|
| `tts_engine.device` | `cuda` | `auto`, `cuda`, `mps`, or `cpu` |
| `model.repo_id` | `chatterbox` | `chatterbox`, `chatterbox-turbo`, `chatterbox-multilingual` (or `original`, `turbo`, `multilingual`) |
| `lite_server.port` | `8005` | Server port |
| `generation_defaults.temperature` | `0.6` | Controls randomness |
| `generation_defaults.exaggeration` | `0.3` | Controls expressiveness |
| `generation_defaults.cfg_weight` | `0.5` | Classifier-Free Guidance weight |
| `generation_defaults.seed` | `1234` | Random seed (0 = random) |
| `generation_defaults.speed_factor` | `1.0` | Post-generation speed multiplier |
| `audio_output.sample_rate` | `24000` | Output sample rate |
| `audio_output.format` | `wav` | Default output format |
| `audio_output.max_reference_duration_sec` | `30` | Max reference audio length |

## API

### Synchronous TTS

```bash
curl -X POST http://localhost:8005/tts \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hello world",
    "reference_audio_filename": "my_voice.wav"
  }'
```

### Async Job

```bash
# Create job
curl -X POST http://localhost:8005/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Long text here...",
    "reference_audio_filename": "my_voice.wav"
  }'

# Poll status
curl http://localhost:8005/api/jobs/{job_id}
```

### Other Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/model-info` | Model status and capabilities |
| GET | `/api/reference-audio` | List reference audio files |
| POST | `/api/reference-audio/upload` | Upload reference audio (.wav/.mp3) |
| POST | `/api/chunks/preview` | Preview text chunking |

## License

MIT
