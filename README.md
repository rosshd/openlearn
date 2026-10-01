# openlearn

[![Tests](https://github.com/rosshd/openlearn/actions/workflows/tests.yml/badge.svg)](https://github.com/rosshd/openlearn/actions/workflows/tests.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python 3.11-3.13](https://img.shields.io/badge/python-3.11--3.13-blue.svg)](pyproject.toml)

openlearn is a work-in-progress, local-first AI tutor.
A learning session starts with a goal, teaches one focused idea, offers a useful optional check, gives feedback, and saves progress for later retrieval.
Courses and learner records stay in files under your local learner home.
Model-backed lessons use a provider account you configure or a local OpenAI-compatible endpoint.
The web app and keyboard-first CLI support the same teaching policy and learner state, with presentation features suited to each interface.

## What a session looks like

This exchange is illustrative, written for this README, and is not a transcript or live evaluation result.

**Tutor:** `alias = items` gives `alias` another name for the same list.
`copy = items[:]` makes a separate shallow copy.

**Check:** If `items = [1, 2]`, `alias = items`, and `copy = items[:]`, then `alias.append(3)`, what is `items`?

**Learner:** `[1, 2]`, because I appended to `alias`.

**Feedback:** Both names refer to one list, so `items` is `[1, 2, 3]`.
The slice copy stays `[1, 2]`.

**Return to practice:** After a later lesson, predict `values[0]` after `values = [4]`, `again = values`, and `again[0] = 9`.

Saved answers, tutor judgments, and course progress show what openlearn recorded.
They do not establish that a learner retained an idea or can use it in a new situation.
The separate [outcome evaluation](docs/OUTCOME_EVAL.md) is an opt-in scripted evaluation lane, not a claim about learner outcomes.

## Quickstart

Use Python 3.11 through 3.13 on macOS, Linux, or Windows.
To run the current source checkout on macOS or Linux:

```bash
git clone https://github.com/rosshd/openlearn.git
cd openlearn
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
openlearn
```

Set `python` to a supported version before creating the environment.
The local web app opens in your browser.
Create a course or choose a bundled template, then configure a provider before starting a model-backed lesson.
Use `openlearn cli` for the terminal interface.
See [installation](docs/INSTALL.md) for published-package installation, Windows setup, upgrades, uninstalling, headless launch, and optional code execution.

To install a published release instead, run `python -m pip install --upgrade openlearn` in an environment using Python 3.11 through 3.13.
A published release may not include changes from the current source checkout.
For the contributor setup, including development dependencies, see [Development](docs/DEVELOPMENT.md).

## Start learning

The web app can create a custom course or start from a bundled template.
Technical Interview Prep is a supported reference course and uses a short confidence survey to tailor its route to role goals and topic familiarity.
The survey does not require an editor or a coding test.

Lessons teach one focused idea at a time.
Checks are recommended but optional for refreshers.
Moving past a check does not award mastery credit, and the concept remains available for later practice.

Quick Learn starts a temporary focused course from source material:

```bash
openlearn quick ./study-guide.pdf
openlearn quick ./notes-folder --name "Biology review"
openlearn quick https://github.com/owner/repository
```

Quick Learn accepts supported text and code files, PDFs, DOCX files, bounded local folders, and public GitHub repositories.
Imports skip hidden directories, generated files, symlinks, binaries, oversized files, and secret-like names.
Imported code is read as text and is never executed during import.

For a selected course with imported class notes, the opt-in CLI source mode previews its outbound request before asking for confirmation:

```bash
openlearn chat my-course "quiz me on the current lesson" --source-mode
```

Source mode is off by default and requires the configured OpenRouter endpoint with `deepseek/deepseek-v4.1-flash`.
Each request requires typing `send source request` after reviewing the preview.
It sends bounded, screened selected-course excerpts, the current lesson answer or question, the pending Check, and at most two relevant lesson exchanges.
It excludes unrelated profiles, goals, preferences, placement, and private notes, and skips optional metadata extraction, videos, and coding-drill actions.
Screening is limited; inspect the preview for names or sensitive details before confirming.
No provider grant or opt-in preference is saved.
Tutor responses and source provenance remain in local history, which ordinary later tutoring may use under its existing behavior.
This is not an app-wide privacy setting.

The saved excerpt ledger records source IDs, checksums, and actual extracted-text line ranges, not original slide/page numbers or proof that a generated claim is correct.
Missing, stale, unsafe, or over-budget material is withheld rather than silently replaced by summaries.
Image-only formulas and direct PPTX ingestion are not supported; review a text/PDF export locally first.
In the web lesson, open Options and choose "Use screened class sources for this turn", review the local preview, then choose "Send screened request".
Every answer, question, or navigation request needs a fresh approval; cancellation sends nothing.
The stored grading key is hidden in the learner preview but remains in the scoped judge request when needed.
Changed requests or source material require a new preview.

### Optional math presentation

Web lessons, saved history, and tutor chat render explicitly marked simple algebra with a local KaTeX renderer.
Use `\(x^2\)` for inline notation and standalone `\[` / `\]` lines for a display equation.
Explain what each equation means in ordinary language beside it.
Matrices, fractions, column vectors, roots, subscripts and ordinary equations are supported within bounded input limits.
Code, currency text, streaming previews and unmarked arrays are never converted into math.
Unsupported notation appears visibly as `Math (text)` rather than losing meaning or breaking the lesson.
This first subset excludes custom macros, external resources, arbitrary styles, decorated/font-variant symbols and advanced alignment environments.
CLI output and backups retain the original notation and explanatory prose; no saved topic is rewritten.
MathML-only output preserves the existing CSP and does not require a CDN or font download.
The bundled KaTeX 0.19.0 script comes from the official npm `katex` archive and retains its MIT license in `src/openlearn/web/static/vendor/katex/LICENSE`.
Its SHA-256 is `103a53763cc033bba8d175bf3f0ba597c3505c9b6747dd3f2c7bc2a6bfcc8ae7`.

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
The optional TUI remains supported in its current form.

Python code checks are optional; see [installation](docs/INSTALL.md) for runtime and isolation details.

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
