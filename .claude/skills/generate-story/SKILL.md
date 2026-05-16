# Skill: generate-story

Generate a horror story optimized for YouTube audio narration, save it locally, and push it to the server.

---

## Step 0 — Determine premise

If `$ARGUMENTS` is set, use it as the story premise.

Otherwise, generate a fresh, specific premise. Choose something rooted in mundane reality that twists into horror — a plumber finding something behind a wall, a night-shift security guard, an elderly neighbor's unusual routine, a childhood memory that now feels wrong. Make it concrete, not vague.

---

## Step 1 — Optional: web research

If the premise could benefit from grounding in real-world details (a specific profession, a real location type, a historical event), use the WebSearch or WebFetch tool to gather 2-3 concrete details that will make the story feel authentic. Skip this if the premise is already grounded.

---

## Step 2 — Write the story

Write a complete horror story following these rules:

**Length:** 5000–7000 words (long-form for audio; ~50–70 min narration at TTS pace)

**NARRATOR_VOICE_RULES (embed all of these):**
- Single narrator: old male, first-person past tense ("I remember when…", "That was the night I…")
- No dialogue from other characters — narrator may paraphrase what others said, never quote directly
- Short paragraphs: 2–4 sentences each. One-sentence paragraphs for emphasis only.
- Sound-based descriptions preferred over visual ones (footsteps, breathing, the creak of a door)
- Lingering unease over jump scares — the horror should grow slowly
- Never name the monster/entity/force explicitly — let the listener fill the gap
- Sentence rhythm matters for audio: vary length. Short. Then longer flowing sentences that build. Then short again.
- No chapter breaks, no section headers — single flowing narrative
- This is AUDIO ONLY — no references to visual layout, "as mentioned above", or spatial page concepts. The listener hears a continuous stream.

**TTS-Friendly Text Rules (the engine reads this raw — avoid tripping it up):**
- Spell out all numbers: "three" not "3", "nineteen eighty-four" not "1984"
- No ALL-CAPS words (TTS may spell them letter-by-letter). Use prose emphasis instead: "It was *wrong*" → "It was deeply, fundamentally wrong."
- No parenthetical asides — restructure as separate sentences
- Keep sentences under ~40 words. The TTS chunks text at ~300 characters; very long sentences get split at unnatural points.
- Use simple, phonetically unambiguous character/place names (2-3 syllables, common pronunciation). Avoid names the TTS might mangle (Siobhan, Cthulhu, Xochitl).
- No URLs, emails, code, or non-prose content

**TTS Prosody Cues (critical — these directly control how the voice sounds):**

The story text is fed directly into a TTS engine with no post-processing. Punctuation and sentence structure ARE the voice direction. Use these intentionally:

- **Ellipses `...`** → audible pause, hesitation. Use before reveals, after wrong details, when narrator trails off in fear. *"The door was open... it hadn't been open before."*
- **Em-dashes `—`** → abrupt break, interrupted thought. Use when the narrator catches themselves, stops mid-sentence, or recoils. *"I reached for the handle — and then I heard it."*
- **Sentence fragments** → staccato delivery, panic. Use at peak horror moments. *"Nothing. Just silence. Then the smell."*
- **Stuttering/repetition** → voice trembling. Use sparingly at peak fear. *"It was — it was looking at me. It had been looking at me the whole time."*
- **Short paragraph after long one** → the voice drops, lands heavy. Use for the key horror beat after a build.

**Prosody anti-patterns (do NOT do these):**
- No more than 2 ellipses per paragraph — overuse sounds glitchy
- Stuttering/repetition max 2–3 times in the entire story — more sounds like a speech impediment
- Never stack fragments for more than 3 consecutive sentences — it loses impact
- Don't use em-dashes in the calm opening sections — save them for when tension exists
- Don't start multiple consecutive paragraphs with the same prosody device

**Intensity mapping (match prose structure to story position):**
- Hook (first 3 sentences): One short punchy sentence allowed to set tension, then ease into flowing prose.
- Opening 5–20%: Long sentences, flowing prose, no fragments. Calm voice. Establish normalcy.
- Middle 20–60%: Occasional ellipses (1 per 2-3 paragraphs), one em-dash break per major paragraph. Tension builds subtly.
- Climax 60–85%: Fragments, ellipses, em-dashes, stutter. Sentences shorten dramatically. Paragraphs get shorter.
- Denouement 85–100%: Returns to longer sentences but retains one final short fragment and one ellipsis for the closing beat. Not fully calm — the ending should still have edge.

**YouTube Retention Rules (critical):**
- **Hook in the first 3 sentences**: hint at something wrong immediately — a missing person, an abandoned tool, a detail that doesn't fit. Do not open with pure reflection. The listener must feel tension before 30 seconds.
- **Wave-like tension**: do not hold at flat low intensity. Insert micro-escalations every 4–6 paragraphs — a small wrong detail, a sound that stops, something slightly off — before the main event.
- **Climax reinforcement**: when the peak horror moment happens, linger on it. Describe the sensation or realization twice in different ways so audio-only listeners don't miss it.
- **Ending with a sharper closing beat**: lingering unease alone is not enough for audio. End with one concrete detail that implies the threat is ongoing or personal — something the narrator notices, hears, or realizes in the final paragraph that tightens the dread.

