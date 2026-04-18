# Agent Instructions

This is the canonical repo instruction file. `CLAUDE.md` is a symlink to this file.

## Response Style

- Keep responses short by default. Expand only when explicitly asked.

## Serana LSP Tools

Use Serana MCP tools for all code navigation and refactoring — never grep/glob for symbols.

| Task | Tool |
|------|------|
| Find definition | `mcp__serena__find_symbol` |
| Find references | `mcp__serena__find_referencing_symbols` |
| Rename symbol | `mcp__serena__rename_symbol` |
| Overview a file | `mcp__serena__get_symbols_overview` |
| Search patterns | `mcp__serena__search_for_pattern` |

**Rules:** Always use Serana for refactoring. Use `find_symbol` before editing. Grep misses dynamic references.

## Codex Pair Programming

Claude and Codex work as pair programmers. Use Codex for exploration, review, and research — not just rescue.

**Delegate to Codex when:**
- Exploring how something works across multiple files
- Reviewing a diff, PR, or recent changes
- Tracing data flow, finding usages of a pattern
- Answering "how does X work" questions spanning many files
- Any task requiring 5+ read/search tool calls

**Don't delegate:** simple single-file reads, questions answerable from current context.

### Invoking Codex (direct bash)

```bash
# Resolve companion script (macOS + Linux)
CODEX_COMPANION=$(find "$HOME/.claude/plugins/cache/openai-codex" -name "codex-companion.mjs" 2>/dev/null | sort -V | tail -1)

# Read-only exploration/review (default)
node "$CODEX_COMPANION" task "<prompt>"

# Write-capable (only when edits are needed)
node "$CODEX_COMPANION" task --write "<prompt>"

# Continue prior Codex work
node "$CODEX_COMPANION" task --resume-last "<follow-up>"

# Background + fetch result
node "$CODEX_COMPANION" task --background "<prompt>"
node "$CODEX_COMPANION" status
node "$CODEX_COMPANION" result <job-id>
```

Resolve `$CODEX_COMPANION` fresh each Bash call — never hardcode the path.

**Rules:** Default read-only. Prefer foreground. Craft tight prompts with file paths. Use `--resume-last` for follow-ups. Always surface output to the user.

## Python Typing Standards

See `docs/PYTHON_STANDARDS.md` for full rules. Summary:
- All functions must have typed params and return types
- **Never return `dict` — always use a Pydantic model**
- Run `python3 -m mypy path/to/module.py --strict` before committing — zero errors

## Beads Issue Tracker

Use `mcp__beads__*` MCP tools directly — never call `bd` via Bash. The `bd` CLI is for you in the terminal.

| Action | MCP tool |
|--------|----------|
| Find available work | `mcp__beads__ready` |
| View issue | `mcp__beads__show` |
| Claim work | `mcp__beads__claim` |
| Create issue | `mcp__beads__create` |
| Update issue | `mcp__beads__update` |
| Close issue | `mcp__beads__close` |

**Context efficiency rules — always pass these to avoid loading full content into memory:**
- `brief=true` on all list/show calls unless you need full detail
- `max_description_length=200` on list calls
- `fields=["id","title","status","priority"]` when you only need a summary

**Never use TodoWrite, TaskCreate, or markdown TODO lists.**

### Filing beads during implementation

Any out-of-scope issue you discover MUST become a bead immediately — never silently leave it unfiled.

```python
mcp__beads__create(
    title="Short imperative title",
    issue_type="bug" | "task" | "chore",
    priority=1 | 2 | 3,  # 1=blocker, 2=normal, 3=nice-to-have
    description="What, why, and how to fix. Include acceptance criteria.",
)
```

## Bead Workflow

1. **Worktree** — `EnterWorktree` before any code changes
2. **Pick** — `mcp__beads__ready`, only status `"open"` (never `"in_progress"`)
3. **Explore** — use Codex (read-only) to understand the area before touching code
4. **Implement** — simplest solution that works; avoid premature abstractions
5. **Test** — verify thoroughly
6. **Adversarial review** — run `/adversarial-review` in the **foreground**; apply all findings; re-test
7. **Commit** — descriptive message
8. **Merge** — resolve conflicts if any; test again
9. **Close** — `mcp__beads__close` only after successful merge
10. **Push** — `git push origin main`
11. **Clean up** — from repo root: `git worktree remove`, `git branch -d`, `git worktree prune`

**CRITICAL RULES:**
- ONLY pick `"open"` beads — never `"in_progress"`
- NEVER commit directly to main — always use a worktree
- ALWAYS use Codex to explore before implementing
- NEVER skip `/adversarial-review` — foreground only, apply all findings before merging
- NEVER mark done before push succeeds AND worktree branch is deleted
- NEVER assume — ask if anything is unclear

## Session Completion

Work is NOT complete until `git push` succeeds.

1. File beads for any remaining issues
2. Run quality gates if code changed
3. Close finished beads; update in-progress ones
4. Push: `git pull --rebase && git push && git status`
5. Verify status shows "up to date with origin"

## Fix It Right

Always fix issues at the source — never apply runtime hacks, container patches, or workarounds.

- **No container-level hotfixes** — if the image is broken, fix the code, commit, rebuild
- **No monkey-patching** — fix the import, the schema, the config properly
- **Think long-term** — every hack is debt that compounds; a proper fix pays off on every future deploy
- **Broken deploy = broken code** — treat import errors, missing modules, and config drift as bugs to fix in source

## Architecture Reference

See `docs/ARCHITECTURE.md` for project structure, API endpoints, GPU rules, and deploy info.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
<!-- END BEADS INTEGRATION -->
