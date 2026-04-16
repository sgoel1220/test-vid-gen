# Chunk-Based Image Generation for Creepy Pasta Videos

This feature generates hand-painted style background images automatically from TTS chunks, designed specifically for creepy pasta narration videos.

## Overview

The workflow:
1. **TTS Chunking**: Story text is split into audio chunks (already done by TTS engine)
2. **Intelligent Grouping**: LLM groups 5-6 chunks that share a common scene/setting
3. **Background Description**: LLM generates painting-style background descriptions (NO humans)
4. **Image Generation**: SDXL creates hand-painted, realistic background images

## Key Features

### 🎨 Painting-Style Quality
- **Hand-painted aesthetic**: Visible brushstrokes, canvas texture
- **Traditional art feel**: Not AI-generated or computer graphics
- **Photorealistic rendering**: High detail with atmospheric perspective
- **Oil painting style**: Classic fine art quality

### 🚫 Background-Only (No Humans)
- Strict enforcement: No people, faces, characters, or figures
- Focus on **environments**: rooms, landscapes, buildings, nature
- Atmospheric **settings**: weather, lighting, mood, colors
- Comprehensive negative prompts prevent any human elements

### 🧠 Intelligent Chunk Grouping
- **LLM-powered analysis**: Groups chunks that share the same location
- **Smart scene detection**: Identifies when setting changes
- **Typical grouping**: 5-6 chunks → 1 background image
- **Smooth visual flow**: Perfect for video narration

### 🎭 Multiple Horror Styles

| Style | Description |
|-------|-------------|
| **DARK_ATMOSPHERIC** | Moody shadows, fog, desaturated colors, dramatic lighting |
| **COSMIC_HORROR** | Lovecraftian void, impossible geometry, eldritch atmosphere |
| **GOTHIC** | Gothic architecture, candlelit, ornate decay, stained glass |
| **PSYCHOLOGICAL** | Uncanny stillness, liminal spaces, eerie isolation |
| **SURREAL_NIGHTMARE** | Distorted reality, dreamlike, Dali-esque composition |
| **FOUND_FOOTAGE** | Grainy, VHS distortion, night vision, documentary feel |
| **FOLK_HORROR** | Rural decay, ancient rituals, pagan symbols, twilight |
| **BODY_HORROR** | Biomechanical environments, organic architecture, visceral |

## API Endpoints

### 1. Preview Chunk Grouping
**GET** `/api/images/chunks/preview`

See how chunks will be grouped without generating images.

**Request:**
```json
{
  "chunks": ["First chunk...", "Second chunk...", "Third chunk..."],
  "chunks_per_group": 5,
  "style": "DARK_ATMOSPHERIC"
}
```

**Response:**
```json
{
  "chunk_groups": [
    {
      "group_index": 0,
      "chunk_indices": [0, 1, 2, 3, 4],
      "background_description": "Abandoned house exterior, overgrown yard...",
      "num_chunks": 5
    }
  ],
  "total_groups": 3,
  "chunks_processed": 15
}
```

### 2. Generate Images (Synchronous)
**POST** `/api/images/chunks/generate`

Generate background images from TTS chunks (blocks until complete).

**Request:**
```json
{
  "chunks": ["Chunk 1 text...", "Chunk 2 text..."],
  "chunks_per_group": 5,
  "style": "DARK_ATMOSPHERIC",
  "width": 1024,
  "height": 1024,
  "steps": 30,
  "guidance_scale": 7.5,
  "seed": 42,
  "run_label": "my_story"
}
```

**Response:**
```json
{
  "run_id": "20260416_123456__my_story__abc123",
  "output_dir": "/path/to/outputs/image_gen_runs/...",
  "scenes": [
    {
      "scene_index": 0,
      "text_segment": "Chunks 0-4",
      "prompt": "Abandoned Victorian house, overgrown yard, oil painting style...",
      "negative_prompt": "people, humans, faces..."
    }
  ],
  "images": [
    {
      "filename": "scene_000.png",
      "url": "/outputs/image_gen_runs/.../scene_000.png",
      "width": 1024,
      "height": 1024,
      "prompt_used": "...",
      "seed_used": 42
    }
  ],
  "manifest_url": "/outputs/image_gen_runs/.../manifest.json"
}
```

### 3. Generate Images (Async)
**POST** `/api/images/chunks/jobs`

Create background job, poll for results.

**Request:** Same as synchronous endpoint

**Response:**
```json
{
  "job_id": "abc123def456",
  "status_url": "/api/images/jobs/abc123def456"
}
```

**Poll Status:**
```bash
curl http://localhost:8005/api/images/jobs/abc123def456
```

## Example Usage

