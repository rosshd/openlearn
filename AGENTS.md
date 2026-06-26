# openlearn Agent Instructions

## Project

openlearn is a local-first AI tutoring CLI. The entry point is `openlearn`; the main implementation lives in `src/openlearn/cli.py`.

## Key Files

- `src/openlearn/cli.py`: commands, REPL, tutor prompts, model calls, storage flows.
- `src/openlearn/constants.py`: prompt constants and limits.
- `src/openlearn/models.py`: dataclasses for topic/session state.
- `src/openlearn/text.py`: parsing and text helpers.
- `tests/`: unittest and pytest coverage.
- `manual-tests/`: human smoke flows.

## Working Rules

- Keep the project local-first. Do not commit topic files, context imports, `config.json`, `state.json`, API keys, or `.env` files.
- Preserve the Markdown plus JSON topic format unless the task explicitly changes storage.
- Respect environment precedence: env vars first, then `config.json`, then defaults.
- Keep edits scoped. Avoid unrelated refactors, dependency churn, or broad prompt rewrites.
- For phase-style work, follow the exact brief, named fields, order, and scope boundaries.
- For bug fixes, reproduce or reason from the user-visible flow before patching.

## Verification

One command gates a change:

```bash
make check    # lint + unit + pytest + mock smoke; must be green before push
```

Before a PR, collect evidence with `make review` (writes logs + diff to
`.artifacts/review/`). For the full mock flow use `make e2e`. `make typecheck`
(pyright) is available but non-blocking. Slow AI-judge evals are marker-gated;
do not run them unless the task calls for real model evaluation.

For the detailed validation/review/PR-prep workflow, see the
`openlearn-validate` skill (`.claude/skills/openlearn-validate/`).
