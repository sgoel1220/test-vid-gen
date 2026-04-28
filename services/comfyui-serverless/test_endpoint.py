"""Quick test script for the RunPod ComfyUI serverless endpoint.

Usage:
    RUNPOD_API_KEY=xxx RUNPOD_ENDPOINT_ID=yyy python test_endpoint.py
    RUNPOD_API_KEY=xxx RUNPOD_ENDPOINT_ID=yyy python test_endpoint.py "a stormy sea at sunset"
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

import httpx

API_KEY = os.environ["RUNPOD_API_KEY"]
ENDPOINT_ID = os.environ["RUNPOD_ENDPOINT_ID"]
BASE_URL = f"https://api.runpod.ai/v2/{ENDPOINT_ID}"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

WORKFLOW_PATH = Path(__file__).parent / "workflow_api.json"


def make_workflow(prompt: str, negative_prompt: str = "", seed: int = 0) -> dict[str, object]:
    """Load workflow_api.json and inject prompt/seed."""
    wf = json.loads(WORKFLOW_PATH.read_text())
    wf["6"]["inputs"]["text"] = prompt
    if negative_prompt:
        wf["7"]["inputs"]["text"] = negative_prompt
    wf["3"]["inputs"]["seed"] = seed
    return wf


def run_sync(prompt: str) -> None:
    """Submit job and wait for result (runsync, up to 120s)."""
    workflow = make_workflow(prompt, seed=int(time.time()) % 2**32)
    payload = {"input": {"workflow": workflow}}

    print(f"Submitting to {BASE_URL}/runsync ...")
    t0 = time.time()

    resp = httpx.post(
        f"{BASE_URL}/runsync",
        headers=HEADERS,
        json=payload,
        timeout=180.0,
    )
    resp.raise_for_status()
    result = resp.json()
    elapsed = time.time() - t0

    status = result.get("status")
    print(f"Status: {status} ({elapsed:.1f}s)")

    if status == "COMPLETED":
        output = result.get("output", {})
        images = output.get("images", [])
        if images:
            img_data = images[0]
            if isinstance(img_data, dict) and "image" in img_data:
                png_bytes = base64.b64decode(img_data["image"])
                out_path = Path("test_output.png")
                out_path.write_bytes(png_bytes)
                print(f"Saved: {out_path} ({len(png_bytes)} bytes)")
            else:
                print(f"Image data format: {type(img_data)}")
        else:
            print(f"Output keys: {list(output.keys()) if isinstance(output, dict) else output}")
    else:
        print(f"Full response: {json.dumps(result, indent=2)}")


if __name__ == "__main__":
    prompt = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "impressionist painting of a dark forest at twilight, thick brushstrokes, moody atmosphere"
    )
    run_sync(prompt)
