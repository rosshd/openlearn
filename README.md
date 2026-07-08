# openLearn

[![Tests](https://github.com/rosshd/openlearn/actions/workflows/tests.yml/badge.svg)](https://github.com/rosshd/openlearn/actions/workflows/tests.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

Local-first AI tutoring that keeps learning state in files you own.

openLearn is an open-source Python CLI for course creation, tutoring, review, drills, imports, and progress tracking.
It stores curriculum, learner state, session notes, and context files locally while using an OpenAI-compatible chat-completions API only for model-backed actions.

## Principles

- Local-first: topics and learner state live under your openLearn home.
- Bring your own model access: use a hosted API key or a local keyless endpoint.
- Transparent scope: model calls use the selected topic, bounded notes, recent context, and the current prompt.
- Human-readable memory: topic files are Markdown with JSON metadata.
- Open core: AGPLv3 keeps hosted modifications open.

## Install

```bash
pipx install openlearn
```

From source:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

On Windows, activate the virtual environment with `.venv\Scripts\Activate.ps1` before installing.

Run the app:

```bash
openlearn
```

Run the project gate:

```bash
make check
```

### Platform support

openLearn supports Linux, macOS, and Windows on Python 3.11 and newer.
Topic file locking works on all supported platforms.
Multiline paste detection requires a POSIX terminal; on Windows, the REPL accepts pasted input one line at a time.

## Configuration

On the first bare `openlearn` run without a usable provider configuration, openLearn guides you through provider selection, live key validation, model selection, and a first learning activity.
The built-in presets cover OpenAI, Anthropic-compatible APIs, Ollama, and custom OpenAI-compatible providers.
Set `OPENAI_API_KEY` to skip this onboarding flow and use environment-based configuration; valid keyless localhost providers such as Ollama are already configured when their base URL and model are set.
The final onboarding step can start Quick Learn from a file, start the Vim starter course, or open the menu.

Interactive setup:

```bash
openlearn init
openlearn config set-key
openlearn config set-model gpt-4.1-mini
openlearn config set-base-url https://api.openai.com/v1
openlearn config show
```

Choose the Ollama preset in `openlearn init`, or set `OPENLEARN_BASE_URL` / `base_url` to a local or custom OpenAI-compatible endpoint such as `http://localhost:11434/v1`, to use a provider that does not require an API key.
Hosted defaults such as OpenAI, OpenRouter, and Anthropic still require `OPENAI_API_KEY` or a saved key.

Environment variables override saved config:

```bash
export OPENAI_API_KEY="your-key"
export OPENLEARN_MODEL="gpt-4.1-mini"
export OPENLEARN_EXTRACTOR_MODEL="gpt-4.1-mini"
export OPENLEARN_BASE_URL="https://api.openai.com/v1"
export OPENLEARN_HOME="/path/to/openlearn-data"
```

`OPENLEARN_EXTRACTOR_MODEL` overrides the model used for learner-metadata extraction.
The equivalent `config.json` key is `extractor_model`; when neither is set, extraction uses the tutor model.

If `OPENLEARN_HOME` is unset, openLearn uses the current directory when it contains `learning-topics/`; otherwise it uses the platform data directory.

## Daily Workflow

```bash
openlearn new vim --goal "Use Vim comfortably for real editing"
openlearn resume
```

For assessment material, Quick Learn creates a separate focused topic and begins teaching without placement or outline approval:

```bash
openlearn quick ./midterm-review.pdf
openlearn quick ./study-folder --name "Biology Midterm"
openlearn quick https://github.com/owner/repository
```

Quick Learn accepts text/code files, PDFs, DOCX files, bounded local folders, and public GitHub repositories.
It runs on the efficient mastery profile throughout, optimizing for coverage per minute rather than deep mastery, so a review session moves quickly across the material.
Folder and repository imports select up to 32 supported files, skip hidden directories, generated folders, secret-like names, symlinks, binaries, and oversized files, then save a manifest and source bundle under local context.
Repository sources are cloned with prompts and hooks disabled, treated as read-only text, and never executed.

`resume` uses the active topic.
If no active topic exists, it falls back to the most recently changed topic.
Learning actions from the menu continue into the REPL automatically.
Interactive sessions support multiline paste as one learner message on POSIX terminals.
On Windows, paste multiple lines one at a time.
If a model-backed REPL turn fails after you type an answer, openLearn keeps that answer in the prompt so pressing Enter resubmits it, or typing replaces it.
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
| Learning | `menu`, `quick`, `repl`, `chat`, `resume`, `next`, `review`, `chapter`, `due` |
| Sources | `import <topic> <file>`, `import <topic> --url <url>`, `import <topic> --scan <dir>`, `paste` |
| Practice | `videos`, REPL `/drill`, REPL `/check` |
| Utilities | `templates`, `test`, `tui` |

Model-backed commands require an API key for non-local providers, but localhost OpenAI-compatible endpoints such as Ollama may be used keylessly.
`OPENLEARN_MOCK=1` runs model-backed tests without any provider call.
Transient provider failures such as rate limits, server errors, URL errors, and timeouts are retried up to three times with bounded backoff before surfacing an error.
`chat`, `resume`, `next`, and `review` accept `--dry-run` to print the rendered prompts instead of calling the model, leaving all local files untouched.
`stats` defaults to an all-topic Rich dashboard with streaks, this week's study minutes, review forecast, and mastery by unit; pass a topic slug to focus on one topic, or `--text` / `--share` for a compact shareable summary.
`repair` fills missing topic metadata defaults and can recover simple corrupt JSON frontmatter such as trailing commas or missing closing braces/brackets, writing a `.bak` file before rewriting the topic.
`repl` also has the `shell` alias.

## Local Files

- `learning-topics/*.md`: user-owned topic notes, course plan, metadata, and session log.
- `learning-topics/<slug>.state.json`: dynamic learner model.
- `learning-topics/<slug>.events.jsonl`: append-only learning events.
- `learning-topics/context/<slug>/`: imported source text, manifests, bundles, and summaries.
- `learning-topics/drills/<slug>/`: generated drill files.
- `state.json`: active-topic state.
- `config.json`: saved provider settings and optional API key.

These files are ignored by Git because they may contain private notes, class material, or credentials.

## License

openLearn is licensed under AGPL-3.0-or-later.
See `LICENSE`.
