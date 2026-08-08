# Contributing

Thanks for helping improve openLearn.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
make check
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.
Contributors need Python 3.11 through 3.13 on Linux, macOS, or Windows.
The default `make check` path is model-free and uses isolated temporary homes.
Use a test home through `OPENLEARN_HOME` instead of real learner data.

## Project Expectations

- Keep the core local-first: topic notes, review state, and API keys should stay user-owned by default.
- Do not commit private learning data, imported context, `config.json`, `state.json`, topic files, API keys, or `.env`.
- Preserve the Markdown plus JSON topic format unless a migration is explicit.
- Prefer small, focused changes with tests when behavior changes.
- Keep dependencies minimal unless a dependency clearly improves the product.
- Update docs when command behavior, storage, provider behavior, or tutor policy changes.
- Do not add telemetry, analytics, or a shared learner-data service without an explicit product decision and documentation update.
- Keep provider accounts, API keys, and local-model infrastructure owned and paid for by the learner or operator who chooses them.

## Release checks

Release changes must keep the installed-package journey working, not only source-tree execution.
Build the wheel and source distribution once, install each into an empty virtual environment, and run `openlearn --version`, `openlearn templates`, and the Maker Bench smoke described in [the release runbook](docs/RELEASING.md).
Never include learner homes, imported context, credentials, or generated development artifacts in a distribution.

## License

By contributing, you agree that your contribution is licensed under AGPL-3.0-or-later, the same license as the project.
