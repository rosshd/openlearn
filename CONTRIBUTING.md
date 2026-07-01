# Contributing

Thanks for helping improve openLearn.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
make check
```

## Project Expectations

- Keep the core local-first: topic notes, review state, and API keys should stay user-owned by default.
- Do not commit private learning data, imported context, `config.json`, `state.json`, topic files, API keys, or `.env`.
- Preserve the Markdown plus JSON topic format unless a migration is explicit.
- Prefer small, focused changes with tests when behavior changes.
- Keep dependencies minimal unless a dependency clearly improves the product.
- Update docs when command behavior, storage, provider behavior, or tutor policy changes.

## License

By contributing, you agree that your contribution is licensed under AGPL-3.0-or-later, the same license as the project.
