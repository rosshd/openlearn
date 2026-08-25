# openlearn

[![Tests](https://github.com/rosshd/openlearn/actions/workflows/tests.yml/badge.svg)](https://github.com/rosshd/openlearn/actions/workflows/tests.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python 3.11-3.13](https://img.shields.io/badge/python-3.11--3.13-blue.svg)](pyproject.toml)

openlearn is a local-first AI tutor.
It keeps courses, progress, and imported study material in files you own.
Model-backed lessons use your chosen provider account or a local OpenAI-compatible endpoint.

The local web app is the default interface.
The terminal interface uses the same courses and learner state.

## Install

openlearn supports Python 3.11 through 3.13 on macOS, Linux, and Windows.

```bash
python -m pip install --upgrade openlearn
openlearn
```

Running `openlearn` opens the local web app in your browser.
It listens only on loopback and exchanges its one-time launch capability for an HttpOnly session cookie.

Use the terminal interface instead:

```bash
openlearn cli
```

See [installation](docs/INSTALL.md) for virtual environments, upgrades, uninstalling, headless launch, and the optional code runner.

## Start learning

The web app can create a broad custom course or start from a bundled template.
Technical Interview Prep is the main reference course and uses a short confidence survey to tailor its route.
The survey asks about role goals and topic familiarity.
It does not require an editor or a coding test.

Lessons teach one focused idea at a time.
Checks are recommended but optional for refreshers.
Moving past a check does not award false mastery credit, and the concept remains available for later practice.

Quick Learn starts a temporary focused course from source material:

```bash
openlearn quick ./study-guide.pdf
openlearn quick ./notes-folder --name "Biology review"
openlearn quick https://github.com/owner/repository
```

Quick Learn accepts supported text and code files, PDFs, DOCX files, bounded local folders, and public GitHub repositories.
Imports skip hidden directories, generated files, symlinks, binaries, oversized files, and secret-like names.
Imported code is read as text and is never executed during import.

## Provider setup

Provider setup is available in the web app or through `openlearn init`.
The built-in presets include OpenRouter, OpenAI, Anthropic-compatible APIs, Ollama, and custom OpenAI-compatible endpoints.
Hosted providers require your own API key.
Configured localhost endpoints such as Ollama can run without a key.

Environment variables override saved configuration:

```bash
export OPENAI_API_KEY="your-key"
export OPENLEARN_MODEL="your-model"
export OPENLEARN_BASE_URL="https://provider.example/v1"
export OPENLEARN_HOME="/path/to/openlearn-data"
```

See [data and privacy](docs/DATA_AND_PRIVACY.md) before moving, backing up, restoring, or deleting a learner home.

## Terminal commands

Common commands:

```bash
openlearn templates
openlearn new algorithms --goal "Refresh interview algorithms"
openlearn resume algorithms
openlearn status algorithms
openlearn due
openlearn data inventory
openlearn doctor
```

Run `openlearn --help` or `openlearn <command> --help` for the current command reference.
Run `openlearn cli` for the keyboard-first menu and tutor REPL.

Secure Python checks require an existing Docker or Podman runtime and the pinned runner image shown by `openlearn doctor`.
`--reduced-isolation` runs learner code as a local subprocess and is not a sandbox.

## Local data

The learner home contains:

- `learning-topics/*.md` for course notes, metadata, and the session log.
- `learning-topics/<slug>.state.json` for dynamic learner state and recoverable in-flight work.
- `learning-topics/<slug>.events.jsonl` for append-only learning events.
- `learning-topics/<slug>.interview.json` for an optional interview-prep profile and placement state.
- `learning-topics/<slug>/context/` for imported source material and manifests.
- `config.json` for provider settings.
- `state.json` for the active course.

These files are ignored by Git because they may contain private notes, course material, or credentials.
The [topic format](docs/TOPIC_FORMAT.md) explains the shareable Markdown and JSON structure.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
make check
```

Start with the [documentation index](docs/README.md), [development guide](docs/DEVELOPMENT.md), and [contributing guide](CONTRIBUTING.md).
The current release direction lives in [the product plan](docs/PLAN.md).

## License

openlearn is licensed under AGPL-3.0-or-later.
See [LICENSE](LICENSE).
