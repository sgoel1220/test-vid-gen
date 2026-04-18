# Future Scope

Ideas and features deferred for future implementation.

---

## Audio Loudness Normalization

**Goal:** Normalize TTS chunk loudness to a consistent target before stitching, ensuring uniform volume across the final production regardless of voice or content.

### Approach

- Use `pyloudnorm` (same EBU R128 algorithm as Audacity's Loudness Normalization — identical quality)
- Apply per-chunk after synthesis, before stitching in `audio/stitching.py`
- Target: **-23 LUFS** (broadcast) or **-16 LUFS** (podcast)

```python
import pyloudnorm as pyln

meter = pyln.Meter(sample_rate)
loudness = meter.integrated_loudness(audio_data)
normalized = pyln.normalize.loudness(audio_data, loudness, -23.0)
```

Add `pyloudnorm` to `creepy-brain/pyproject.toml` dependencies.

---

## Audio Sound Effects (SFX) Integration

**Goal:** Automatically mix ambient sound effects into generated audio to enhance immersion for creepy pasta productions (e.g. door creaking, car idling, low hum).

### Approach

1. **One-time download** — curate ~100 royalty-free SFX clips from sources like [Freesound.org](https://freesound.org), [BBC Sound Effects](https://sound-effects.bbcrewind.co.uk/), or [Zapsplat](https://www.zapsplat.com) and store them locally in `services/tts-server/audio/sfx/`.
2. **LLM annotation** — add a step in the story-engine pipeline where the LLM tags script moments with SFX cues, e.g. `[SFX: doors/creak_slow]`.
3. **Stitcher mixing** — detect cue markers in `stitch_audio_chunks` (`audio/stitching.py`) and mix in the matching clip using `pydub`.

### Suggested SFX Library Structure

```
audio/sfx/
├── ambience/    # wind, rain, forest, basement hum
├── doors/       # creak, slam, knock, lock click
├── footsteps/   # wood, gravel, wet floor
├── vehicles/    # car idle, distant engine, tires on gravel
├── horror/      # heartbeat, breathing, whisper static
└── weather/     # thunder, rain on glass, wind howl
```

### SFX Sources (one-time download, no per-use cost)

| Source | License | Notes |
|--------|---------|-------|
| [Freesound.org](https://freesound.org) | CC0 / CC-BY | API available for bulk download script |
| [BBC Sound Effects](https://sound-effects.bbcrewind.co.uk/) | Personal/non-commercial | Professional quality |
| [Pixabay Sound Effects](https://pixabay.com/sound-effects/) | Royalty-free, no attribution | Good variety |
| [Zapsplat](https://www.zapsplat.com) | Royalty-free with free account | Professional quality |

### Alternative: AI-Generated SFX (on-demand, paid)

If the pre-recorded library proves too limiting, AI generation APIs can synthesize SFX from text prompts:

- [ElevenLabs Sound Effects API](https://elevenlabs.io/sound-effects) — text → audio, up to 20s, 48kHz, loopable
- [fal.ai / CassetteAI](https://fal.ai/models/cassetteai/sound-effects-generator/api) — text → WAV, ~1s latency
- [fal.ai / Beatoven](https://fal.ai/models/beatoven/sound-effect-generation/api) — diffusion model, 1–35s, 44.1kHz stereo
- [Stability AI Stable Audio](https://stability.ai/stable-audio) — text → SFX or music

### Implementation Notes

- The pre-recorded library approach is preferred: zero runtime cost, instant lookup, fully deterministic.
- Freesound has a Python-friendly REST API — a one-time bulk download script can automate curation.
- `pydub` (likely already a dependency) handles audio mixing in ~10 lines.
- LLM cue format should be simple and structured: `[SFX: category/clip_name]` embedded in the script text.
