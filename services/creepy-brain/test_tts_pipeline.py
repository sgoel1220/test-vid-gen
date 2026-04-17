#!/usr/bin/env python3
"""Test TTS pipeline: normalization -> chunking -> synthesis."""

import asyncio
import os
import sys

# ~5 minutes of audio (~750 words at 150 wpm)
TEST_STORY = """
# The Watcher in the Woods

## Part One: The Arrival

The old cabin had been in the family for three generations, though no one had visited it in over twenty years. When Marcus inherited it from his estranged grandmother, he saw it as an opportunity—a chance to escape the noise of the city and find peace in the mountains.

Dr. Thompson had been explicit about his diagnosis. "You need rest, Marcus. Real rest. Away from screens, away from deadlines, away from everything that's been slowly killing you."

So here he was, driving up a winding gravel road as the autumn sun dipped below the treeline. The GPS had stopped working an hour ago, but the handwritten directions from the family lawyer were surprisingly accurate. "Turn left at the dead oak... follow the creek... the cabin will be on your right."

The structure emerged from the mist like something from another era. Weathered cedar planks, a stone chimney, windows that seemed to watch his approach with dark, knowing eyes. Marcus told himself it was just the fading light playing tricks on him.

The key turned in the lock with surprising ease. Inside, dust motes danced in the beam of his flashlight. The furniture was covered in white sheets, giving everything the appearance of ghosts frozen mid-conversation.

"Home sweet home," he muttered, his voice too loud in the silence.

## Part Two: The First Night

That first night, Marcus couldn't sleep. He blamed it on the unfamiliar bed, the complete absence of city noise, the way the wind seemed to whisper through gaps in the old walls. But in the quiet moments between gusts, he heard something else.

Footsteps. Outside the cabin. Circling.

He told himself it was an animal—a deer, perhaps, or a curious fox. But the rhythm was wrong. Too deliberate. Too patient. Something was walking around his cabin with the measured pace of a sentry.

Marcus grabbed his flashlight and went to the window. The beam cut through the darkness, illuminating trees, bushes, the edge of the creek. Nothing else. But the footsteps continued, always just beyond where he pointed the light.

"Hello?" His voice cracked. "Is someone there?"

The footsteps stopped. The silence that followed was somehow worse.

## Part Three: The Discovery

By the third day, Marcus had convinced himself he'd imagined everything. The mountain air was doing wonders for his anxiety, and he'd started sleeping better. He even began exploring the property, discovering an old well behind the cabin and a root cellar that had been sealed with chains and a heavy padlock.

He found the key hanging on a nail inside the chimney, wrapped in oilcloth to protect it from the elements. His grandmother had left it there for someone to find. For him to find.

The root cellar smelled of earth and something else—something old and wrong. His flashlight revealed shelves lined with mason jars, their contents long since rotted to black sludge. But in the back, hidden behind the shelves, was a doorway.

It shouldn't have been there. The cellar was too small for another room. And yet.

## Part Four: The Truth

The hidden room contained journals—dozens of them, filled with his grandmother's handwriting. As Marcus read, the truth of his inheritance became horrifyingly clear.

The cabin wasn't a retreat. It was a prison. And his grandmother hadn't been its caretaker—she had been its warden.

"It requires a host," the final entry read. "Someone of the bloodline. I've grown too old, too weak. It's been patient, waiting for the next one. If you're reading this, I'm sorry. I'm so sorry. But you can never leave. None of us can ever leave."

Marcus closed the journal with trembling hands. Outside, the footsteps had returned. But now they weren't circling the cabin. They were climbing the cellar stairs. Coming for him.

He understood now why his grandmother had sent the inheritance to him specifically. Why his parents had never spoken of this place. Why the family had scattered across the country, putting as much distance as possible between themselves and these woods.

But blood called to blood. And the thing in the forest had been waiting a very long time.

As the cellar door creaked open, Marcus finally saw what had been watching him all along. In that moment, he understood that some inheritances cannot be refused. Some debts must be paid in full.

The watcher had finally found its new home.
"""


