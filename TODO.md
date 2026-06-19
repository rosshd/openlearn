# openlearn TODOs

## Active

- [ ] **v0.5.0 Phase 1** — Rich UI + platformdirs (see docs/V0.5.0.md)

## Near-term (new features — v0.5.0+)

- [ ] **YouTube video suggestions** — `/videos` command + auto-suggest after hard answers; `youtube-search-python`; opt-in via course options
- [ ] **Coding problem sandbox** — `/drill` generates `.py` + test cases, opens in VS Code; `/check` runs pytest and feeds results to tutor
- [ ] **PDF import** — `openlearn import <topic> <file.pdf>` via `pdfplumber`
- [ ] **URL import** — `openlearn import <topic> --url <url>` via `requests` + `trafilatura`
- [ ] **Class folder auto-scan** — `openlearn import <topic> --scan <dir>`; deduplicates by checksum; handles `.pdf`, `.md`, `.txt`, `.docx`

## Backlog

- [ ] **Source import polish** — summarizer prompt and UX around source grounding need a pass before they're daily-usable
- [ ] **Provider abstraction** — extract the `urlopen` chat-completion call into a small `ModelProvider` class so adding Anthropic native / Ollama doesn't require touching prompt logic
- [ ] **Dry-run prompt preview** — `--dry-run` flag on any model-backed command that prints the full prompt instead of calling the API
- [ ] **Topic file backup before rewrites** — `.bak` snapshot before any metadata rewrite; avoids data loss from a bad model parse
- [ ] **`openlearn repair` improvements** — detect and fix corrupt JSON frontmatter (unclosed braces, trailing commas)
- [ ] **`config show` masked key** — confirm masking is consistent across `config show` and error output
- [ ] **FMHY integration** — contact maintainers for license/permission; see docs/DEPENDENCIES.md

## Done (recent)

- [x] Phase 1: Tutor correctness bugs (review flag, /done blocking, advance_slide guard, update_course_position clamp)
- [x] Phase 2: System prompt quality (generation_system_prompt cleanup, placement_evaluation uses METADATA_EXTRACTOR_SYSTEM)
- [x] Phase 3: Session history compression + first lesson word limit (trim_words, FIRST_LESSON_WORD_LIMIT, compact_session_context)
- [x] Phase 4: Test coverage (129 tests passing)
- [x] v0.4.0: Answer evaluation loop, dynamic prompt, /done UX, review scheduling scaffold, Rich UI groundwork, version bump + tag
