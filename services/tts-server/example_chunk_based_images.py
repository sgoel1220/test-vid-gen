#!/usr/bin/env python3
"""Example: Chunk-based image generation workflow for creepy pasta videos.

This demonstrates the full workflow:
1. Generate TTS audio chunks from story text
2. Group chunks (5-6 per group) using LLM to identify common scenes/settings
3. LLM generates background-only descriptions (no humans, painting style)
4. SDXL generates hand-painted style background images

Usage:
    python3 example_chunk_based_images.py
"""

from __future__ import annotations

import json
from pathlib import Path


# Example creepy pasta story
STORY_TEXT = """
The old house at the end of Maple Street had been abandoned for decades.
Its windows were dark and empty, like hollow eyes staring into nothing.
The paint had long since peeled away, revealing rotting wood beneath.
Weeds choked the overgrown yard, and a rusted gate creaked in the wind.

One night, Sarah decided to investigate. She pushed through the gate,
her flashlight cutting through the darkness. The front door hung open,
inviting her inside. The floorboards groaned under her weight as she
stepped into the musty interior.

Dust motes danced in her flashlight beam. The walls were covered in
peeling wallpaper, depicting faded roses that seemed to watch her.
A grand staircase led up into darkness, its railing cracked and broken.
The air was thick with the smell of decay and something else—something
wrong that she couldn't quite identify.

Sarah heard a sound from upstairs—a slow, deliberate scraping, like
something being dragged across the floor. Her heart pounded as she
climbed the stairs, each step threatening to give way beneath her.
The hallway above was impossibly long, doors lining both sides,
all of them closed except one at the very end.

The scraping grew louder. Sarah approached the open door, her hands
trembling. Inside was a bedroom, frozen in time from decades ago.
A child's toys lay scattered on the floor. The bed was made, as if
waiting for someone who would never return. And in the corner,
barely visible in the shadows, something moved.
"""


def main():
    """Run the chunk-based image generation example."""

    # Step 1: Simulate TTS chunking (in real usage, this comes from TTS engine)
    print("=" * 70)
    print("CHUNK-BASED IMAGE GENERATION EXAMPLE")
    print("=" * 70)

    # For demo, we'll use the text chunking directly
    from text.chunking import chunk_text_by_sentences

    chunks = chunk_text_by_sentences(STORY_TEXT, chunk_size=300)
    print(f"\n✓ Text chunked into {len(chunks)} TTS chunks")
    for i, chunk in enumerate(chunks):
        print(f"  Chunk {i}: {chunk[:60]}...")

    # Step 2: Preview chunk grouping (optional)
    print("\n" + "=" * 70)
    print("PREVIEW: Chunk Grouping")
    print("=" * 70)

    preview_request = {
        "chunks": chunks,
        "chunks_per_group": 5,
        "style": "DARK_ATMOSPHERIC",
    }

    print("\nSending preview request to /api/images/chunks/preview...")
    print("(This will show how chunks are grouped without generating images)")

    # Uncomment to actually call the API:
    # with httpx.Client(base_url="http://localhost:8005") as client:
    #     response = client.post("/api/images/chunks/preview", json=preview_request)
    #     preview = response.json()
    #     print(f"\n✓ Created {preview['total_groups']} chunk groups:")
    #     for group in preview["chunk_groups"]:
    #         print(f"\n  Group {group['group_index']}:")
    #         print(f"    Chunks: {group['chunk_indices']}")
    #         print(f"    Background: {group['background_description'][:100]}...")

    # Step 3: Generate images from chunks
    print("\n" + "=" * 70)
    print("GENERATE: Background Images")
    print("=" * 70)

    gen_request = {
        "chunks": chunks,
        "chunks_per_group": 5,
        "style": "DARK_ATMOSPHERIC",
        "width": 1024,
        "height": 1024,
        "steps": 30,
        "guidance_scale": 7.5,
        "seed": 42,
        "run_label": "creepy_pasta_example",
    }

    print("\nConfiguration:")
    print(f"  • Chunks: {len(chunks)}")
    print(f"  • Target chunks per image: 5")
    print(f"  • Style: DARK_ATMOSPHERIC (hand-painted, no humans)")
    print(f"  • Size: 1024x1024")
    print(f"  • Steps: 30")

    print("\n" + "=" * 70)
    print("To generate images, start the server and run:")
    print("=" * 70)
    print("\n  curl -X POST http://localhost:8005/api/images/chunks/generate \\")
    print("    -H 'Content-Type: application/json' \\")
    print("    -d @request.json")

    # Save request to file for easy testing
    request_file = Path("chunk_image_request.json")
    request_file.write_text(json.dumps(gen_request, indent=2))
    print(f"\n✓ Request saved to: {request_file}")

    print("\n" + "=" * 70)
    print("KEY FEATURES")
    print("=" * 70)
    print("""
1. PAINTING STYLE:
   • Oil painting aesthetic with visible brushstrokes
   • Canvas texture and atmospheric perspective
   • Traditional art quality, not AI-generated look
   • High detail and photorealistic rendering

2. BACKGROUND-ONLY:
   • No humans, faces, or characters
   • Focus on environments: rooms, landscapes, weather
   • Atmospheric settings that match the story mood
   • Negative prompts prevent people/figures

3. INTELLIGENT GROUPING:
   • LLM analyzes 5-6 chunks at a time
   • Groups chunks that share same location/setting
   • Generates one background per group
   • Smooth visual flow for video narration

4. HORROR STYLES:
   • DARK_ATMOSPHERIC: Moody shadows, fog, cinematic
   • COSMIC_HORROR: Lovecraftian void, impossible geometry
   • GOTHIC: Architecture, candlelit, ornate decay
   • PSYCHOLOGICAL: Uncanny, eerie, liminal spaces
   • And more...
    """)


if __name__ == "__main__":
    main()
