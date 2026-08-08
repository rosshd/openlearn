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

Run the local web app:

```bash
openlearn web
```

This opens the loopback-only Maker Bench interface for provider setup, starter courses, focused tutoring, progress, and session history.
Each launch uses a private capability stored in the local server lease, and the browser exchanges it once for an HttpOnly session cookie before the token is removed from the address bar.
It reads and writes the same local course files as the CLI.
Focus Bench starts text-only and opens an optional Dual Surface for three early tools: a plain Python workbench, consent-based YouTube playback, and bounded course-source imports from a file, local folder, or public GitHub repository.
Code runs only after an explicit click and requires the existing Docker or Podman safety runtime; tool use does not award mastery.
Source import and extraction stay local and do not contact the model provider; selected excerpts may be sent to that provider later when the tutor uses them.
Use `openlearn web --no-browser` on a headless machine or `openlearn web --port 9000` to choose another local port.

Run the terminal interface:

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
Secure Python drill checks require a locally running Docker or Podman runtime.
On macOS and Windows, the runtime may provide its normal Linux VM while openLearn keeps the same container contract.

## Configuration

On the first bare `openlearn` run without a usable provider configuration, openLearn first offers a learning destination.
The recommended Technical Interview Prep destination can begin its offline reasoning placement immediately and defer provider setup until course planning.
Other destinations continue through provider selection, live key validation, and model selection before their first model-backed activity.
The built-in presets cover OpenAI, Anthropic-compatible APIs, Ollama, and custom OpenAI-compatible providers.
Set `OPENAI_API_KEY` to skip this onboarding flow and use environment-based configuration; valid keyless localhost providers such as Ollama are already configured when their base URL and model are set.
The onboarding destination menu can start the recommended Technical Interview Prep course, start Quick Learn from a file, start the Vim starter course, or open the main menu.

Interactive setup:

```bash
openlearn init
openlearn config set-key
openlearn config set-model gpt-4.1-mini
openlearn config set-base-url https://api.openai.com/v1
openlearn config set-editor nvim
openlearn config show
```

Choose the Ollama preset in `openlearn init`, or set `OPENLEARN_BASE_URL` / `base_url` to a local or custom OpenAI-compatible endpoint such as `http://localhost:11434/v1`, to use a provider that does not require an API key.
Hosted defaults such as OpenAI, OpenRouter, and Anthropic still require `OPENAI_API_KEY` or a saved key.

