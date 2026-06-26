---
name: openlearn-validate
description: >
  Validate and prepare changes in the openlearn repo before review or push.
  Use whenever you have finished editing openlearn code and need to prove the
  change is safe: running the test/lint/smoke gate, collecting evidence, or
  preparing a pull request. Triggers on requests like "validate this", "is this
  ready to push", "run the checks", "review before PR", or "prep a PR" while
  working in openlearn.
---

# openlearn validation

The validation lane lives in the `Makefile`. Prefer it over ad-hoc commands so
results are consistent and reproducible.

## The one command

```bash
make check
```

`check` is the green gate: `lint` (ruff) + `unit` + `pytest` + `smoke` (mocked
tutor chat). If it passes, the change is safe to push. Do not push on a red gate.

## Review before a PR

```bash
make review
```

Runs `check` and writes an evidence bundle to `.artifacts/review/<timestamp>/`:

- `check.log` — full gate output
- `diff.stat` / `diff.patch` — exactly what changed

After it passes, summarize for the human:

1. What changed (from `diff.stat`) and why.
2. What ran and the result (from `check.log`): tests, lint, smoke.
3. Risk: behavior changes, storage-format changes, prompt changes, anything
   touching `state.json` / topic Markdown. Call these out explicitly.
4. Anything not covered (e.g. slow AI-judge evals were skipped).

Report evidence, not just "done".

## Conventions

- Everything runs against the project venv (`.venv/bin/python`); the Makefile
  already points at it.
- Smoke and any CLI exercise must run mocked and isolated:
  `OPENLEARN_MOCK=1 OPENLEARN_HOME="$(mktemp -d)"`. Never touch the user's real
  `~/learning-topics`, `state.json`, or `config.json`.
- `make typecheck` (pyright) is available but **non-blocking** — the dynamic core
  still reports type issues. Use it to chip away, not as a gate.
- `make e2e` runs the full `manual-tests/smoke-full.sh --mock` flow; use it for
  larger changes, not every edit.
- Slow evals are marker-gated (`-m 'not slow'`); only run them when the task is
  about real model-output quality.

## Do not

- Do not commit topic files, context imports, `config.json`, `state.json`, API
  keys, or `.env`.
- Do not weaken the gate (e.g. excluding real source) to make it pass. If `check`
  is red, fix the cause or report it.
