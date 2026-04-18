Run an adversarial review of the current changes using Codex's model.

Execute this command and wait for it to complete (do NOT run in background):

```bash
node "/Users/shubham/.claude/plugins/cache/openai-codex/codex/1.0.3/scripts/codex-companion.mjs" adversarial-review --wait $ARGUMENTS
```

After it completes, read and report all findings. Then implement every actionable finding before proceeding. If any finding conflicts with the bead's intent, stop and ask the user — do not silently ignore it.
