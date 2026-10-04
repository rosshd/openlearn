# Development

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Loop

1. Reproduce or understand the user-visible failure.
2. Keep edits scoped to the requested behavior.
3. Add or update focused tests for changed behavior.
4. Run `make check`.
5. Report what changed, what ran, and remaining risk.

## Product scope

Keep product changes centered on the general tutoring loop: a learner sets a goal, studies a focused lesson, can try a useful check, gets feedback, saves progress, and can return to retrieve the idea later.
Technical Interview Prep is a supported reference course, not a separate expanding interview-training product.
Preserve existing imports, coding tools, interview records, and terminal workflows.
Defer new interview simulations, language expansion, activity adapters, automatic research or diagrams, community features, and hosted or sync work until a later explicit scope decision.
The optional TUI remains supported, but its feature set is frozen.
The web and CLI share teaching policy and learner state; interface-specific presentation does not require exact UI parity.
See the [product plan](PLAN.md) for the current scope and release direction.

## Commands

| Command | Purpose |
| --- | --- |
| `make check` | Green gate: Ruff, pytest, and mocked interface smoke |
| `make unit` | Compatibility entry point for direct `unittest` runs |
| `make review` | Optional `make check` evidence bundle under `.artifacts/review/`; not an independent review |
| `make e2e` | Full mocked manual smoke flow |
| `make oci-live` | Opt-in live Docker/Podman runner boundary tests using only a pre-provisioned pinned image |
| `make typecheck` | Pyright, useful but non-blocking |
| `make repo-status` | Show local version, branch divergence, and worktree state |
| `make worktree NAME=<task> TYPE=<type>` | Create a safe repo-local task worktree |
| `make finish NAME=<task>` | Remove a clean, merged repo-local task worktree and branch |

GitHub Actions runs pytest across Ubuntu, Windows, and macOS on Python 3.11 through 3.13.
Pytest collects the older `unittest.TestCase` suites, so `make check` does not run them a second time through `unittest`.
Workflow smoke tests use `pexpect`; POSIX pty tests are skipped on Windows.
The OCI live lane is excluded from `make check`, never pulls an image, and skips with runtime/image diagnostics unless `OPENLEARN_RUN_OCI_TESTS=1` and a supported runtime already has the pinned image.
Run it with `make oci-live` after explicitly provisioning Docker or Podman and the exact image printed by `openlearn doctor`.
The workflow-dispatch OCI job explicitly pre-provisions that image before invoking the same no-pull lane.
The live tests iterate both Docker and Podman when both are ready, so the same lane can run on appropriately provisioned Linux, macOS, or Windows hosts.

## Releases

`src/openlearn/__init__.py` is the single source for the package version.
`pyproject.toml` reads that value through setuptools dynamic metadata.

To publish a release, update `__version__`, merge the release commit, then push a matching `vX.Y.Z` tag.
The release workflow builds the sdist and wheel, verifies both distributions report the tag version through `openlearn.__version__` and `openlearn --version`, publishes to PyPI with trusted publishing, and creates or updates the GitHub release with the built artifacts.
The PyPI project and its trusted-publishing settings must exist before the first automated publish.

## Safety

- Use `OPENLEARN_MOCK=1` for model-free CLI smoke.
- Use a temporary `OPENLEARN_HOME` for tests and manual flows.
- For provider-configuration tests, clear provider environment variables, mock saved config reads, and reset the config cache.
- Do not let automated tests read real topics, config, state, or API keys.
- Do not weaken lint, tests, or smoke to make the gate pass.

## Exploratory Dogfooding

See [Exploratory Dogfood Evidence](DOGFOOD_EVIDENCE.md) for the real-PTY mock mission, evidence contract, and sanitization boundaries.

## Headless and bounded tutor QA

Use a supported Python 3.11-3.13 environment and a synthetic-only temporary learner home.
Before testing, record the intended commit and confirm that the running interpreter imports that checkout, not an older installed package:

```bash
git rev-parse HEAD
git status --short
python -c 'import openlearn, openlearn.cli; print(openlearn.__version__); print(openlearn.cli.__file__)'
make check
OPENLEARN_HOME="$(mktemp -d)" OPENLEARN_MOCK=1 openlearn web --no-browser
```

Keep provider environment overrides and real credentials out of the mock test home/session, exercise only offline/mock flows, and stop the mock server with Ctrl-C before a separately authorized paid run.
Restart the application and reload the browser after source changes; a version number alone does not prove that the tested fixes are running.
Use `make e2e` for the mocked public CLI journey when browser access is unavailable.

`--no-browser` suppresses automatic browser opening; it does not expose the loopback server to another machine or separately managed cloud browser.
Use a supported browser on the same host that can access loopback, such as the owner's Mac browser with permission.
If that browser is unavailable or blocks loopback, keep browser QA blocked and continue offline CLI/mock checks.
Do not disable browser safeguards, add a tunnel/proxy, change loopback binding, or transfer an existing learner/config directory to work around the restriction.

On a trusted headless host, the owner can use `openlearn init` in an interactive terminal for provider setup, or `openlearn config set-key` without an argument for hidden key entry.
Stop if hidden terminal input is unavailable; do not pipe a key, put it in command arguments/history, paste it into an agent chat, or record the credential-entry screen.
`config set-key` saves a key but does not prove provider/model validation succeeded.
`init` validates the provider/model through metadata requests and finishes setup without starting tutor inference.
`openlearn config show` reports key presence without printing its value.
Keep keys, capability-bearing browser launch URLs, raw source/request previews, and learner/config files out of logs and handoffs.

Paid QA is a separate opt-in operation, not part of `make check`.
Obtain explicit permission for the provider, screened synthetic inputs, request ceiling, and conservative dollar ceiling before starting.
The current guard supports the approved OpenRouter source-mode endpoint/model and requires a trusted pricing/bounds snapshot verified within the last day; do not invent prices or copy a stale fixture.
For example, the operator can set a smaller six-request/$0.03 batch using existing controls:

```bash
export OPENLEARN_QA_BUDGET=1
export OPENLEARN_QA_PRICING="/absolute/private/path/verified-pricing.json"
export OPENLEARN_QA_LEDGER="/absolute/private/path/qa-ledger.json"
export OPENLEARN_QA_MAX_CALLS=6
export OPENLEARN_QA_MAX_USD=0.03
```

These variables configure the guard, not provider credentials or permission to spend.
All generation, judging, extraction, repair, and retry subcalls count; guarded transports disable automatic retries and reserve a conservative bound before each request.
Stop the batch on the first rate-limit, funding/access restriction, or unknown-usage transport failure, without a manual retry.
Retain unknown-cost reservations instead of treating failures as free, clearing the ledger, changing its pricing/caps, or switching providers to continue.
A new batch needs separate authorization and a separate ledger; carry every earlier reservation into cumulative reporting rather than resetting prior evidence.
Review and approve each screened source-mode request in the application; prior consent does not carry over.

Report a short evidence checklist:

- Passed: exact commit/import, offline gate, and only the browser/model behaviors actually observed.
- Blocked: the failing step and safely retained status, retry timing, or provider attribution, if available; omit raw responses and private state.
- Untested: grading, hints, correction, or delayed unaided recall not reached; mocked tests and tiny synthetic runs do not demonstrate learning gains.
- Cost: provider request count, confirmed usage calculations, unknown-cost reservations, cumulative conservative exposure, and invoice cost only when independently known.
- Cleanup: no active requests, server stopped, temporary browser tab closed, and learner data and previous ledgers preserved.

## Phase Work

For phase implementation review or next-prompt writing, use `.claude/skills/openlearn-phase-review/`.
