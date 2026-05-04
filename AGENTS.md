# Agent Instructions

This is the canonical repo instruction file. `CLAUDE.md` is a symlink to this file.

## Response Style

- Keep responses short by default. Expand only when explicitly asked.

## Serena LSP Tools

Use Serena MCP tools for all code navigation and refactoring — never grep/glob for symbols.

| Task | Tool |
|------|------|
| Find definition | `mcp__serena__find_symbol` |
| Find references | `mcp__serena__find_referencing_symbols` |
| Rename symbol | `mcp__serena__rename_symbol` |
| Overview a file | `mcp__serena__get_symbols_overview` |
| Search patterns | `mcp__serena__search_for_pattern` |

**Rules:** Always use Serena for refactoring. Use `find_symbol` before editing. Grep misses dynamic references.

## Codex Pair Programming

Claude and Codex work as pair programmers. Use Codex for exploration, review, and research — not just rescue.

**Delegate to Codex when:**
- Exploring how something works across multiple files
- Reviewing a diff, PR, or recent changes
- Tracing data flow, finding usages of a pattern
- Answering "how does X work" questions spanning many files
- Any task requiring 5+ read/search tool calls

**Don't delegate:** simple single-file reads, questions answerable from current context.

```bash
CODEX_COMPANION=$(find "$HOME/.claude/plugins/cache/openai-codex" -name "codex-companion.mjs" 2>/dev/null | sort -V | tail -1)
node "$CODEX_COMPANION" task "<prompt>"                   # read-only (default)
node "$CODEX_COMPANION" task --write "<prompt>"           # write-capable
node "$CODEX_COMPANION" task --resume-last "<follow-up>"  # continue prior work
node "$CODEX_COMPANION" task --background "<prompt>"      # background job
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

**Session init (required before any beads operation):** call `mcp__beads__context(workspace_root='.')` once at the start of every session.

| Action | MCP tool |
|--------|----------|
| Init session | `mcp__beads__context(workspace_root='.')` |
| Find available work | `mcp__beads__ready` |
| View issue | `mcp__beads__show` |
| Claim work | `mcp__beads__claim` |
| Create issue | `mcp__beads__create` |
| Update issue | `mcp__beads__update` |
| Add dependency | `mcp__beads__dep` |
| Close issue | `mcp__beads__close` |

**Context efficiency — always pass these:**
- `brief=true` on all list/show calls unless you need full detail
- `max_description_length=200` on list calls
- `fields=["id","title","status","priority"]` when you only need a summary

**Never use TodoWrite, TaskCreate, or markdown TODO lists.**

### Epic Planning — "Implement X" requests

When the user asks to **implement**, **build**, or **add** anything non-trivial:

1. **Create an epic:**
   ```python
   mcp__beads__create(title="<Feature>", issue_type="epic", priority=2, description="Goal and scope.")
   ```

2. **Create tasks in parallel** (one per sub-task):
   ```python
   mcp__beads__create(title="...", issue_type="task", priority=2, description="...")
   ```

3. **Link tasks to the epic** (`parent-child` = task belongs to epic):
   ```python
   mcp__beads__dep(issue_id="<task-id>", depends_on_id="<epic-id>", dep_type="parent-child")
   ```

4. **Link ordering within the epic** (task B blocked by task A):
   ```python
   mcp__beads__dep(issue_id="<task-b>", depends_on_id="<task-a>")  # default: blocks
   ```

5. **Confirm the plan** — show the user the epic + task list before implementing.

**"Complete epic X":** `mcp__beads__show(epic-id)` → enumerate child tasks → respect `blocks` order → fan out independent tasks as parallel `pick-bead` agents.

**Rules:**
- Every "implement X" request gets an epic — no floating tasks
- Epic closes only after all child tasks are closed
- Out-of-scope issues discovered mid-work → new bead immediately, linked to same epic

### Filing beads during implementation

Any out-of-scope issue discovered MUST become a bead immediately — never silently leave it unfiled.

```python
mcp__beads__create(
    title="Short imperative title",
    issue_type="bug" | "task" | "chore",
    priority=1 | 2 | 3,  # 1=blocker, 2=normal, 3=nice-to-have
    description="What, why, and how to fix. Include acceptance criteria.",
)
```

## Bead Workflow

1. **Pick** — `mcp__beads__ready`, only status `"open"` (never `"in_progress"`)
2. **Explore** — use Codex (read-only) to understand the area before touching code
3. **Implement** — simplest solution that works; avoid premature abstractions
4. **Test** — verify thoroughly
5. **Adversarial review** — run `/adversarial-review` in the **foreground**; apply **all findings immediately without asking**; re-test. When multiple fix options exist, pick the best long-term solution consistent with this file's rules — never ask.
6. **Commit + push** — follow the Git & Deploy flow below
7. **Close** — `mcp__beads__close` only after push succeeds

**CRITICAL RULES:**
- ONLY pick `"open"` beads — never `"in_progress"`
- NEVER use git worktrees — commit directly on main
- ALWAYS use Codex to explore before implementing
- NEVER skip `/adversarial-review` — foreground only, apply all findings before pushing
- NEVER mark done before push succeeds
- NEVER assume — ask if anything is unclear

## Git & Deploy

After **any** completed change (fix, feature, refactor, config, docs, etc.), always do this without being asked:

```bash
git add <files> && git commit -m "<type>(...): ..."
git pull --rebase && git push
cd services/creepy-brain && docker compose up -d --build brain
docker compose ps brain   # verify container is up
```

**Build commands:**
```bash
# Frontend
cd services/creepy-brain/static && npx esbuild src/main.ts --bundle --outfile=dist/app.js --format=esm --target=es2020

# Docker services: brain, postgres
cd services/creepy-brain && docker compose up -d --build brain
```

Compose file: `services/creepy-brain/docker-compose.yml`.

## Session Completion

Work is NOT complete until `git push` succeeds.

1. File beads for any remaining issues
2. Run quality gates if code changed
3. Close finished beads; update in-progress ones
4. `git pull --rebase && git push && git status`
5. Verify status shows "up to date with origin"

## Fix It Right

Always fix issues at the source — never apply runtime hacks, container patches, or workarounds.

- **No container-level hotfixes** — fix the code, commit, rebuild
- **No monkey-patching** — fix the import, schema, config properly
- **Think long-term** — every hack compounds; a proper fix pays off on every deploy
- **Broken deploy = broken code** — treat import errors and config drift as bugs to fix in source

## Architecture Reference

See `docs/ARCHITECTURE.md` for project structure, API endpoints, GPU rules, and deploy info.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
<!-- END BEADS INTEGRATION -->
