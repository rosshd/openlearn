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

## Rejected Libraries

| Library | Reason |
|---------|--------|
| `pymupdf` | AGPL dual-license (Artifex); requires distributing source or paying — conflicts with project goals |
| `playwright` | ~450MB binary; overkill for static web page extraction |
| `youtube-search-python` | Unmaintained; breaks against modern `httpx` (passes removed `proxies=` kwarg). Replaced in v0.5.0 by a ~40-line `ytInitialData` parser built on our existing `requests` dep — zero new deps, no API key, fully under our control. See `fetch_video_suggestions`/`parse_video_results` in `cli.py`. |
| `newspaper4k` | Pulls in unnecessary NLP/ML deps |
| `py-fsrs` | Not on PyPI; would require vendoring or git submodule |
| `glow` | Go binary, not a Python dependency |

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
