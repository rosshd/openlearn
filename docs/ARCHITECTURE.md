# Architecture

This is the human-readable architecture summary.
Agents should use `.claude/skills/openlearn-architecture/` for operational rules.

## Current Shape

openLearn is a Python CLI with one package, `openlearn`.
`src/openlearn/cli.py` still owns most behavior: commands, REPL, menu flow, topic storage orchestration, prompt construction, imports, and provider calls.

Supporting modules:

- `constants.py`: prompt constants, defaults, limits, profile values, and option labels.
- `models.py`: dataclasses for topic and pending-context state.
- `text.py`: parsing, trimming, metadata-update helpers, answer-key extraction, and context compaction.
- `ui.py`: terminal formatting and Rich output helpers.

Split only when it pays for itself.
Likely split points are provider calls, topic storage, import handling, and tutor policy.

## Storage

Topic files are user-owned Markdown with JSON metadata between `---` separators.
JSON avoids a YAML dependency and keeps the file editable.

```md
---
{"topic": "Vim", "known": [], "weak_spots": []}
---

# Vim
```

The slug is the stable file identifier at `learning-topics/<slug>.md`.
Runtime state can also live in `<slug>.state.json`, `<slug>.events.jsonl`, `state.json`, imported context directories, and drill directories.
Event logs are append-only.
Writes use per-topic lock files with `fcntl.flock` on POSIX and `msvcrt.locking` on Windows.

Important dynamic metadata includes pending questions, answer status, concept attempts, rolling pass rate, quiz state, active drill path, imported checksums, learner preferences, structured course completion, and per-slide concept coverage.
Pending questions may be multiple choice with an answer key, multiple choice without a stored key, or free response.
Learner preferences capture explicit navigation choices such as skipped material and should constrain future tutor turns.
Quick Learn topics also store `learning_mode`, `quick_source_type`, `quick_source_label`, and `coverage_contract` so they can remain visibly separate and enforce source-grounded concept coverage.

## Model Calls

Model-backed commands send only selected-topic context:

- Topic metadata and relevant learner state.
- Bounded notes and recent session history.
- Imported context summaries when relevant.
- The current learner prompt or generated instruction.

Configuration precedence is environment variables, then `config.json`, then defaults.
Provider calls target OpenAI-compatible chat completions at `{base_url}/chat/completions`.
Hosted default base URLs require an API key, while local or custom OpenAI-compatible endpoints may be keyless.
When no key is configured for a keyless endpoint, requests omit the `Authorization` header; a 401 response is reported as an API-key-required endpoint.
For `chat`, `resume`, `next`, and `review`, `--dry-run` prints the rendered system and user messages instead of calling the provider or mutating local files.
Learner-metadata extraction can use `OPENLEARN_EXTRACTOR_MODEL` or `extractor_model`; otherwise it uses the tutor model.
Extractor calls send a reduced metadata snapshot limited to pending checks, focus, known concepts, weak spots, and review due items.

## Source Ingestion

Normal imports save source summaries and deduplicate by checksum.
Quick Learn accepts one file, one folder, or a public GitHub repository URL, then creates a new topic, writes selected source context, summarizes it, generates a source-grounded course plan, and starts the first lesson without placement or outline approval.
Folder and repository ingestion is bounded to 32 supported files, 200 KB per file, 240,000 selected characters, and a 60,000-character bundle for summary grounding.
The selector prefers README files, package manifests, docs, then non-test source files, and skips hidden/generated directories, secret-like filenames, symlinks, binary files, and unsupported suffixes.
Public GitHub repositories are shallow-cloned with terminal prompts, system config, global config, and hooks disabled, and imported code is never executed.

## Interactive UI

The REPL is line-oriented but coalesces quick multiline paste into one learner message on POSIX terminals.
Windows does not support `select.select` on stdin, so the same input path falls back to one line per learner message.
After a tutor response, learner-metadata extraction is deferred so the next prompt appears immediately.
Natural navigation phrases such as `continue`, `move on`, and `skip` advance the current slide instead of being graded as answers.
Tutor output renders in a Rich panel for interactive terminal sessions, streaming updates redraw the same panel as tokens arrive, and hidden answer or coverage markers are stripped before display.
Multiple-choice options are normalized onto separate lines before Rich Markdown rendering.

## Tests

`make check` is the gate.
Tests use temporary `OPENLEARN_HOME` directories and mock mode where needed so they do not touch real user data.
GitHub Actions also runs `python -m unittest` on Ubuntu, Windows, and macOS for Python 3.11 and 3.13.
Workflow tests that require `pexpect` and a POSIX pty are skipped on Windows with explicit reasons.
