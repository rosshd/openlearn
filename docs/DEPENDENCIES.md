# Dependency Research & Third-Party Resources

This document tracks evaluated libraries, external resources, and integration candidates.
All additions must be license-compatible with AGPLv3.

---

## Approved Libraries

These have been evaluated and approved for use in openlearn.

| Library | License | Size | Use case | Status |
|---------|---------|------|----------|--------|
| `rich` | MIT | ~600KB | Replace hand-rolled ANSI in `ui.py`; Markdown rendering, syntax highlighting, spinners | Shipped — v0.5.0 |
| `platformdirs` | MIT | ~10KB | Cross-platform config/data directory resolution; replaces fragile `OPENLEARN_HOME` CWD fallback | Shipped — v0.5.0 |
| `pdfplumber` | MIT | ~500KB + deps | PDF text extraction for `openlearn import`; better quality than pypdf for structured docs | Shipped — v0.5.0 |
| `requests` | Apache 2.0 | ~130KB | HTTP client for URL import + YouTube results scraping; replaces stdlib `urllib` for user-facing web fetches | Shipped — v0.5.0 |
| `trafilatura` | Apache 2.0 | ~150KB | Web page content extraction for `openlearn import --url`; strips nav/ads/boilerplate automatically | Shipped — v0.5.0 |
| `python-docx` | MIT | ~500KB | `.docx` support for `openlearn import --scan` folder auto-import | Shipped — v0.5.0 |
| `ebisu` | Unlicense (public domain) | ~15KB | Math-based forgetting curve for SRS scheduling; optional upgrade from fixed-interval review | Shipped (optional extra) — v0.5.0 |

---

---

## Candidates — Under Evaluation

Libraries being considered for v0.6.0 and beyond. Not yet approved.

### `plotext` — Terminal Charts
**License:** MIT | **Size:** ~50KB | **Deps:** none beyond stdlib

Terminal-native line, bar, and scatter charts. No X11, no browser, no image rendering.
Single `pip install plotext`. Used for `openlearn stats` progress charts.

Evaluation notes:
- Renders cleanly inside Rich console output.
- Supports Unicode braille-style sparklines and ASCII fallback.
- Actively maintained as of 2024.
- **Verdict: approve for v0.6.0 Phase 2.**

---

### `litellm` — Unified LLM API
**License:** MIT | **Size:** ~2MB + optional deps | **Deps:** `httpx`, `pydantic`

Drop-in wrapper around 100+ LLM providers (OpenAI, Anthropic, Groq, Ollama, Bedrock,
Azure, Vertex, etc.) using a single `completion()` call. Handles streaming, retries,
token counting, and fallbacks.

Key benefit: replaces the current `openrouter-only` call with provider-agnostic code.
Users can set `model=ollama/llama3.2` for fully local, private study sessions.

Evaluation notes:
- Adds ~2MB to the install; acceptable for the provider abstraction value.
- Supports `stream=True`, `response_format` (JSON mode), and async.
- AGPL-compatible (MIT).
- Alternative: build a thin manual abstraction (~100 lines) to avoid the dependency.
  LiteLLM is the right call unless install size becomes a hard constraint.
- **Target: v0.7.0 multi-provider phase.**

---

### `deepeval` — LLM Conversation Evaluation
**License:** Apache 2.0 | **Size:** ~5MB + deps | **Deps:** `openai`, `pydantic`

Framework for evaluating LLM outputs using AI judges. Supports `GEval` (custom rubrics),
`AnswerRelevancy`, `Faithfulness`, `ContextualRecall`, and conversational metrics.

Used for testing tutor conversation quality beyond what pexpect smoke tests can check —
e.g., does the tutor offer a hint before giving the answer? Does it acknowledge a
prerequisite gap?

Evaluation notes:
- `[dev]` dependency only; not shipped to end users.
- Requires an API call per test run (uses GPT-4o as the judge by default; can override).
- Mark tests `@pytest.mark.slow` and exclude from default `python -m unittest` run.
- **Target: v0.6.0 Phase 5.**

---

### `instructor` — Structured LLM Outputs
**License:** MIT | **Size:** ~200KB | **Deps:** `openai`, `pydantic`

Wraps any OpenAI-compatible API to return Pydantic models instead of raw strings.
Handles retry logic when the model returns malformed JSON.

Relevant for v0.6.0 Phase 1 structured evaluation — ensures the `{"verdict": ...,
"score": ..., "gap": ...}` evaluation contract parses reliably.

Evaluation notes:
- May be unnecessary if native `response_format={"type":"json_object"}` is reliable
  enough on the target models (claude-sonnet-4-5 and gpt-4o both support it well).
- If JSON mode is sufficient, skip `instructor` to keep the dep count low.
- **Evaluate Phase 1 implementation first; add instructor only if JSON mode proves flaky.**

---

### `docling` — High-Quality Document Parsing
**License:** MIT | **Size:** ~50MB with ML models | **Deps:** `torch` (optional)

