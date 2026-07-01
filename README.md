# openLearn

[![Tests](https://github.com/rosshd/openlearn/actions/workflows/tests.yml/badge.svg)](https://github.com/rosshd/openlearn/actions/workflows/tests.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

Local-first AI tutoring that keeps learning state in files you own.

openLearn is an open-source Python CLI for course creation, tutoring, review, drills, imports, and progress tracking.
It stores curriculum, learner state, session notes, and context files locally while using an OpenAI-compatible chat-completions API only for model-backed actions.

## Principles

- Local-first: topics and learner state live under your openLearn home.
- Bring your own API key: no required hosted account or subscription.
- Transparent scope: model calls use the selected topic, bounded notes, recent context, and the current prompt.
- Human-readable memory: topic files are Markdown with JSON metadata.
- Open core: AGPLv3 keeps hosted modifications open.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Run the app:

```bash
openlearn
```

Run the project gate:

```bash
make check
```

## Configuration

Interactive setup:

```bash
openlearn init
openlearn config set-key
openlearn config set-model gpt-4.1-mini
openlearn config set-base-url https://api.openai.com/v1
openlearn config show
```

Environment variables override saved config:

```bash
export OPENAI_API_KEY="your-key"
export OPENLEARN_MODEL="gpt-4.1-mini"
export OPENLEARN_BASE_URL="https://api.openai.com/v1"
export OPENLEARN_HOME="/path/to/openlearn-data"
```

If `OPENLEARN_HOME` is unset, openLearn uses the current directory when it contains `learning-topics/`; otherwise it uses the platform data directory.

## Daily Workflow

```bash
openlearn new vim --goal "Use Vim comfortably for real editing"
openlearn resume
```

`resume` uses the active topic.
If no active topic exists, it falls back to the most recently changed topic.
Learning actions from the menu continue into the REPL automatically.
Interactive sessions support multiline paste as one learner message.
Plain requests such as "continue", "move on", or "skip" advance the current slide; if the wording includes a preference such as "I don't need this", openLearn stores it as a learner preference.

Inside the REPL:

```text
openlearn> /n
openlearn> continue
openlearn> /done
openlearn> /review
openlearn> /drill
openlearn> /check
openlearn> /videos --n 3 registers
openlearn> /status
openlearn> /q
```

Use `/help --all` for the full REPL command list.

## Command Surface

| Area | Commands |
| --- | --- |
| Setup | `init`, `config show`, `config set-key`, `config set-model`, `config set-base-url`, `config clear-key` |
| Topics | `new`, `delete`, `list`, `recent`, `active`, `edit`, `status`, `summary`, `stats`, `repair` |
| Learning | `menu`, `repl`, `chat`, `resume`, `next`, `review`, `chapter`, `due` |
| Sources | `import <topic> <file>`, `import <topic> --url <url>`, `import <topic> --scan <dir>`, `paste` |
| Practice | `videos`, REPL `/drill`, REPL `/check` |
| Utilities | `templates`, `test`, `tui` |

Model-backed commands require an API key unless `OPENLEARN_MOCK=1` is set for tests.
`repl` also has the `shell` alias.

## Local Files

- `learning-topics/*.md`: user-owned topic notes, course plan, metadata, and session log.
- `learning-topics/<slug>.state.json`: dynamic learner model.
- `learning-topics/<slug>.events.jsonl`: append-only learning events.
- `learning-topics/context/<slug>/`: imported source summaries.
- `learning-topics/drills/<slug>/`: generated drill files.
- `state.json`: active-topic state.
- `config.json`: saved provider settings and optional API key.

These files are ignored by Git because they may contain private notes, class material, or credentials.

## License

openLearn is licensed under AGPL-3.0-or-later.
See `LICENSE`.
