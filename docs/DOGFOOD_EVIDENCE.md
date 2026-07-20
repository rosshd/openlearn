# Exploratory Dogfood Evidence

The dogfood foundation drives the installed `openlearn` command through a real PTY and saves sanitized evidence for later review.
It lives under `tests/dogfood/` because it is test support rather than an application entry point.

## Run the Representative Mission

Run from the repository root after installing the development dependencies and the `openlearn` command.
The mission creates its own isolated home, forces mock-provider mode, and does not read the caller's openLearn state.

```bash
RUN_PARENT="$(mktemp -d)"
RUN_ROOT="$RUN_PARENT/mock-draft-course" \
OPENLEARN_BIN="$(command -v openlearn)" \
python - <<'PY'
import os
from pathlib import Path

from tests.dogfood.missions import run_mock_draft_course_mission

outcome = run_mock_draft_course_mission(
    Path(os.environ["RUN_ROOT"]),
    command=(os.environ["OPENLEARN_BIN"], "menu"),
)
print(f"achieved={outcome.achieved}")
print(f"evidence={outcome.evidence}")
PY
```

The command prints the evidence directory to inspect.
Delete `RUN_PARENT` after review because the sibling `home/` directory contains the isolated course files created by the mission.

## Version 1 Artifact Contract

Each mission owns one run directory with isolated application state and a separate evidence bundle.

```text
mock-draft-course/
├── home/                         isolated OPENLEARN_HOME
└── evidence/
    ├── manifest.json             mission context, artifact index, and outcome
    ├── interactions.jsonl        ordered keyboard input and rendered PTY output
    ├── decisions.jsonl           bounded observations, selected actions, and provenance
    ├── final-state.json          metadata-only inventory of the isolated home
    └── frames/
        ├── 001-mission-entry.txt
        ├── 002-draft-details-complete.txt
        └── 003-mission-completion.txt
```

Every structured artifact carries `schema_version: 1`.
Consumers must reject or explicitly migrate unsupported schema versions instead of assuming field compatibility.
Paths recorded in `manifest.json` are relative to the evidence directory except for the isolated `openlearn_home` and executed command.

`manifest.json` contains:

- `started_at`: UTC mission start time.
- `mission`: the sanitized learner persona and user-level goal.
- `environment`: mock-provider mode, isolated home, and public command.
- `artifacts`: the interaction stream, ordered terminal frames with labels and UTC capture times, and final-state inventory.
- `status`: `running` until finalization, then `completed` or `failed`.
- `outcome`: achieved flag, sanitized summary, exit or signal status, keyboard interaction count, and elapsed seconds.

Each line of `interactions.jsonl` is an ordered JSON event with `schema_version`, `event` (`input` or `output`), mission-relative `elapsed_seconds`, and sanitized `text`.
Input events represent keyboard text sent through the PTY, while output events contain chunks rendered by the real terminal process.

Each line of `decisions.jsonl` links one selected action to the exact bounded observation supplied to its fake or Codex decision source.
The record includes truncation and remaining-budget metadata plus sanitized source provenance when the source provides it.

Each terminal frame is a sanitized plain-text snapshot of rendered output at a meaningful checkpoint.
The manifest is the source of frame order, labels, paths, and capture times.

`final-state.json` records only relative paths and entry kinds under the isolated home.
It deliberately excludes file contents, hashes, sizes, timestamps, permissions, and symlink targets.

## Safety Boundaries

- Never run a mission against a real `OPENLEARN_HOME`, learner topic, imported context, or credential.
- Mission setup may create isolated fixtures directly, but all actions after mission start must use keyboard input through the public terminal interface.
- Capture only allow-listed environment facts; never persist the full process environment.
- Every `EvidenceBundle` and `EvidenceRecorder` call must explicitly provide `sensitive_values`, even when an isolated fixture has none.
- Provide known private fixture values through `sensitive_values` so they are redacted from every artifact.
- Common OpenAI and GitHub token shapes, bearer tokens, and credential-bearing URLs are redacted at the capture layer, including terminal echo.
- Terminal control sequences are removed before output or frame evidence is persisted.
- Treat redaction as defense in depth, not permission to use real secrets or private learner material.
- The final-state artifact is an inventory, not a backup of learner data.

