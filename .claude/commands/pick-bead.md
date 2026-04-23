Pick a ready bead and complete it end-to-end: implement, test, review, commit, merge, and push.

Follow this EXACT workflow. Do NOT skip any step.

---

## Step 0 — Optional bead selection

If $ARGUMENTS is set, treat it as a bead ID and skip discovery.
Otherwise, call `mcp__beads__ready` to list ready beads, show them to the user, and ask which one to work on. ONLY pick beads with status **open**. NEVER pick status **in_progress**.

---

## Step 1 — Create an isolated worktree

Before touching any code, create a worktree:

```
EnterWorktree(name="bead-<id>")
```

All subsequent work happens inside that worktree branch.

---

## Step 2 — Claim the bead

```
mcp__beads__claim(issue_id="<id>")
```

---

## Step 3 — Review context

1. Call `mcp__beads__show(issue_id="<id>")` for full details.
2. Review the Architecture Overview section in `AGENTS.md`.
3. If anything is unclear or ambiguous, **STOP and ask the user** before writing code.

---

## Step 4 — Implement

- Write the simplest solution that satisfies the acceptance criteria.
- Follow all CLAUDE.md rules: static typing, Pydantic models, no bare dicts.
- Run mypy on modified files and fix all errors before proceeding.

---

## Step 5 — Test

Verify the changes work. Run any relevant tests. Fix failures before continuing.

---

## Step 6 — Adversarial review (MANDATORY)

Run the adversarial review in the **foreground** and wait for it to finish:

Use the `/adversarial-review` skill command.

- Read every finding.
- Implement every actionable finding in the worktree.
- If a finding conflicts with the bead's intent, STOP and ask the user.
- Re-test after applying fixes.

---

## Step 7 — Commit

Stage relevant files and create a descriptive commit inside the worktree:

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

## Step 8 — Exit worktree and merge to main

```bash
# Exit the worktree (keep the branch)
# Then from repo root:
git merge worktree-bead-<id> --no-edit
```

Resolve any merge conflicts carefully, then re-test.

---

## Step 9 — Close the bead

```
mcp__beads__close(issue_id="<id>", reason="Completed")
```

---

## Step 10 — Push to remote

```bash
git pull --rebase
git push origin main
git status  # must show "up to date with origin"
```

Do NOT stop here if push fails — resolve and retry until it succeeds.

---

## Done

Report to the user: bead closed, changes pushed.
