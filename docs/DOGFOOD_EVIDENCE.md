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

## Extend the Harness

Use `PtyMissionRunner` for process control, `EvidenceBundle` for persistence, and a mission function for goal-specific terminal decisions.
Capture frames at entry, major decisions, friction or errors, and completion.
Always close the runner in a `finally` block, inventory final state before completing the bundle, and determine achievement from visible process results plus isolated local state.

Add integration coverage that proves the installed command saw a real TTY, actions went through keyboard input, the caller's home remained untouched, artifacts survived process exit, and private fixture values were absent from every persisted file.
