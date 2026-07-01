---
name: openlearn-validate
description: >
  Use after editing openlearn code, before review or push, or when asked to run
  checks, validate work, prepare a PR, or prove a change is safe.
---

# openlearn validation

## Green Gate

```bash
make check
```

`check` runs ruff, unittest, pytest, and the mocked smoke flow.
Do not push or call code changes done on a red gate.

## Review Evidence

```bash
make review
```

This writes `.artifacts/review/<timestamp>/check.log`, `diff.stat`, and `diff.patch`.
Report what changed, what ran, pass or fail status, risk, and any skipped coverage.

## Rules

- Use the Makefile lanes instead of ad-hoc equivalents.
- CLI smoke must be mocked and isolated with `OPENLEARN_MOCK=1` and a temporary `OPENLEARN_HOME`.
- `make typecheck` is useful but non-blocking.
- `make e2e` is for larger changes or version-boundary confidence.
- Slow AI-judge evals are excluded by default; run them only for model-output quality work.
- Never weaken the gate to make it pass.
- Never test against the user's real topics, config, state, or API key.
