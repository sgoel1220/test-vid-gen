// Image Test page — generate images via RunPod ComfyUI Serverless endpoint

let pollTimer: ReturnType<typeof setInterval> | null = null;

export function mount(container: HTMLElement): void {
  container.innerHTML = `
    <div class="section">
      <h2>Image Generation</h2>
      <div class="create-form">
        <div class="form-row">
          <label>Prompt</label>
          <textarea id="img-prompt" rows="3" placeholder="impressionist painting of a dark forest at twilight, thick brushstrokes, moody atmosphere"></textarea>
        </div>
        <div class="form-row">
          <label>Negative Prompt</label>
          <input type="text" id="img-negative" value="photorealistic, photograph, blurry, low quality, watermark, text, deformed">
        </div>
        <div class="form-row-inline">
          <div class="form-field">
            <label>Width</label>
            <input type="text" id="img-width" value="1280">
          </div>
          <div class="form-field">
            <label>Height</label>
            <input type="text" id="img-height" value="720">
          </div>
          <div class="form-field">
            <label>Seed (0 = random)</label>
            <input type="text" id="img-seed" value="0">
          </div>
          <div class="form-field-btn">
            <button class="btn" id="img-generate">Generate</button>
          </div>
        </div>
      </div>
      <div id="img-status" class="muted" style="margin-bottom:0.5rem;font-size:0.85rem;"></div>
      <div id="img-output"></div>
    </div>

    <div class="section" style="margin-top:2rem;">
      <h2>Paste Response</h2>
      <p class="muted" style="font-size:0.8rem;margin-bottom:0.5rem;">Or paste a raw RunPod JSON response to render:</p>
      <div class="form-row">
        <textarea id="img-json" rows="5" placeholder='{"status":"COMPLETED","output":{"images":[...]}}'></textarea>
      </div>
      <button class="btn" id="img-render">Render</button>
      <div id="img-paste-output" style="margin-top:0.75rem;"></div>
    </div>
  `;

  document.getElementById("img-generate")!.addEventListener("click", handleGenerate);
  document.getElementById("img-render")!.addEventListener("click", handleRender);
}

export function unmount(): void {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function handleGenerate(): Promise<void> {
  const prompt = (document.getElementById("img-prompt") as HTMLTextAreaElement).value.trim();
  const negative = (document.getElementById("img-negative") as HTMLInputElement).value.trim();
  const width = parseInt((document.getElementById("img-width") as HTMLInputElement).value) || 1280;
  const height = parseInt((document.getElementById("img-height") as HTMLInputElement).value) || 720;
  const seed = parseInt((document.getElementById("img-seed") as HTMLInputElement).value) || Math.floor(Math.random() * 2 ** 32);

  if (!prompt) { setStatus("Enter a prompt.", "text-err"); return; }

  const workflow = buildWorkflow(prompt, negative, width, height, seed);
  const statusEl = document.getElementById("img-status")!;
  const outputEl = document.getElementById("img-output")!;

  setStatus("Submitting...");
  outputEl.innerHTML = "";

  try {
    const resp = await fetch("/api/image/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workflow }),
    });

    if (!resp.ok) {
      const text = await resp.text();
      setStatus(`Error ${resp.status}: ${text}`, "text-err");
      return;
    }

    const data = await resp.json();

    if (data.status === "COMPLETED") {
      renderOutput(data, outputEl);
      setStatus("Done!");
    } else if (data.id) {
      setStatus(`Job ${data.id} — ${data.status}. Polling...`);
      startPolling(data.id, outputEl);
    } else {
      setStatus("Unexpected response");
      outputEl.innerHTML = `<pre class="code-block">${JSON.stringify(data, null, 2)}</pre>`;
    }
  } catch (err) {
    setStatus(`Network error: ${err}`, "text-err");
  }
}

function startPolling(jobId: string, outputEl: HTMLElement): void {
  if (pollTimer) clearInterval(pollTimer);

  pollTimer = setInterval(async () => {
    try {
      const resp = await fetch(`/api/image/status/${jobId}`);
      if (!resp.ok) return;
      const data = await resp.json();

      if (data.status === "COMPLETED") {
        clearInterval(pollTimer!);
        pollTimer = null;
        renderOutput(data, outputEl);
        setStatus("Done!");
      } else if (data.status === "FAILED") {
        clearInterval(pollTimer!);
        pollTimer = null;
        setStatus(`Failed: ${data.error || "unknown"}`, "text-err");
        outputEl.innerHTML = `<pre class="code-block">${JSON.stringify(data, null, 2)}</pre>`;
      } else {
        setStatus(`Job ${jobId} — ${data.status}...`);
      }
    } catch {
      // ignore transient errors
    }
  }, 3000);
}

