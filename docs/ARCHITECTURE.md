# Architecture

## Current Shape

openLearn currently has one package, `openlearn`, with a small CLI in `src/openlearn/cli.py`.

The CLI owns five concerns for now:

- Topic file creation and parsing.
- Local project/home discovery.
- Prompt construction from topic state.
- Config and active-topic state.
- OpenAI-compatible chat-completion calls.

This is intentionally simple for the MVP. Once behavior is proven, split provider calls, topic storage, and review logic into separate modules.

## Data Ownership

Topic files are user-owned. They should remain readable and editable without openLearn.

The current topic file format is Markdown with JSON metadata between `---` separators:

```md
---
{
  "topic": "Vim",
  "known": [],
  "weak_spots": []
}
---

# Vim

## Notes
```

JSON was chosen instead of YAML to avoid adding a dependency in the first version.

The topic slug is the stable file identifier. For example, `Operating Systems` becomes `operating-systems`, and the file is stored at `learning-topics/operating-systems.md`.

Topic commands update active-topic state in `state.json` so `resume`, `next`, and `edit` can work without repeating the topic name.

## Model Usage

Model-backed commands send:

- Metadata from the selected topic.
- A bounded excerpt of the topic notes.
- Recent session history from the selected topic.
- The user's current prompt or generated instruction.

They do not send every topic or any global learning database.

## Provider Direction

The first provider is OpenAI-compatible chat completions through `OPENAI_API_KEY` and a configurable base URL. This keeps OpenAI, OpenRouter, local proxies, and other compatible endpoints easier to support.

Configuration precedence is:

- Environment variables: `OPENAI_API_KEY`, `OPENLEARN_MODEL`, and `OPENLEARN_BASE_URL`.
- Saved local config in `config.json`.
- Built-in defaults.

The current request target is:

```text
{base_url}/chat/completions
```

Future providers should implement a small interface:

```python
class ModelProvider:
    def complete(self, model: str, system: str, user: str) -> str:
        ...
```

This will allow OpenAI, Anthropic, Gemini, OpenRouter, and Ollama support without changing topic storage.

## Testing

The test suite uses Python's standard `unittest` module:

```bash
python -m unittest
```

Tests set `OPENLEARN_HOME` to temporary directories so they do not read or modify real user data.
