# openLearn

[![Tests](https://github.com/rosshd/openlearn/actions/workflows/tests.yml/badge.svg)](https://github.com/rosshd/openlearn/actions/workflows/tests.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

Local-first AI tutoring that keeps learning state in files you own.

openLearn is an open-source, local-first AI learning workspace. It keeps your curriculum, progress, review state, and session notes in files you own, while letting you bring your own model API key.

The early version is intentionally small: a Python CLI that creates topic files, reads local learning state, and uses OpenAI-compatible chat-completion APIs when you ask for tutoring, review, resume help, or next steps.

## Principles

- Local-first by default: learning topics live under `learning-topics/`.
- Bring your own API key first: no required account, hosted service, or subscription.
- Transparent model usage: only relevant topic state is sent to the model.
- Human-readable memory: topics are Markdown files with simple metadata.
- Open core: the project is licensed under AGPLv3 to keep hosted modifications open.

## Current Commands

```bash
python -m openlearn
python -m openlearn init
python -m openlearn menu
python -m openlearn repl
python -m openlearn config set-key
python -m openlearn config set-model gpt-4.1-mini
python -m openlearn config set-base-url https://api.openai.com/v1
python -m openlearn config show
python -m openlearn config clear-key
python -m openlearn new vim --goal "Learn Vim motions and macros"
python -m openlearn list
python -m openlearn recent
python -m openlearn active vim
python -m openlearn status vim
python -m openlearn edit vim
python -m openlearn resume
python -m openlearn next
python -m openlearn chat vim "How should I practice macros?"
python -m openlearn review vim
```

Commands and interactive actions that need model output require an API key: `chat`, `review`, `resume`, `next`, REPL questions, and model-backed menu actions.
Running `openlearn` with no command opens the interactive menu.

## Demo

Create a local learning workspace and a topic:

```bash
openlearn init
openlearn new vim --goal "Use Vim comfortably for real editing"
```

Check what openLearn knows about the topic:

```bash
openlearn status vim
```

Example output:

```text
Topic: vim
Goal: Use Vim comfortably for real editing
Current focus: not set
Level: beginner
Model: gpt-4.1-mini
Known: none
Weak spots: none
Review due: none
```

Resume later without remembering the topic name:

```bash
openlearn resume
```

`resume` reads the active or most recent topic, sends only that topic's relevant local state to the configured model provider, prints a short continuation plan, and appends the session back into the topic file.

Review a topic when you want active recall:

```bash
openlearn review vim
```

For back-and-forth learning, start the REPL:

```bash
openlearn repl
```

Inside the REPL, type normal questions to ask the active topic without retyping `openlearn chat <topic> ...` every time:

```text
openlearn> I think the answer is registers. Am I right?
openlearn> /next
openlearn> /review
openlearn> /status
openlearn> /quit
```

Useful REPL commands are `/help`, `/resume`, `/next`, `/review`, `/status`, `/active <topic>`, `/recent`, `/new <topic> [goal]`, `/ask <question>`, and `/quit`.

Your topic notes remain normal Markdown files under `learning-topics/`, so you can inspect or edit them directly at any time.

## Intended Workflow

The daily workflow should be fast enough to use between classes, coding sessions, or focused study blocks.

Create a topic once:

```bash
openlearn new vim --goal "Use Vim comfortably for real editing"
```

Resume later with no topic name:

```bash
openlearn resume
```

`resume` uses the active topic. If no active topic exists, it falls back to the most recently changed topic file. Topic commands like `new`, `status`, `chat`, `review`, `resume`, and `next` update the active topic automatically.

Fast commands:

- `openlearn recent`: show recently used topics.
- `openlearn active`: show the active topic.
- `openlearn active os`: switch active topic.
- `openlearn edit`: open the active topic file in `$EDITOR`.
- `openlearn menu`: open a numbered menu for common actions.
- `openlearn repl`: start a back-and-forth learning prompt.
- `openlearn resume`: recap and continue where you left off.
- `openlearn next`: generate the next 10-15 minute learning step.
- `openlearn review vim`: generate a short active-recall review.

The target experience is:

```bash
openlearn resume
```

Then learn, answer the recall question, and leave. The topic file keeps the session log for next time.

## Setup

From the project directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Run tests:

```bash
python -m unittest
```

The tests use temporary directories and do not require an API key.

Set an OpenAI API key before using `chat` or `review`:

```bash
openlearn config set-key
```

This stores the key in `config.json` under your openLearn home with file permissions set to owner-read/write when supported by the OS. `config.json` is ignored by Git.

Set the default model:

```bash
openlearn config set-model gpt-4.1-mini
```

Use an OpenAI-compatible provider:

```bash
openlearn config set-base-url https://api.openai.com/v1
```

The base URL should point at an API that supports `POST /chat/completions` with OpenAI-style request and response shapes.

Check config:

```bash
openlearn config show
```

Power users can still use environment variables. These take precedence over saved config:

```bash
export OPENAI_API_KEY="your-key"
export OPENLEARN_MODEL="gpt-4.1-mini"
export OPENLEARN_BASE_URL="https://api.openai.com/v1"
```

Optional settings:

```bash
export OPENLEARN_HOME="/path/to/your/openlearn-data"
```

If `OPENLEARN_HOME` is not set, openLearn uses the current project directory when it contains `learning-topics/`; otherwise it uses `~/.openlearn`.

## Local Files

openLearn keeps user data outside Git by default:

- `learning-topics/*.md`: user-owned topic notes and session logs.
- `state.json`: active-topic state.
- `config.json`: saved model settings and optional API key.

These files are ignored because they may contain private notes, class material, or credentials.

## Pricing Language

The product direction is:

> Bring your own API key, or optionally use transparent usage-based hosted credits with no subscription required.

The hosted-credit path is intentionally not part of the MVP.

## License

openLearn is licensed under the GNU Affero General Public License v3.0 or later. See `LICENSE`.
