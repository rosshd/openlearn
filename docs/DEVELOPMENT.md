# Development

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

## Loop

1. Reproduce or understand the user-visible failure.
2. Keep edits scoped to the requested behavior.
3. Add or update focused tests for changed behavior.
4. Run `make check`.
5. Report what changed, what ran, and remaining risk.

## Commands

| Command | Purpose |
| --- | --- |
| `make check` | Green gate: ruff, unittest, pytest, mocked smoke |
| `make review` | Gate plus evidence bundle under `.artifacts/review/` |
| `make e2e` | Full mocked manual smoke flow |
| `make typecheck` | Pyright, useful but non-blocking |

## Safety

- Use `OPENLEARN_MOCK=1` for model-free CLI smoke.
- Use a temporary `OPENLEARN_HOME` for tests and manual flows.
- Do not test against real topics, config, state, or API keys.
- Do not weaken lint, tests, or smoke to make the gate pass.

## Phase Work

For phase implementation review or next-prompt writing, use `.claude/skills/openlearn-phase-review/`.