function handleRender(): void {
  const raw = (document.getElementById("img-json") as HTMLTextAreaElement).value.trim();
  const outputEl = document.getElementById("img-paste-output")!;
  outputEl.innerHTML = "";

  if (!raw) { outputEl.innerHTML = '<p class="text-err">Paste JSON first.</p>'; return; }

  let data: any;
  try { data = JSON.parse(raw); } catch (e: any) {
    outputEl.innerHTML = `<p class="text-err">Invalid JSON: ${e.message}</p>`;
    return;
  }

  if (data.status && data.status !== "COMPLETED") {
    outputEl.innerHTML = `<p class="muted">Status: <strong>${data.status}</strong> — not completed.</p>`;
    return;
  }

  renderOutput(data, outputEl);
}

function renderOutput(data: any, el: HTMLElement): void {
  const images = extractImages(data);

  if (images.length === 0) {
    el.innerHTML = `<p class="text-err">No images found.</p><pre class="code-block" style="max-height:200px;overflow:auto;">${JSON.stringify(data.output || data, null, 2).slice(0, 1000)}</pre>`;
    return;
  }

  el.innerHTML = images.map((b64, i) => {
    const src = b64.startsWith("data:") ? b64 : `data:image/png;base64,${b64}`;
    return `<img src="${src}" style="max-width:100%;border-radius:6px;border:1px solid #333;margin-bottom:0.5rem;" alt="Generated image ${i + 1}">`;
  }).join("");
}

function extractImages(data: any): string[] {
  const images: string[] = [];
  const output = data.output || data;

  // Format: output.images = [{image: "base64..."}, ...]
  if (Array.isArray(output.images)) {
    for (const img of output.images) {
      if (typeof img === "string") images.push(img);
      else if (img.image) images.push(img.image);
    }
  }
  // Format: output.message = "base64..."
  if (typeof output.message === "string" && output.message.length > 200) {
    images.push(output.message);
  }
  // Format: output is a string
  if (typeof output === "string" && output.length > 200) {
    images.push(output);
  }

  return images;
}

function buildWorkflow(prompt: string, negative: string, width: number, height: number, seed: number): object {
  return {
    "3": { class_type: "KSampler", inputs: { seed, steps: 4, cfg: 2.0, sampler_name: "euler", scheduler: "sgm_uniform", denoise: 1.0, model: ["15", 0], positive: ["6", 0], negative: ["7", 0], latent_image: ["5", 0] } },
    "4": { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "sd_xl_base_1.0.safetensors" } },
    "5": { class_type: "EmptyLatentImage", inputs: { width, height, batch_size: 1 } },
    "6": { class_type: "CLIPTextEncode", inputs: { text: prompt, clip: ["14", 1] } },
    "7": { class_type: "CLIPTextEncode", inputs: { text: negative || "photorealistic, photograph, blurry, low quality, watermark, text, deformed", clip: ["14", 1] } },
    "8": { class_type: "VAEDecode", inputs: { samples: ["3", 0], vae: ["10", 0] } },
    "9": { class_type: "SaveImage", inputs: { filename_prefix: "output", images: ["8", 0] } },
    "10": { class_type: "VAELoader", inputs: { vae_name: "sdxl_vae_fp16_fix.safetensors" } },
    "14": { class_type: "LoraLoader", inputs: { lora_name: "impressionism_sdxl.safetensors", strength_model: 0.8, strength_clip: 0.8, model: ["4", 0], clip: ["4", 1] } },
    "15": { class_type: "LoraLoader", inputs: { lora_name: "sdxl_lightning_4step_lora.safetensors", strength_model: 1.0, strength_clip: 1.0, model: ["14", 0], clip: ["14", 1] } },
  };
}

function setStatus(msg: string, cls: string = ""): void {
  const el = document.getElementById("img-status")!;
  el.textContent = msg;
  el.className = cls || "muted";
}
