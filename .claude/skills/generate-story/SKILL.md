# Skill: generate-story

Generate a horror story optimized for audio narration, save it locally, and push it to the server.

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

**Length:** 2000–4000 words

**NARRATOR_VOICE_RULES (embed all of these):**
- Single narrator: old male, first-person past tense ("I remember when…", "That was the night I…")
- No dialogue from other characters — narrator may paraphrase what others said, never quote them directly
- Short paragraphs: 2–4 sentences each. One-sentence paragraphs for emphasis only.
- Sound-based descriptions preferred over visual ones (footsteps, breathing, the creak of a door)
- Lingering unease over jump scares — the horror should grow slowly
- Slow escalation: establish routine first, then let the wrongness seep in
- Never name the monster/entity/force explicitly — let the reader fill the gap
- Sentence rhythm matters for audio: vary sentence length. Short. Then longer flowing sentences that build. Then short again.
- Avoid complex punctuation (em-dashes, semicolons) — read aloud naturally
- No chapter breaks, no section headers — single flowing narrative

**Story structure:**
1. Opening hook (1–2 paragraphs): place the narrator in time, hint at what was lost
2. Routine established (3–5 paragraphs): normal life, the job/place/habit at center
3. First wrongness (2–3 paragraphs): something small that doesn't fit
4. Escalation (5–8 paragraphs): wrong things compound, narrator rationalizes
5. Peak horror (3–4 paragraphs): the full shape of it, no escape
6. Aftermath (2–3 paragraphs): narrator survived, but changed — the unease lingers

---

## Step 3 — Self-review

Read the draft and evaluate:

1. **Pacing**: Does the routine feel lived-in before the horror starts?
2. **Prose quality**: Are there clichés? ("heart pounding", "blood ran cold") — replace them.
3. **Audio rhythm**: Read 3 random paragraphs aloud mentally. Do they flow?
4. **Horror effectiveness**: Is the unknown more terrifying than what's shown?
5. **Dialogue rule**: Any quoted dialogue from other characters? Remove it.

---

## Step 4 — Revise

Apply all fixes identified in Step 3. Trim any paragraph that doesn't earn its place. Strengthen the atmosphere in the opening and the aftermath.

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
RESPONSE=$(curl --fail-with-body -sS -X POST http://localhost:8000/api/stories/ingest \
  -H 'Content-Type: application/json' \
  -d "{\"title\": \"<title>\", \"premise\": \"<premise>\", \"full_text\": \"<full story text escaped for JSON>\", \"idempotency_key\": \"<YYYY-MM-DD-slug>\"}" \
  2>&1)
echo "$RESPONSE"
```

`--fail-with-body` causes curl to exit non-zero on HTTP 4xx/5xx while still printing the response body, so the caller can tell whether the commit succeeded.

If curl exits non-zero or connection is refused, print the error and continue — the story is already saved locally. Do not retry automatically.

Report the server response (story ID if successful).

---

## Done

Report to the user:
- Story title and word count
- File path where it was saved
- Server response (story ID or error)