IBM Research open-source library (2024) that converts PDF, DOCX, PPTX, HTML, and
images to clean structured Markdown. Significantly better layout reconstruction than
`pdfplumber` for academic PDFs, textbooks, and slides with tables/formulas.

Evaluation notes:
- The ML-powered mode requires `torch` (~800MB) — far too heavy as a default dep.
- The lightweight mode (no `torch`) still outperforms `pdfplumber` for clean PDFs.
- Could be offered as `pip install openlearn[docling]` optional extra.
- **Hold for v0.7.0+ when import quality becomes a user complaint.**

---

### `textual` — Rich TUI Framework
**License:** MIT | **Size:** ~1MB | **Deps:** `rich` (already installed)

Full-featured TUI framework from the makers of Rich. Supports reactive widgets,
data tables, input forms, progress bars, and layout panels — all keyboard-driven.

Would power an optional `openlearn tui` mode with a proper dashboard: course list,
progress charts, study calendar heatmap, lesson view.

Evaluation notes:
- High build cost — requires redesigning the current print-based UI for the TUI path.
- Worth it for v1.0.0 polish; not a priority until core tutor quality is strong.
- **Target: v0.8.0 optional TUI mode.**

---

### `sentence-transformers` — Local Embeddings
**License:** Apache 2.0 | **Size:** ~500MB with models | **Deps:** `torch`

Local embedding generation for semantic search across course notes, context files,
and session logs. Would enable "search my notes" and concept similarity features.

Evaluation notes:
- Extremely heavy; only viable as an optional extra for power users.
- Better option: use the embedding endpoint on whichever API the user already has
  configured (OpenAI `text-embedding-3-small`, Ollama `nomic-embed-text`).
- **Defer until semantic search is a validated user need.**

---

## Rejected Libraries

| Library | Reason |
|---------|--------|
| `pymupdf` | AGPL dual-license (Artifex); requires distributing source or paying — conflicts with project goals |
| `playwright` | ~450MB binary; overkill for static web page extraction |
| `youtube-search-python` | Unmaintained; breaks against modern `httpx` (passes removed `proxies=` kwarg). Replaced in v0.5.0 by a ~40-line `ytInitialData` parser built on our existing `requests` dep — zero new deps, no API key, fully under our control. See `fetch_video_suggestions`/`parse_video_results` in `cli.py`. |
| `newspaper4k` | Pulls in unnecessary NLP/ML deps |
| `py-fsrs` | Not on PyPI; would require vendoring or git submodule |
| `glow` | Go binary, not a Python dependency |
| `langchain` | Extremely heavy (dozens of transitive deps); solves problems openlearn doesn't have; opaque abstractions that fight local-first design |
| `llama-index` | Same concerns as langchain; overkill for the current scope |

---

## External Resource: FMHY (freemediaheckyeah)

**What it is:** A massive community-curated index of free online resources across every topic —
learning materials, tools, media, and more. Hosted at [fmhy.net](https://fmhy.net) with source
at [github.com/fmhy/FMHY](https://github.com/fmhy/FMHY).

**Why it's relevant:** The wiki contains an extensive categorized list of free learning resources
(courses, tutorials, references) that would be highly valuable for the tutor to surface when a
learner is stuck or wants supplementary material.

**License status:** The FMHY repository currently has **no license file**. Under copyright law
this means all rights reserved by default, even though the content is publicly accessible.
Programmatic use or redistribution is legally ambiguous without explicit permission.

**What we can do now:**
- Reference fmhy.net as a suggested manual resource in tutor output
- Use `trafilatura` to let users import any specific FMHY page as a context file themselves

**Action item (when project is further along):**
> Contact the FMHY maintainers and request a permissive license (CC0 or CC-BY) or explicit
> permission for programmatic read-only use. The FMHY community is open and collaborative —
> this is likely to be granted. Once licensed, the Markdown wiki structure is clean enough
> to parse directly from raw GitHub URLs without any scraping infrastructure.
>
> Suggested integration after permission: `openlearn import --fmhy <category>` fetches the
> relevant FMHY wiki page, extracts resource links, and saves them as a context file for
> the active topic.

---

## Web Import Architecture (`openlearn import --url`)

Planned feature using `requests` + `trafilatura`:

```
openlearn import <topic> --url https://example.com/article
  → requests fetches the page HTML
  → trafilatura extracts clean article text (strips nav, ads, boilerplate)
  → existing source summarizer pipeline processes the text
  → summary saved as context file under learning-topics/context/<slug>/
```

This reuses the existing `summarize_context_file` path with no new prompt logic needed.

---

## Coding Problem Sandbox

Zero new dependencies. Planned architecture:

```
/drill  →  tutor generates a .py file with problem docstring + test cases
        →  subprocess.Popen(['code', filepath]) opens it in VS Code
/check  →  runs pytest on the file, feeds results back to tutor
        →  tutor gives targeted feedback on the failure
```
