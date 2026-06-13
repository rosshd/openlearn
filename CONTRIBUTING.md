# Contributing

Thanks for helping improve openLearn.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m unittest
```

## Project Expectations

- Keep the core local-first: topic notes, review state, and API keys should stay user-owned by default.
- Do not commit private learning data, `config.json`, `state.json`, topic Markdown files, API keys, or `.env` files.
- Prefer small, focused changes with tests when behavior changes.
- Keep dependencies minimal unless a dependency clearly improves the MVP.
- Update `README.md` or `docs/ARCHITECTURE.md` when command behavior, storage, or provider behavior changes.

## License

By contributing, you agree that your contribution is licensed under AGPL-3.0-or-later, the same license as the project.
