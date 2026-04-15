---
description: Run this repo's main Python validation commands and summarize failures with clear scope.
agent: build
---

Run this repository's standard lightweight Python quality gates using the active Python 3.10 environment.

If you need to inspect code structure while diagnosing a failure, prefer `ast-grep` over plain grep for syntax-aware searches. Use `rg` only for raw text lookup.

Required order:

1. `python -m compileall server.py engine.py config.py models.py utils.py start.py`
2. `python -m pytest tests`

Requirements:

- Report each command with pass/fail status.
- If something fails, identify whether it is caused by the current change or appears pre-existing.
- Do not add unrelated refactors.
- If a command output is noisy, summarize only the actionable failures.
- If everything passes, give a short green summary.
- If validation is blocked by missing dependencies, model downloads, or hardware-specific environment issues, call that out clearly instead of mislabeling it as a code regression.
