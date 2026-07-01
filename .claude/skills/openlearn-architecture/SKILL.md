---
name: openlearn-architecture
description: >
  Use when changing openlearn storage, topic parsing, config precedence, imports,
  provider calls, event logging, or module boundaries.
---

# openlearn architecture

## Shape

- `src/openlearn/cli.py` currently owns CLI commands, REPL, menu flow, prompts, storage orchestration, imports, and provider calls.
- Keep broad refactors out of feature fixes unless the task explicitly asks for a module split.
- If splitting code, prefer small seams around provider calls, topic storage, import handling, and tutor policy.

## Storage

- Topic files are user-owned Markdown with JSON metadata between `---` separators.
- The topic slug is the stable file identifier under `learning-topics/<slug>.md`.
- Dynamic learner state may live in metadata, `<slug>.state.json`, and `<slug>.events.jsonl`.
- Event logs are append-only.
- Dynamic metadata includes pending questions, learner preferences, active drills, concept attempts, quiz state, imported checksums, and answer status.
- Pending questions can be free response or multiple choice, with or without a stored answer key.
- Learner preferences are durable constraints extracted from explicit navigation such as skipped material.
- Never mutate or commit real user topics, imported context, `state.json`, `config.json`, API keys, or `.env`.

## Config And Providers

- Configuration precedence is environment variables, then `config.json`, then built-in defaults.
- Provider calls target OpenAI-compatible chat completions at `{base_url}/chat/completions`.
- Keep model-backed commands bounded to the selected topic, relevant metadata, bounded notes, recent session context, and the current prompt.
- Do not send all topics or a global learner database.

## Imports

- File and URL imports save context under `learning-topics/context/<slug>/`.
- Use structured parsers already in dependencies: `pdfplumber`, `python-docx`, `requests`, and `trafilatura`.
- Deduplicate imports with checksums.

## Interactive UI

- The REPL coalesces quick multiline paste into a single learner message.
- Natural `continue`, `move on`, and `skip` wording advances the slide instead of going through answer grading.

## Tests

- Storage or provider changes need unit coverage plus `make check`.
- Use mocked, isolated CLI flows with `OPENLEARN_MOCK=1` and temporary `OPENLEARN_HOME`.