**Story structure (timed for ~1 hour narration):**
1. **Hook (0–1 min)**: open mid-tension — reference the disappearance, the wrong detail, or the thing that was never explained. Pull the listener in immediately.
2. **Backstory (1–6 min)**: who the narrator is, the job/place/relationship at the center, what was normal before.
3. **Arrival / Setup (6–15 min)**: returning to the scene, environment described in full sensory detail, first signs something is wrong.
4. **Disturbance (15–25 min)**: abandoned tools, wrong smells, silences, details that don't add up. Narrator rationalizes. Insert 1–2 micro-escalations here.
5. **Escalation / Climb (25–40 min)**: narrator goes deeper in — physically or psychologically. Wrong things compound. Each rationalization fails.
6. **Contact / Peak horror (40–50 min)**: the full shape of it. Describe it slowly. Reinforce the key moment twice so it lands in audio.
7. **Aftermath (50–60 min)**: narrator survived but is changed. End with one sharp concrete beat — not just reflection, but something that happened after, something noticed, something that means it isn't over.

---

## Step 3 — Self-review

Read the draft and evaluate against all of the following:

1. **Hook**: Does tension appear in the first 3 sentences? If not, restructure — bring the anomaly forward.
2. **Wave tension**: Are there micro-escalations every 4–6 paragraphs in the middle section? If the tone is flat, insert them.
3. **Prose quality**: Are there clichés? ("heart pounding", "blood ran cold", "spine tingling") — replace every one.
4. **Audio rhythm**: Read 3 random paragraphs aloud mentally. Do they flow without stumbling?
5. **TTS prosody**: Does the climax (60–85%) use fragments, ellipses, and em-dashes? Does the opening feel calm by comparison? If the whole story uses the same sentence length and punctuation density, it will sound flat — vary it.
6. **Prosody overuse**: Are there more than 2 ellipses in any paragraph? More than 3 stutters total? Fix.
7. **TTS-hostile content**: Any numbers not spelled out? ALL-CAPS? Parentheticals? Sentences over 40 words? Fix.
8. **Climax reinforcement**: Is the peak moment described twice in different ways? If not, add the reinforcement.
9. **Ending beat**: Does the final paragraph end with something concrete and threatening, not just mood? If not, sharpen it.
10. **Dialogue rule**: Any quoted dialogue from other characters? Remove it.
11. **Length**: Is the story at least 5000 words? If under, expand the environment, backstory, or escalation sections.

---

## Step 4 — Revise

Apply every fix identified in Step 3. Do not skip any item. Specifically:
- If the hook is weak, rewrite the opening paragraph before anything else.
- If micro-escalations are missing, add them now — do not leave the middle flat.
- If the climax is subtle, add one reinforcing paragraph immediately after the peak moment.
- If the ending lacks a concrete beat, write one — a sound, a detail, a discovery — that closes with dread not resignation.
- If the climax section reads like the opening (same sentence length, no fragments, no ellipses), revise it — add fragments, em-dash breaks, and hesitation to match the peak intensity.
- If TTS-hostile content exists (numbers, caps, long sentences), fix it inline — do not leave for later.

---

## Step 5 — Count words and prepare metadata

- Count approximate word count of the final story
- Generate a slugified title (lowercase, hyphens, no special chars): e.g. `the-night-shift`
- Get today's date in YYYY-MM-DD format
- Derive a stable idempotency key: `YYYY-MM-DD-slugified-title` (e.g. `2026-04-25-the-night-shift`)

---

## Step 6 — Save to file

Save the story to `output/stories/YYYY-MM-DD-slugified-title.md` with this exact YAML frontmatter:

```markdown
---
title: "Full Story Title"
premise: "The one-sentence premise"
date: YYYY-MM-DD
word_count: <number>
---

<story text here>
```

Create the `output/stories/` directory if it doesn't exist.

---

## Step 7 — Push to server

Call the ingest endpoint. Include the idempotency key so retries are safe (server returns the existing story if already ingested):

```bash
RESPONSE=$(curl --fail-with-body -sS -X POST http://localhost:8006/api/stories/ingest \
  -H 'Content-Type: application/json' \
  -d "{\"title\": \"<title>\", \"premise\": \"<premise>\", \"full_text\": \"<full story text escaped for JSON>\", \"idempotency_key\": \"<YYYY-MM-DD-slug>\"}" \
  2>&1)
echo "$RESPONSE"
```

`--fail-with-body` causes curl to exit non-zero on HTTP 4xx/5xx while still printing the response body.

If curl exits non-zero or connection is refused, print the error and continue — the story is already saved locally. Do not retry automatically.

Report the server response (story ID if successful).

---

## Done

Report to the user:
- Story title and word count
- File path where it was saved
- Server response (story ID or error)
