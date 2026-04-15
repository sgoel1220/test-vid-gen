---
name: python-quality-gates
description: Run and interpret this repo's Python validation commands using compileall plus pytest for the Chatterbox TTS server.
---

## What I do

- Run the repository's standard validation commands.
- Use the repo's documented tooling order so failures are easier to interpret.
- Keep validation focused on the current change instead of inventing new checks.

## When to use me

Use this skill after Python changes, before closing work, or when you need a fast confidence check for repo health.

## Canonical commands for this repo

Use the active Python 3.10 environment:

```bash
python -m compileall server.py engine.py config.py models.py utils.py start.py
python -m pytest tests
```

## Recommended execution order

1. `python -m compileall server.py engine.py config.py models.py utils.py start.py`
2. `python -m pytest tests`

## Interpretation rules

- Treat failures in touched areas as blockers.
- If failures are pre-existing and outside the change, call them out explicitly.
- Do not suppress type or runtime issues with unsafe workarounds.
- Prefer minimal fixes over broad refactors while getting quality gates green.
- If you need to inspect structural code patterns while debugging failures, prefer `ast-grep` for syntax-aware search and keep `rg` for plain-text lookup only.

## Search guidance while validating

- Use `ast-grep` when the question is about code structure rather than raw text.
- Canonical examples for this repo:

```bash
ast-grep run --lang python --pattern 'def $NAME($$$ARGS): $$$' .
ast-grep run --lang python --pattern 'class $NAME($$$BASES): $$$' .
ast-grep run --lang python --pattern '$OBJ.$METHOD($$$ARGS)' .
```

## Repo-specific caveats

- Runtime smoke checks may be affected by model downloads, Python-version mismatches, or GPU availability.
- Keep validation evidence concise: command, pass/fail, and any scoped caveat.