### Python Example
```python
import httpx

# Your TTS chunks (from TTS engine)
chunks = [
    "The old house at the end of the street...",
    "Its windows were dark and empty...",
    "Sarah approached the creaking gate...",
    # ... 10-15 more chunks
]

# Generate background images
with httpx.Client(base_url="http://localhost:8005") as client:
    response = client.post("/api/images/chunks/generate", json={
        "chunks": chunks,
        "chunks_per_group": 5,
        "style": "DARK_ATMOSPHERIC",
        "width": 1024,
        "height": 1024,
        "steps": 30,
        "seed": 42,
        "run_label": "creepy_pasta_episode_1"
    })

    result = response.json()
    print(f"Generated {len(result['images'])} background images")
    print(f"Output: {result['output_dir']}")
```

### cURL Example
```bash
# Save request to file
cat > request.json << 'EOF'
{
  "chunks": [
    "The old house stood silent...",
    "Broken windows stared like empty eyes..."
  ],
  "chunks_per_group": 5,
  "style": "DARK_ATMOSPHERIC",
  "width": 1024,
  "height": 1024
}
EOF

# Generate images
curl -X POST http://localhost:8005/api/images/chunks/generate \
  -H "Content-Type: application/json" \
  -d @request.json
```

## Workflow Integration

### Complete Creepy Pasta Pipeline

```python
# Step 1: Generate TTS audio
tts_response = client.post("/tts", json={
    "text": story_text,
    "reference_audio": "creepy_narrator.wav"
})

chunks = tts_response.json()["chunks"]  # Get text chunks used
audio_files = tts_response.json()["audio_files"]

# Step 2: Generate background images from the same chunks
image_response = client.post("/api/images/chunks/generate", json={
    "chunks": [c["text"] for c in chunks],
    "chunks_per_group": 5,
    "style": "DARK_ATMOSPHERIC"
})

# Step 3: Combine audio + images in video editor
# - Each image covers 5-6 audio chunks
# - Smooth transitions between backgrounds
# - Perfect sync between narration and visuals
```

## Configuration

### Image Quality
- **Steps**: 30-50 (higher = better quality, slower)
- **Guidance Scale**: 7-9 (controls prompt adherence)
- **Resolution**: 1024x1024 recommended (SDXL native)

### Grouping
- **chunks_per_group**: 5-6 typical (adjust based on story pacing)
- LLM will adjust grouping based on scene changes
- Shorter chunks = more images, better scene matching
- Longer chunks = fewer images, faster generation

### VRAM Management
- TTS model auto-unloads before image generation
- SDXL loads (requires ~6.5GB VRAM)
- After images generated, TTS reloads automatically
- Single GPU constraint handled seamlessly

## Technical Details

### Prompt Engineering
**Positive Prompts Include:**
- Base scene description from LLM
- Painting style modifiers: oil painting, brushstrokes, canvas texture
- Traditional art quality: atmospheric perspective, fine art
- Style-specific atmosphere: dark moody, fog, shadows, etc.

**Negative Prompts Include:**
```
people, humans, faces, characters, person, man, woman, child,
figure, portrait, body, hands, eyes,
low quality, blurry, text, watermark, logo, signature,
bright, cheerful, cartoon, anime, 3d render, cgi,
artificial, digital art, computer generated, pixelated
```

### LLM Processing
- Uses Qwen/Qwen2.5-1.5B-Instruct (same as TTS normalization)
- Analyzes chunks to identify common settings
- Generates background-only scene descriptions
- Enforces "no humans" rule in prompts

### Caching
- Scene prompts cached by content hash
- Chunk groupings cached to avoid re-analysis
- Cache location: `outputs/scene_prompt_cache/`

## Troubleshooting

### Images have people/characters
- Check negative prompt is applied
- Increase guidance_scale (8-10)
- Try different style (PSYCHOLOGICAL enforces isolation)

### Images don't look like paintings
- Verify _PAINTING_STYLE_BASE is in prompts
- Increase steps to 40-50
- Check style suffix is applied correctly

### Grouping is wrong
- Adjust chunks_per_group (try 4 or 6)
- Check LLM is loading correctly
- Review chunk text quality (are they meaningful?)

### VRAM errors
- TTS should auto-unload (check logs)
- Reduce image resolution (512x512 uses less VRAM)
- Wait 2-3 seconds between generations

## Example Output

With the sample creepy pasta story, you'll get ~3-4 background images:

1. **Image 1** (Chunks 0-4): Abandoned house exterior, overgrown yard, rusty gate
2. **Image 2** (Chunks 5-9): Dark interior hallway, peeling wallpaper, dusty atmosphere
3. **Image 3** (Chunks 10-14): Grand staircase leading up into darkness
4. **Image 4** (Chunks 15+): Empty bedroom frozen in time, toys scattered

All in hand-painted style, no people, perfect for video backgrounds.

## See Also

- `example_chunk_based_images.py` - Full working example
- `image/chunk_grouper.py` - LLM grouping implementation
- `image/prompts.py` - Painting-style prompt engineering
- `image_routes.py` - API endpoint implementations
