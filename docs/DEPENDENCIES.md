# Dependencies

All additions must be compatible with AGPL-3.0-or-later and the local-first product promise.
Prefer small direct libraries over broad orchestration frameworks.

## Approved

| Library | License | Use |
| --- | --- | --- |
| `rich` | MIT | Terminal formatting, Markdown-ish output, spinners |
| `platformdirs` | MIT | Cross-platform config and data directories |
| `pdfplumber` | MIT | PDF text extraction |
| `requests` | Apache-2.0 | URL import and lightweight web fetches |
| `trafilatura` | Apache-2.0 | Readable web-page extraction |
| `python-docx` | MIT | DOCX import |
| `plotext` | MIT | Terminal progress charts |
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
summary is saved under learning-topics/context/<slug>/
```

File import uses format-specific parsers and the same source-summary path.
Imports are deduplicated by checksum.

## External Resource Notes

FMHY is useful as a human-discoverable resource index.
Do not redistribute or programmatically ingest its repository until licensing permission is explicit.
User-directed imports of public pages remain acceptable through the normal URL import path.

## Drill Sandbox

Coding drills use no new dependency.
The tutor writes a small Python file with tests, `/check` runs pytest on that file, and the tutor explains failures or confirms success.
