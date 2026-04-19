Refactor a target area of the codebase end-to-end: explore, plan, implement, type-check, test, review, commit.

`$ARGUMENTS` — description of what to refactor (e.g. "DRY the pod lifecycle in tts.py and image.py").
If empty, ask the user what they want to refactor before proceeding.

Follow this EXACT workflow. Do NOT skip any step.

---

## Step 0 — Clarify scope

If $ARGUMENTS is empty, use `AskUserQuestion` to ask:
- What code should be refactored?
- What is the goal (DRY, extract abstraction, rename, split, simplify)?

If $ARGUMENTS is provided, confirm your interpretation in one sentence before proceeding.

---

## Step 1 — Explore with Codex (read-only)

Delegate deep exploration to Codex **before touching any code**:

```bash
CODEX_COMPANION=$(find "$HOME/.claude/plugins/cache/openai-codex" -name "codex-companion.mjs" 2>/dev/null | sort -V | tail -1)
node "$CODEX_COMPANION" task "Explore the refactoring target: $ARGUMENTS

For every file involved:
1. Show the exact duplicated or problematic code with file paths and line numbers
2. List all symbols (functions, classes, types) that will be touched
3. List all callers/importers of those symbols across the codebase
4. Identify any existing abstractions that overlap with the proposed change
5. Flag any edge cases, error paths, or tests that must stay green"
```

Surface the full Codex output to the user. Do not proceed until you fully understand the blast radius.

---

## Step 2 — Plan

Write a concise implementation plan:

1. **What changes** — new symbols to add, symbols to modify, symbols to delete
2. **Call-site updates** — every file that imports or calls the changed symbols
3. **Test changes** — new tests to write, existing tests to update
4. **Risks** — anything that could break (typing, runtime behaviour, ordering)

Use `AskUserQuestion` if any decision has multiple valid approaches. Do NOT proceed until the plan is approved.

---

## Step 3 — Implement

Apply the refactor using Serena symbolic tools wherever possible:

- `mcp__serena__find_symbol` — locate definitions before editing
- `mcp__serena__replace_symbol_body` — replace entire functions/classes
- `mcp__serena__replace_content` — targeted regex edits within a symbol
- `mcp__serena__rename_symbol` — rename across the whole codebase
- `mcp__serena__find_referencing_symbols` — verify all call sites updated

Rules:
- One logical change at a time; do not mix unrelated fixes
- No behaviour changes — pure structural refactor unless explicitly requested
- Preserve all existing error handling, logging, and docstrings unless they are the target

---

## Step 4 — Type-check

Run mypy on every modified file:

```bash
python3 -m mypy <modified files> --strict
```

Fix **all** errors before continuing. Zero errors required.

---

## Step 5 — Test

Run the relevant test suite:

```bash
python3 -m pytest <relevant test paths> -v
```

- All previously passing tests must still pass
- Add new unit tests for any new abstraction introduced
- Fix failures — do not proceed with red tests

---

## Step 6 — Adversarial review (MANDATORY)

Run the adversarial review in the **foreground** and wait for results:

```bash
CODEX_DIR="$(find "${CLAUDE_PLUGINS_DIR:-$HOME/.claude/plugins}/cache/openai-codex/codex" -name "codex-companion.mjs" -print -quit 2>/dev/null)" && node "$CODEX_DIR" adversarial-review --wait
```

Read every finding. For each one:
- **Actionable** → implement the fix, then re-run mypy + tests
- **Conflicts with intent** → stop and ask the user before proceeding
- **Cosmetic / out-of-scope** → note it, do not apply silently

Do NOT proceed to commit until all actionable findings are resolved.

---

## Step 7 — Commit

Stage only the refactor files (no unrelated changes):

```bash
git add <changed files>
git commit -m "$(cat <<'EOF'
refactor(<scope>): <short imperative summary>

<One paragraph: what was duplicated/messy, what abstraction was introduced,
and why this is the right structure going forward.>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Step 8 — Push

```bash
git pull --rebase && git push origin main && git status
```

Must show "up to date with origin" before declaring done.

---

## Done

Report to the user:
- What was refactored and what abstraction was introduced
- Lines added / removed (from `git diff --shortstat HEAD~1`)
- Test results summary
- Any findings from the adversarial review that were applied