The representative mission does not enter a teaching session, so it has no tutor-only transcript.
A future teaching mission must derive a sanitized tutor transcript from captured public terminal interactions without storing hidden model reasoning or private source material.

## Run the Opt-In Codex Explorer

The Codex explorer is deliberately excluded from `make check` and the default pytest suite never resolves or starts a Codex executable.
Use it only when a live AI-driven terminal evaluation is intended.
The supplied output root must not already exist.

```bash
make codex-dogfood RUN_ROOT="$(mktemp -d)/codex-draft-course"
```

The command runs a direct learner and an error-prone learner against separate isolated homes.
The personas encourage different routes, but route choice is recorded evidence rather than part of the hidden success predicate.
This lets unexpected friction or recovery remain visible instead of turning a completed learner goal into a false mission failure.
Codex chooses every keyboard action from the bounded sanitized PTY observation, while the harness independently verifies that exactly one draft with the requested public fields exists.
Codex receives neither the verifier implementation nor filesystem access to the mission, evidence, or repository.
The command prints only each variant's final status and evidence path.
It exits nonzero if either variant does not achieve the mission.

Pass an explicit installed command, Codex home, or model when needed:

```bash
./scripts/run-codex-dogfood /private/tmp/openlearn-codex-run \
  --openlearn .venv/bin/openlearn \
  --codex /Users/ross/.local/bin/codex \
  --codex-home /Users/ross/.codex \
  --model gpt-5
```

Each decision record includes `source_kind` plus sanitized provenance.
Live records identify Codex's CLI version and model when explicitly selected, invocation and schema fingerprints, process status and duration, and accepted event-type counts.
Fake composition tests use `source_kind: fake`, so deterministic evidence cannot be mistaken for a live Codex run.
Raw Codex reasoning, stderr, environment variables, prompts, authentication material, and user configuration are never persisted.

Explorer evidence is not an independent review verdict.
Future UX-critic and tutor-judge evaluations must use fresh Codex runs that do not reuse the explorer's model session or hidden reasoning.

## Extend the Harness

Use `PtyMissionRunner` for process control, `EvidenceBundle` for persistence, and a mission function for goal-specific terminal decisions.
Capture frames at entry, major decisions, friction or errors, and completion.
Always close the runner in a `finally` block, inventory final state before completing the bundle, and determine achievement from visible process results plus isolated local state.

Add integration coverage that proves the installed command saw a real TTY, actions went through keyboard input, the caller's home remained untouched, artifacts survived process exit, and private fixture values were absent from every persisted file.

## Run the Tutor Behavior Eval

The tutor behavior lane runs the scripted learner scenarios in `tests/evals/scenarios/` through the live `ask_topic` flow.
It creates a separate isolated `OPENLEARN_HOME` for every scenario and never reads saved openLearn config, topics, state, or credentials.
Supply the provider key through the process environment and select a judge model that differs from the tutor model.

```bash
OPENAI_API_KEY="..." \
OPENLEARN_MODEL="tutor-model" \
make tutor-behavior-eval \
  RUN_ROOT="$(mktemp -d)/tutor-behavior" \
  JUDGE_MODEL="independent-judge-model"
```

The output root must not exist before the run.
The target is deliberately absent from `make check` because it makes live model calls and evaluates output quality.
If live access is missing, mock mode is enabled, or the tutor and judge models match, the command exits with setup instructions instead of reporting a false pass.

Each run writes `evidence/manifest.json`, `evidence/summary.md`, and `evidence/turns.jsonl`.
Every JSONL record includes the scenario and learner persona, scripted history, live learner message and tutor response, rubric verdict, state delta, emitted learning events, and sanitized model and fixture provenance.
Use this lane before tutor-policy changes and as an intentional release check for releases that change tutor behavior.
