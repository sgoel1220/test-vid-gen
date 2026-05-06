# Image Generation Guide — ComfyUI Horror Painting Style

## Workflow File

`comfyui-sdxl-lightning-impressionism.json` — load via drag-and-drop in ComfyUI (full graph format).

## Model Stack

| Component | File | Weight | Role |
|-----------|------|--------|------|
| **Checkpoint** | `albedobase_xl_v31.safetensors` | — | AlbedoBase XL v3.1 base model |
| **Detail Tweaker XL** | `detail_tweaker_xl.safetensors` | 1.0 / 1.0 | Adds detail refinement |
| **xl_more_art-full** | `xl_more_art_full.safetensors` | 0.7 / 0.7 | Pushes artistic/aesthetic look |
| **Midjourney Mimic** | `midjourney_mimic.safetensors` | 0.6 / 0.6 | Color/composition enhancer |
| **Impressionism** | `impressionism_sdxl.safetensors` | 0.4 / 0.4 | Oil painting texture, trigger: `monet` |
| **Andreas Achenbach** | `andreas_achenbach_sdxl.safetensors` | 0.4 / 0.4 | Dramatic landscape style, trigger: `style of Andreas Achenbach` |

All commercially licensed for image generation. See license details below.

**WARNING:** Do not change LoRA weights — the tested values work well together. Changing them causes hallucinations.

## Sampler Settings

| Param | Value |
|-------|-------|
| Steps | 34 |
| CFG | 2 |
| Sampler | dpmpp_2m |
| Scheduler | karras |
| Resolution | 1216x832 (landscape) |
| Seed control | fixed (for comparing), randomize (for variety) |
| Denoise | 1.0 |

## Prompt Formula

```
cinematic [SHORT SCENE DESCRIPTION], loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting
```

### Rules
1. Always start with `cinematic`
2. Keep scene description **short** — let the LoRAs do the work
3. Focus on atmosphere: lighting, shadows, fog, mood
4. Always end with: `loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting`
5. Do NOT over-describe details — too many specifics cause hallucinations
6. No people/characters in scene descriptions

### Negative Prompt (always use this)

```
deformed, ng_deepnegative_v1_75t, (deformed, distorted, disfigured:1.5), (mutated hands and fingers:1.5), monochrome background, furry, loli, poorly drawn, bad anatomy, wrong anatomy, extra limbs, missing limb, floating limbs, missing fingers, elongated hands, disconnected limbs, mutation, mutated, ugly, disgusting, blurry, blurry eyes, background characters, muscular, smooth, clean, minimalist, sleek, modern, photorealistic, sharp details, hyperdetailed, fine details, smooth rendering, digital art
```

## Example Prompts (tested, good results)

```
cinematic haunted manor hallway, dusty portraits on walls, single candle flickering, long shadows, loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting

cinematic old decaying clock tower interior, dusty gears, flickering candlelight, deep shadows, thick fog, loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting

cinematic abandoned cathedral nave, shattered stained glass, single candle on altar, long shadows across stone floor, loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting

cinematic old graveyard path at night, crooked tombstones fading into fog, distant lantern glow between dead trees, loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting

cinematic torch-lit dungeon corridor, wet stone walls, chains hanging from ceiling, darkness swallowing the far end, loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting

cinematic decaying library interior, towering dusty bookshelves, single oil lamp on reading desk, cobwebs and long shadows, loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting

cinematic stormy coastline at night, shipwreck on jagged rocks, lighthouse beam cutting through rain, loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting

cinematic foggy swamp at dusk, gnarled trees rising from black water, faint lantern floating in the mist, loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting

cinematic abandoned ballroom, shattered chandelier, moonlight through broken windows, dust in the air, loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting

cinematic old clock tower staircase spiraling upward, cracked walls, faint candlelight from above, deep shadows below, loose brushstrokes, impasto texture, vibring color, style of Andreas Achenbach, monet, oil painting
```

## Troubleshooting

- **Too bright?** Add `dark moody atmosphere, deep shadows, low key lighting` to scene description
- **Hallucinations / weird artifacts?** Shorten the scene description — fewer details = fewer artifacts
- **Too AI-looking?** Add `rough canvas` to prompt. Do NOT increase Detail Tweaker or Midjourney Mimic weights
- **Want to compare changes?** Set seed control to `fixed` in KSampler
- **Missing models error after restart?** Hit Refresh in ComfyUI Manager to rescan model directories

## Download Commands

All models go on the cloud machine under `/workspace/ComfyUI/models/`.

```bash
# Checkpoint → models/checkpoints/
wget -O /workspace/ComfyUI/models/checkpoints/albedobase_xl_v31.safetensors \
  "https://civitai.com/api/download/models/281176?token=YOUR_TOKEN"

# LoRAs → models/loras/
wget -O /workspace/ComfyUI/models/loras/detail_tweaker_xl.safetensors \
  "https://civitai.com/api/download/models/135867?token=YOUR_TOKEN"

wget -O /workspace/ComfyUI/models/loras/xl_more_art_full.safetensors \
  "https://civitai.com/api/download/models/152309?token=YOUR_TOKEN"

wget -O /workspace/ComfyUI/models/loras/midjourney_mimic.safetensors \
  "https://civitai.com/api/download/models/283697?token=YOUR_TOKEN"

wget -O /workspace/ComfyUI/models/loras/impressionism_sdxl.safetensors \
  "https://civitai.com/api/download/models/133465?token=YOUR_TOKEN"

wget -O /workspace/ComfyUI/models/loras/andreas_achenbach_sdxl.safetensors \
  "https://civitai.com/api/download/models/510042?token=YOUR_TOKEN"
```

Replace `YOUR_TOKEN` with your CivitAI API key from https://civitai.com/user/account → API Keys.

## Commercial Licensing

| Component | License | Commercial Image Gen |
|-----------|---------|---------------------|
| AlbedoBase XL v3.1 | CreativeML Open RAIL++-M | Yes |
| Detail Tweaker XL | Custom (permissive) | Yes |
| xl_more_art-full | Custom (no derivatives, image gen OK) | Yes |
| Midjourney Mimic v1.2 | CreativeML Open | Yes |
| Impressionism | Custom (permissive) | Yes |
| Andreas Achenbach | Custom (permissive) | Yes |
