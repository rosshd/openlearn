# openlearn TODOs

## Active

- [ ] **Tutor behavior polish** — audit the 6 identified issues from the tutor behavior improvement plan; verify each is resolved or create a task per remaining issue
- [ ] **`/done` UX** — after advancing a slide, print a short confirmation so the learner knows the slide changed (currently silent)
- [ ] **Session context trimming edge cases** — test `compact_session_context` against very short sessions and sessions with only system turns; ensure no crash or empty output

## Near-term (v0.5.0 direction)

- [ ] **Review scheduling** — add lightweight review items to topic metadata (concept, due date, difficulty, source unit); generate them from weak answers and completed lessons
- [ ] **Due review indicator** — show `Reviews due: N` in menu/status bar; make due review one key away
- [ ] **Mark review result** — quick `easy / hard / missed` input after each review item; update `last_reviewed`, `review_due`, and `weak_spots`
- [ ] **`openlearn due`** — CLI command to list all topics with due reviews across the workspace

## Near-term (new features)

- [ ] **YouTube video suggestions** — after hard concepts, tutor suggests 2-3 short relevant YouTube videos (title + URL + duration); use `youtube-search-python` (no OAuth, public search only); print inline in REPL after lesson or on `/videos` command
- [ ] **Coding problem sandbox** — REPL command `/drill` or `/challenge` generates a LeetCode-style `.py` file with problem docstring + test cases, opens it in VS Code via `subprocess.Popen(['code', filepath])`, then `/check` runs `pytest` on it and feeds results back to tutor for feedback; zero new deps beyond pytest
- [ ] **PDF import** — extend `openlearn import` to accept `.pdf` files using `pdfplumber` (lightweight, pure Python); extracts text per page, feeds into existing source summarizer
- [ ] **Class folder auto-scan** — `openlearn import --scan <dir>` walks a directory and imports all valid files (`.pdf`, `.md`, `.txt`, `.docx`) in one pass; dispatches to pdfplumber for PDFs, python-docx for .docx, plain read for others; skips already-imported files by checksum

## Backlog

- [ ] **Source import polish** — `openlearn import` and `openlearn paste` exist but the summarizer prompt and UX around source grounding need a pass before they're daily-usable
- [ ] **Provider abstraction** — extract the `urlopen` chat-completion call into a small `ModelProvider` class so adding Anthropic native / Ollama doesn't require touching prompt logic
- [ ] **Dry-run prompt preview** — `--dry-run` flag on any model-backed command that prints the full prompt instead of calling the API; useful for debugging tutor behavior
- [ ] **Topic file backup before rewrites** — write a `.bak` snapshot of the topic file before any metadata rewrite operation; avoids data loss from a bad model parse
- [ ] **`openlearn repair` improvements** — currently fills missing defaults; extend it to detect and fix corrupt JSON frontmatter (unclosed braces, trailing commas)
- [ ] **Test: menu happy paths** — integration tests covering the full menu flow: create → start course → answer → continue → review → switch → delete
- [ ] **`config show` masked key** — currently shows partial key; confirm masking is consistent across `config show` and error output

## Done (recent)

- [x] Phase 1: Tutor correctness bugs (review flag, /done blocking, advance_slide guard, update_course_position clamp)
- [x] Phase 2: System prompt quality (generation_system_prompt cleanup, placement_evaluation uses METADATA_EXTRACTOR_SYSTEM)
- [x] Phase 3: Session history compression + first lesson word limit (trim_words, FIRST_LESSON_WORD_LIMIT, compact_session_context)
- [x] Phase 4: Test coverage (105 tests passing)
