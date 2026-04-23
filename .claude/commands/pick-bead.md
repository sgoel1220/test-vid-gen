Pick a ready bead and complete it end-to-end: implement, test, review, commit, and push.

Follow this EXACT workflow. Do NOT skip any step.

---

## Step 0 — Optional bead selection

If $ARGUMENTS is set, treat it as a bead ID and skip discovery.
Otherwise, call `mcp__beads__ready` to list ready beads, show them to the user, and ask which one to work on. ONLY pick beads with status **open**. NEVER pick status **in_progress**.

---

## Step 1 — Claim the bead

```
mcp__beads__claim(issue_id="<id>")
```

---

## Step 2 — Review context

1. Call `mcp__beads__show(issue_id="<id>")` for full details.
2. Review the Architecture Overview section in `AGENTS.md`.
3. If anything is unclear or ambiguous, **STOP and ask the user** before writing code.

---

## Step 3 — Implement

- Commit directly on `main` — **never create a worktree**.
- Write the simplest solution that satisfies the acceptance criteria.
- Follow all CLAUDE.md rules: static typing, Pydantic models, no bare dicts.
- Run mypy on modified files and fix all errors before proceeding.

---

## Step 4 — Test

Verify the changes work. Run any relevant tests. Fix failures before continuing.

---

## Step 5 — Adversarial review (MANDATORY)

Run the adversarial review in the **foreground** and wait for it to finish:

Use the `/adversarial-review` skill command.

- Read every finding.
- Implement every actionable finding.
- If a finding conflicts with the bead's intent, STOP and ask the user.
- Re-test after applying fixes.

---

## Step 6 — Commit

Stage relevant files and create a descriptive commit:

```bash
git add <files>
git commit -m "$(cat <<'EOF'
<Short imperative summary>

<Optional detail paragraph>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Step 7 — Close the bead

```
mcp__beads__close(issue_id="<id>", reason="Completed")
```

---

## Step 8 — Push to remote

```bash
git pull --rebase
git push origin main
git status  # must show "up to date with origin"
```

Do NOT stop here if push fails — resolve and retry until it succeeds.

---

## Done

Report to the user: bead closed, changes pushed.
