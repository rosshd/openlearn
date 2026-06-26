# openlearn — Claude Code context

Local-first AI tutoring CLI. Python, editable install via `pip install -e .`. Entry point: `openlearn`.

## Key files

- `src/openlearn/cli.py` — all commands, REPL, tutor prompt construction, model calls
- `src/openlearn/constants.py` — limits and prompt constants (FIRST_LESSON_WORD_LIMIT, TUTOR_FORMAT_RULES, etc.)
- `src/openlearn/models.py` — Topic, TopicSummary, PendingContext dataclasses
- `src/openlearn/text.py` — text helpers (trim_words, compact_session_context, parse_metadata_update, etc.)
- `src/openlearn/ui.py` — formatting helpers
- `tests/` — unittest suite, run with `python -m unittest`

## Data layout

- `learning-topics/*.md` — user-owned topic files (Markdown + JSON frontmatter between `---`)
- `learning-topics/<slug>.state.json` — dynamic learner model (concept records, counters, quiz history); machine-written, not meant for hand-editing
- `learning-topics/<slug>.events.jsonl` — append-only event log (`answer_judged`, `difficulty_changed`, etc.); never mutated
- `state.json` — active-topic state
- `config.json` — API key, model, base URL (gitignored)
- `learning-topics/context/<slug>/` — imported source summaries

## Provider

OpenAI-compatible chat completions. Config precedence: env vars → `config.json` → built-in defaults.
Default: OpenRouter (`https://openrouter.ai/api/v1`), model `anthropic/claude-sonnet-4-5`.

## Running tests

```bash
python -m unittest
```

Tests use temporary `OPENLEARN_HOME` dirs; no API key required.

## Active work & todos

See [TODO.md](TODO.md) for current tasks and backlog.
