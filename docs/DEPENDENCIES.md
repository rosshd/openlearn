# Dependencies

All additions must be compatible with AGPL-3.0-or-later and the local-first product promise.
Prefer small direct libraries over broad orchestration frameworks.

## Approved

| Library | License | Use |
| --- | --- | --- |
| `rich` | MIT | Terminal formatting, Markdown-ish output, spinners, stats dashboard |
| `platformdirs` | MIT | Cross-platform config and data directories |
| `pdfplumber` | MIT | PDF text extraction |
| `requests` | Apache-2.0 | URL import and lightweight web fetches |
| `trafilatura` | Apache-2.0 | Readable web-page extraction |
| `python-docx` | MIT | DOCX import |
| `ebisu` | Unlicense | Optional forgetting-curve SRS extra |

## Candidates

| Library | License | Use | Decision |
| --- | --- | --- | --- |
| `litellm` | MIT core | Unified provider API | Consider when multi-provider support exceeds the thin current interface |
| `deepeval` | Apache-2.0 | AI-judge conversation evals | Dev-only slow lane for tutor quality |
| `instructor` | MIT | Structured model outputs | Add only if native JSON mode is too flaky |
| `docling` | MIT | Higher-quality document parsing | Optional extra only; default install is too heavy if ML deps are required |
| `textual` | MIT | Full TUI | Later UI path, not core tutor quality |
| `sentence-transformers` | Apache-2.0 | Local embeddings | Defer unless semantic search becomes validated |

## Rejected

| Library | Reason |
| --- | --- |
| `pymupdf` | AGPL dual-license creates distribution ambiguity |
| `playwright` | Browser binary is too heavy for static content extraction |
| `youtube-search-python` | Replaced by a small parser over existing `requests` |
| `newspaper4k` | Pulls unnecessary NLP dependencies |
| `py-fsrs` | Not on PyPI at the time of evaluation |
| `glow` | Go binary, not a Python dependency |
| `langchain` | Too broad and opaque for this local-first CLI |
| `llama-index` | Same concern as LangChain |

## Import Architecture

URL import:

```text
requests fetches HTML
trafilatura extracts readable text
source summarizer compresses it
summary is saved under learning-topics/<slug>/context/
```

File import uses format-specific parsers and the same source-summary path.
Imports are deduplicated by checksum.
Quick Learn repository import uses the system `git` executable for shallow public GitHub clones with prompts and hooks disabled; it adds no Python dependency and treats cloned files as inert text.

## External Resource Notes

FMHY is useful as a human-discoverable resource index.
Do not redistribute or programmatically ingest its repository until licensing permission is explicit.
User-directed imports of public pages remain acceptable through the normal URL import path.

## Drill Code Runner

Coding drills add no Python package dependency.
Secure `/check` execution requires an existing Docker or Podman installation and an explicitly acquired digest-pinned official Python image.
openLearn neither installs the runtime nor pulls the image automatically.
The generic per-call worker uses only the Python standard library inside the image.
Run `openlearn doctor` to inspect local readiness and print the explicit acquisition command.
Initial technical-interview placement is conversation-only and does not use the editor, runner, Docker, Podman, or the pinned image.
These dependencies apply only when the learner reaches a later coding drill or other executable course activity.
