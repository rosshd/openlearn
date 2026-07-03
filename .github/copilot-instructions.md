# Copilot instructions for openLearn

## Commands

- Install editable package: `python -m pip install -e .`
- Run the test suite: `python -m unittest`
- Run one test method: `python -m unittest tests.test_cli.CliStorageTests.test_new_topic_starts_unstarted`
- Run one test class: `python -m unittest tests.test_cli.CliStorageTests`
- CI runs `python -m unittest` on Ubuntu, Windows, and macOS for Python 3.11 and 3.13.
- From source without installing: `PYTHONPATH=src python -m openlearn ...`
- Manual UX seed flow: `openlearn test` or `openlearn test --reset --resume`

## Architecture

- Single-package Python CLI in `src/openlearn/`; `openlearn.cli` owns the MVP’s storage, prompts, menu/REPL flow, config, and OpenAI-compatible provider calls.
- The main entrypoint is `openlearn` / `python -m openlearn`.
- Learning topics are user-owned Markdown files under `learning-topics/` with a JSON metadata block between `---` delimiters.
- Topic slugs are the stable file IDs; topic commands update `state.json` so `resume`, `next`, `edit`, `menu`, and `repl` can work without retyping the topic name.
- Saved config lives in `config.json`; both `config.json` and `state.json` are local-only data.
- Model-backed commands send only the selected topic’s metadata, a bounded notes excerpt, recent session history, and the current prompt.
- `chat`, `resume`, `next`, and `review` support `--dry-run` to print rendered prompts without provider calls or local file mutation.
- Learner-metadata extraction sends a smaller state snapshot.
- The REPL is thin: slash commands dispatch to the same handlers used by the non-interactive CLI.
- Multiline paste coalescing is POSIX-only; Windows stdin falls back to one line per learner message.

## Conventions

- Keep the project local-first: do not commit topic Markdown files, context files, `config.json`, `state.json`, API keys, or `.env` files.
- Preserve the Markdown + JSON topic format; use `repair`/metadata normalization patterns when filling missing fields.
- Environment precedence is `OPENAI_API_KEY`, `OPENLEARN_MODEL`, `OPENLEARN_EXTRACTOR_MODEL`, and `OPENLEARN_BASE_URL` first, then `config.json`, then defaults.
- Topic commands should leave the active-topic state consistent with the file they operate on.
- Topic writes must keep using the shared file-lock interface, which maps to `fcntl` on POSIX and `msvcrt` on Windows.
- Context imports are `.txt` files only; imported context and summaries live under each topic’s `learning-topics/<slug>/context/` directory.
- Tests isolate data by setting `OPENLEARN_HOME` to a temporary directory; follow that pattern for new tests.
- Skip tests on Windows only when they genuinely require a POSIX pty or TTY behavior, and include the reason in the skip message.
- Read `README.md`, `CONTRIBUTING.md`, `docs/ARCHITECTURE.md`, and `manual-tests/README.txt` before changing CLI behavior, storage, or model prompts.