`openlearn config set-editor <command> [args...]` stores the editor as an argument list, so multi-argument commands such as `openlearn config set-editor code --wait` or `openlearn config set-editor idea --wait` do not use a shell.
The saved editor takes precedence over `EDITOR`, then `VISUAL`; openLearn defaults to `nvim` when none are configured.

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
openlearn new interview-prep --template algorithms
openlearn new technical-interview-prep --template technical-interview-prep
openlearn new backend-interviews --interview-prep
openlearn resume
```

Run `openlearn templates` to list the bundled Technical Interview Prep, Vim, Git, Python, SQL, algorithms, and other starter outlines.
From the main menu, press `s` to browse and create any bundled starter course or `i` for `New interview course`.
Technical Interview Prep uses a LeetCode-style algorithms and data-structures outline and enters the same interview-prep flow as the shortcut.
When the active interview course has unfinished placement, the menu shows `Continue interview prep` and hides the new-course shortcut.
An explicit `--goal` takes precedence over a template's default goal.
`--interview-prep` is an explicit opt-in that creates a separate learner-owned local profile and offers a short reasoning placement.
Normal topic creation does not create interview metadata or show placement prompts.

Manage the profile and optional placement independently:

```bash
openlearn interview profile backend-interviews
openlearn interview edit backend-interviews weekly_minutes 180
openlearn interview placement backend-interviews defer
openlearn interview placement backend-interviews start
openlearn interview placement backend-interviews resume
openlearn interview placement backend-interviews discard
openlearn interview clear backend-interviews
```

New placement uses one original bundled problem to collect clarification questions and a spoken or typed solution route covering the approach, data structure or technique, edge cases and tests, and time and space complexity.
Enter one line at a time, use `/show` to review the current section, `/undo` to remove its latest line, and use `/done` to submit that section.
The two sections are durable, so `/stop`, EOF, and interruption resume at the exact stage with the saved draft.
The result is a provisional course-start passport that recommends a first activity, records reasoning signals and a practice priority, and explicitly leaves coding fluency unobserved.
It never grants mastery or claims interview readiness.
When a provider is ready, completion continues directly into course planning and the named first activity.
Without a provider, completion still succeeds and prints `openlearn init` plus the exact resume command.
Interruptions are resumable, while explicit discard or profile clearing preserves append-only attempt evidence.
The normal recovery command is `openlearn resume [topic]`.
It returns an unfinished placement to its exact next stage before any provider or source work, then carries a deferred or provisional result into course planning without offering the separate legacy placement quiz.
Course planning receives only a bounded summary of the target, schedule, gap statuses, uncertainty, and recommendations.
Raw reasoning stays in local append-only evidence.
If model-backed teaching is not configured, resume reports the adjacent placement state, confirms that all work is saved, and gives both the configuration and resume commands before changing course state.
Dry-run, mock mode, and keyless localhost providers remain available without a hosted API key.
Initial reasoning placement does not open an editor, create a coding workspace, execute code, or require Docker or Podman.
Existing coding-placement v1 and v2 records remain readable under their recorded lifecycle instead of being reinterpreted as reasoning evidence.
Resuming an active legacy attempt offers the recommended short placement, continued legacy placement, or a safe exit, and requires confirmation before replacing the active attempt.
Real implementation, testing, and revision happen later through course coding drills.
Those drills can open the configured editor and use secure `/check` execution, while a controlled-editor mock interview remains a later interview experience rather than a placement requirement.

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
Before a model-backed REPL turn is sent, openLearn stores the answer in that topic's local state file.
If the turn or process fails, the next REPL restores the answer so pressing Enter resubmits it or typing replaces it.
The saved answer is cleared after the tutor response is appended, with at-least-once recovery if the process stops between those two writes.
Plain requests such as "continue", "move on", or "skip" advance the current slide; if the wording includes a preference such as "I don't need this", openLearn stores it as a learner preference.

Inside the REPL:

```text
openlearn> /n
openlearn> continue
openlearn> /done
openlearn> /review
openlearn> /drill
openlearn> /drill --leetcode
openlearn> /check
openlearn> /check --reduced-isolation
openlearn> /videos --n 3 registers
openlearn> /status
openlearn> /q
```

Use `/help --all` for the full REPL command list.

`/drill` generates a topic-aware Python exercise, while `/drill --leetcode` selects one from the bundled interview-practice bank without calling the model.
After the drill opens in your configured editor, implement the function, save the file, return to openLearn, and run `/check`.
The secure default runs an attempt copy and a separate test harness in a bounded OCI container with no network, a read-only root, a non-root user, dropped capabilities, and CPU, memory, process, output, file, and wall-time limits.
The pinned runner image is never pulled automatically.
Run `openlearn doctor` for runtime status and the exact explicit image-acquisition command when setup is incomplete.
Ordinary offline checks use only the already-present digest-pinned image.
`/check --reduced-isolation` is an explicit per-run fallback that executes a local subprocess with warnings.
It is not a sandbox and learner code can still access account files and the network or escape best-effort limits.
If the editor cannot be launched, openLearn keeps the drill active and prints its path so you can open it manually before running `/check`.

## Command Surface

| Area | Commands |
| --- | --- |
| Setup | `init`, `doctor`, `config show`, `config set-key`, `config set-model`, `config set-base-url`, `config set-editor`, `config clear-key` |
| Topics | `new`, `delete`, `list`, `recent`, `active`, `edit`, `status`, `summary`, `stats`, `repair` |
| Learning | `menu`, `quick`, `repl`, `chat`, `resume`, `next`, `review`, `chapter`, `due` |
| Sources | `import <topic> <file>`, `import <topic> --url <url>`, `import <topic> --scan <dir>`, `paste` |
| Practice | `videos`, REPL `/drill`, REPL `/check` |
| Interview prep | `interview setup`, `interview profile`, `interview edit`, `interview placement`, `interview clear` |
| Utilities | `web`, `templates`, `test`, `tui` |

Model-backed commands require an API key for non-local providers, but localhost OpenAI-compatible endpoints such as Ollama may be used keylessly.
`OPENLEARN_MOCK=1` runs model-backed tests without any provider call.
Transient provider failures such as rate limits, server errors, URL errors, and timeouts are retried up to three times with bounded backoff before surfacing an error.
`chat`, `resume`, `next`, and `review` accept `--dry-run` to print the rendered prompts instead of calling the model, leaving all local files untouched.
`stats` defaults to an all-topic Rich dashboard with streaks, this week's study minutes, review forecast, and mastery by unit; pass a topic slug to focus on one topic, or `--text` / `--share` for a compact shareable summary.
`repair` fills missing topic metadata defaults and can recover simple corrupt JSON frontmatter such as trailing commas or missing closing braces/brackets, writing a `.bak` file before rewriting the topic.
`repl` also has the `shell` alias.

## Local Files

- `learning-topics/*.md`: user-owned topic notes, course plan, metadata, and session log.
- `learning-topics/<slug>.state.json`: dynamic learner model and any in-flight REPL answer.
- `learning-topics/<slug>.events.jsonl`: append-only learning events.
- `learning-topics/<slug>.interview.json`: optional editable interview profile, placement status, evidence references, and provisional recommendations.
- `learning-topics/<slug>/context/`: imported source text, manifests, bundles, and summaries.
- `learning-topics/drills/<slug>/`: generated drill files.
- `state.json`: active-topic state.
- `config.json`: saved provider settings and optional API key.

These files are ignored by Git because they may contain private notes, class material, or credentials.
See [the shareable topic format](docs/TOPIC_FORMAT.md) for the Markdown plus JSON structure and guidance on copying a topic safely.

## License

openLearn is licensed under AGPL-3.0-or-later.
See `LICENSE`.
