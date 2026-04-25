# SDXL RunPod Server

Production-ready SDXL 1.0 image generation server with LoRA support, built for RunPod GPU instances.

## What's inside

| Component | Detail |
|-----------|--------|
| Base image | `nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04` |
| Python | 3.11 |
| Framework | FastAPI + Uvicorn |
| Models | SDXL 1.0 Base + Refiner |
| Attention | xformers memory-efficient attention |
| LoRA | Hot-swap without model reload |

## API

### `POST /generate`

```json
{
  "prompt": "a photo of a cat astronaut",
  "negative_prompt": "blurry, low quality",
  "loras": [{"name": "my-lora", "weight": 0.8}],
  "width": 1344,
  "height": 768,
  "steps": 30,
  "cfg": 6.0,
  "seed": 42,
  "refiner_denoise": 0.35
}
```

Returns:

```json
{
  "image_b64": "<base64 PNG>",
  "seed": 42,
  "elapsed_seconds": 8.3,
  "width": 1344,
  "height": 768,
  "steps": 30,
  "cfg": 6.0,
  "loras_applied": ["my-lora"]
}
```

All fields except `prompt` are optional. Defaults: `width=1344`, `height=768`, `steps=30`, `cfg=6.0`, `refiner_denoise=0.35`.

### `GET /health`

```json
{
  "status": "ok",
  "device": "cuda",
  "base_loaded": true,
  "refiner_loaded": true,
  "loras_available": 3,
  "cuda_memory_allocated_gb": 12.4
}
```

### `GET /loras`

Returns list of registered LoRA adapters:

```json
[{"name": "my-lora", "path": "/loras/my-lora.safetensors"}]
```

### `POST /reload-loras`

Rescans `/loras` directory and updates the registry without restarting the server. Useful after adding new `.safetensors` files.

---

## Local development

```bash
# 1. Copy .env file
cp .env.example .env
# Edit .env and set HF_TOKEN

# 2. Place .safetensors LoRA files in ./loras/

# 3. Build and start
docker compose up --build

# 4. Check health
curl http://localhost:8000/health

# 5. Generate
curl -s -X POST http://localhost:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"a golden retriever on the moon","steps":20}' \
  | python3 -c "
import sys, json, base64
r = json.load(sys.stdin)
open('out.png','wb').write(base64.b64decode(r['image_b64']))
print(f'Saved out.png  seed={r[\"seed\"]}  {r[\"elapsed_seconds\"]}s')
"
```

---

## RunPod Deployment

### 1. Build and push your image

```bash
docker build -t your-dockerhub-user/sdxl-server:latest .
docker push your-dockerhub-user/sdxl-server:latest
```

Or use the GitHub Actions workflow below to push to GHCR.

### 2. Create a RunPod Template

1. Log into [RunPod](https://www.runpod.io) → **Templates** → **New Template**.
2. Fill in:

   | Field | Value |
   |-------|-------|
   | **Container Image** | `your-dockerhub-user/sdxl-server:latest` |
   | **Container Disk** | 20 GB (code + Python packages) |
   | **Volume Disk** | 60+ GB (model weights ~30 GB + room for LoRAs) |
   | **Volume Mount Path** | `/root/.cache/huggingface` |
   | **Expose HTTP Port** | `8000` |

3. Under **Environment Variables**, add:

   | Key | Value |
   |-----|-------|
   | `HF_TOKEN` | `hf_xxxxxxxxxxxxxxxxxxxx` *(your token)* |
   | `LORAS_DIR` | `/loras` *(optional, default)* |

   > **Security note:** RunPod environment variables in template settings are stored encrypted and injected at runtime — they are not baked into the image.

4. Click **Save Template**.

### 3. Launch a Pod

1. Go to **Pods** → **Deploy**.
2. Select a GPU with ≥ 24 GB VRAM (A100, RTX 4090, A40, or similar). SDXL base + refiner together use ~18–22 GB at fp16.
3. Choose your template.
4. Click **Deploy On-Demand** (or **Spot** for lower cost).

### 4. Access the endpoint

Once the pod is running, RunPod exposes your HTTP port as:

```
https://<pod-id>-8000.proxy.runpod.net
```

You can find the URL in the pod's **Connect** dialog. Test with:

```bash
curl https://<pod-id>-8000.proxy.runpod.net/health
```

### 5. Mounting LoRAs

Option A — **Bake into image**: Add `.safetensors` files to `./loras/` before building.

Option B — **RunPod Network Volume**: Create a Network Volume, upload your `.safetensors` files, and mount it at `/loras` in the template's volume settings.

Option C — **SSH upload**: Use RunPod's SSH tunnel to `scp` files directly to `/loras/` on the running pod, then call `POST /reload-loras` to register them.

---

## Baking model weights into the image (optional)

If you have a private registry and want zero cold-start time, build with models pre-cached:

```bash
docker build \
  --build-arg BUILD_MODELS=1 \
  --build-arg HF_TOKEN=hf_xxx \
  -t your-user/sdxl-server:baked .
```

This adds ~30 GB to the image but eliminates the download on first start.

---

## GPU memory guide

| GPU | VRAM | Notes |
|-----|------|-------|
| RTX 4090 | 24 GB | Fits base + refiner fp16, recommended |
| A40 | 48 GB | Comfortable headroom |
| A100 40 GB | 40 GB | Production tier |
| RTX 3090 | 24 GB | Tight but works |
| RTX 4080 | 16 GB | **Not enough** for both models simultaneously |

For 16 GB GPUs you would need to use CPU offloading (`enable_model_cpu_offload`) — not recommended for latency-sensitive workloads.
