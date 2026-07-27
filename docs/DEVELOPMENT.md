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
| `make oci-live` | Opt-in live Docker/Podman runner boundary tests using only a pre-provisioned pinned image |
| `make typecheck` | Pyright, useful but non-blocking |
| `make repo-status` | Show local version, branch divergence, and worktree state |
| `make worktree NAME=<task> TYPE=<type>` | Create a safe repo-local task worktree |
| `make finish NAME=<task>` | Remove a clean, merged repo-local task worktree and branch |

GitHub Actions runs the unittest lane across Ubuntu, Windows, and macOS on Python 3.11 and 3.13.
Local `pytest` workflow smoke tests use `pexpect`; POSIX pty tests are skipped on Windows.
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
- Do not test against real topics, config, state, or API keys.
- Do not weaken lint, tests, or smoke to make the gate pass.

## Exploratory Dogfooding

See [Exploratory Dogfood Evidence](DOGFOOD_EVIDENCE.md) for the real-PTY mock mission, evidence contract, and sanitization boundaries.

## Phase Work

For phase implementation review or next-prompt writing, use `.claude/skills/openlearn-phase-review/`.