async def main() -> None:
    # Add app to path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    from app.config import settings
    from app.text.normalization import normalize_text
    from app.text.chunking import chunk_text_by_sentences
    from app.audio.validation import validate_chunk_audio
    from app.gpu import get_provider, GpuPodSpec
    import httpx

    print("=" * 60)
    print("TTS Pipeline Test")
    print("=" * 60)

    # Step 1: Normalize
    print("\n[1/5] Normalizing text...")
    print(f"  Input length: {len(TEST_STORY)} chars")
    normalized = await normalize_text(TEST_STORY)
    print(f"  Output length: {len(normalized)} chars")
    print(f"  Preview: {normalized[:200]}...")

    # Step 2: Chunk
    print("\n[2/5] Chunking text...")
    chunks = chunk_text_by_sentences(normalized, chunk_size=settings.tts_chunk_size)
    print(f"  Created {len(chunks)} chunks")
    for i, chunk in enumerate(chunks):
        print(f"    Chunk {i+1}: {len(chunk)} chars")

    # Step 3: Start GPU pod
    print("\n[3/5] Starting GPU pod...")
    provider = get_provider(settings.runpod_api_key)
    spec = GpuPodSpec.from_config()
    print(f"  GPU type: {spec.gpu_type}")
    print(f"  Image: {spec.image}")

    pod = await provider.create_pod(spec, idempotency_key="tts-test")
    print(f"  Pod ID: {pod.id}")
    print(f"  Status: {pod.status.value}")

    # Step 4: Wait for ready
    print("\n[4/5] Waiting for pod to be ready...")
    try:
        pod = await provider.wait_for_ready(pod.id, timeout_sec=600)  # 10 minutes for cold start
        print(f"  Pod ready at: {pod.endpoint_url}")
    except TimeoutError:
        print("  ERROR: Pod did not become ready in time")
        await provider.terminate_pod(pod.id)
        return

    # Step 5: Synthesize all chunks
    print("\n[5/5] Synthesizing audio...")
    print(f"  Voice: {settings.tts_default_voice}")
    print(f"  Seed: {settings.tts_seed}")
    print(f"  Exaggeration: {settings.tts_exaggeration}")
    print(f"  CFG Weight: {settings.tts_cfg_weight}")
    print(f"  Temperature: {settings.tts_temperature}")

    output_dir = "/tmp/tts_test_output"
    os.makedirs(output_dir, exist_ok=True)

    total_duration = 0.0
    validated_chunks = 0
    failed_chunks = 0
    async with httpx.AsyncClient(base_url=pod.endpoint_url, timeout=120.0) as client:
        for i, chunk in enumerate(chunks):
            print(f"  Synthesizing chunk {i+1}/{len(chunks)}...", end=" ", flush=True)
            try:
                resp = await client.post(
                    "/synthesize",
                    json={
                        "text": chunk,
                        "voice": settings.tts_default_voice,
                        "seed": settings.tts_seed,
                        "exaggeration": settings.tts_exaggeration,
                        "cfg_weight": settings.tts_cfg_weight,
                        "temperature": settings.tts_temperature,
                        "repetition_penalty": settings.tts_repetition_penalty,
                        "min_p": settings.tts_min_p,
                        "top_p": settings.tts_top_p,
                    }
                )
                resp.raise_for_status()

                # Validate the audio chunk
                validation = validate_chunk_audio(resp.content)
                if not validation.passed:
                    print(f"VALIDATION FAILED: {validation.failure_reason}")
                    failed_chunks += 1
                    continue

                validated_chunks += 1
                # Save to file
                output_path = f"{output_dir}/chunk_{i+1:03d}.wav"
                with open(output_path, "wb") as f:
                    f.write(resp.content)

                total_duration += validation.duration_sec
                print(f"OK ({validation.duration_sec:.1f}s, rms={validation.rms:.4f}, peak={validation.peak_amplitude:.4f}, voiced={validation.voiced_ratio:.1%})")

            except Exception as e:
                print(f"FAILED: {e}")

    # Cleanup
    print("\n[Cleanup] Terminating pod...")
    await provider.terminate_pod(pod.id)
    print("  Pod terminated")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Chunks processed: {len(chunks)}")
    print(f"  Validated: {validated_chunks}, Failed: {failed_chunks}")
    print(f"  Total duration: {total_duration:.1f}s ({total_duration/60:.1f} minutes)")
    print(f"  Output files: {output_dir}/chunk_*.wav")
    print("\nTo concatenate all chunks:")
    print(f"  sox {output_dir}/chunk_*.wav {output_dir}/full_story.wav")


if __name__ == "__main__":
    asyncio.run(main())
