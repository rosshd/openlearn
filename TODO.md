# openlearn TODOs

## Active

- [ ] **Fast Learn mode** - add a main-menu flow for importing one file or folder and immediately starting a generated lesson path with no schedule confirmation.
- [ ] **Tag and push current release** - code + tests are green; confirm version target before tagging.

## Backlog

- [ ] **Per-concept review grading** - current due-review prompt records one easy/hard/missed result for the whole session; later support mixed outcomes per concept
- [ ] **Source import polish** - support Fast Learn quality: better source grounding, faster summaries for small files, and efficient depth scaling for folders or harder material
- [ ] **Provider abstraction** - extract the `urlopen` chat-completion call into a small `ModelProvider` class so adding Anthropic native / Ollama doesn't require touching prompt logic
- [ ] **Dry-run prompt preview** - `--dry-run` flag on any model-backed command that prints the full prompt instead of calling the API
- [ ] **`openlearn repair` improvements** - detect and fix corrupt JSON frontmatter (unclosed braces, trailing commas)
- [ ] **FMHY integration** - contact maintainers for license/permission; see docs/DEPENDENCIES.md

## Done (recent)

- [x] **v0.5.0 Phase 1** - Rich UI rewrite + platformdirs data dir + migration notice
- [x] **v0.5.0 Phase 2** - Review scheduling polish (status-bar count, menu `r` shortcut, inline easy/hard/missed, rich `due` table, `/review --due`)
- [x] **v0.5.0 Phase 3** - PDF/DOCX/URL import + `--scan` folder auto-import with checksum dedupe
- [x] **v0.5.0 Phase 4** - Coding drill sandbox (`/drill`, `/check`, curated bank, pytest integration)
- [x] **v0.5.0 Phase 5** - CLI review prompt, real per-concept Ebisu SRS, import dedupe, User-Agent, `.bak` snapshots, config-mask audit, happy-path integration test
- [x] **v0.5.0 Phase 6** - YouTube suggestions (zero-dep `ytInitialData` parser), `/videos` + `openlearn videos`, opt-in `suggest_videos`, version bump to 0.5.0 + `--version`
- [x] **v0.5.0 test hardening** - pexpect workflow smoke tests + JSON tutor regression evals for observed REPL/tutor behavior failures
- [x] **Menu import parity** - new-course + Context-files menus now offer file/URL/folder import via a shared import core; fixed mislabeled `.txt` options, version-tracked User-Agents, video-suggestion dedup on fetch failure
- [x] Phase 1-4 (v0.4.0): tutor correctness, system-prompt quality, session compression, test coverage
- [x] v0.4.0: Answer evaluation loop, dynamic prompt, /done UX, review scheduling scaffold, Rich UI groundwork, version bump + tag
