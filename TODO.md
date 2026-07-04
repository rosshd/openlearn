# openlearn TODOs

## Active

- [ ] **Quick Learn validation** - dogfood the file, folder, and public GitHub flows with real study material before release.
- [ ] **Tag and push current release** - confirm `src/openlearn/__init__.py` version target, then push the matching `vX.Y.Z` tag to run the automated release workflow.

## Backlog

- [ ] **Per-concept review grading** - current due-review prompt records one easy/hard/missed result for the whole session; later support mixed outcomes per concept
- [ ] **Source import polish** - improve Quick Learn depth scaling for very large folders and repositories; consider private repository authentication separately
- [ ] **Provider abstraction** - extract the `urlopen` chat-completion call into a small `ModelProvider` class so adding Anthropic native / Ollama doesn't require touching prompt logic
- [ ] **FMHY integration** - contact maintainers for license/permission; see docs/DEPENDENCIES.md

## Done (recent)

- [x] **Quick Learn MVP** - start a separate adaptive topic from a file, bounded folder, or public GitHub repository with no placement or outline approval
- [x] **v0.5.0 Phase 1** - Rich UI rewrite + platformdirs data dir + migration notice
- [x] **v0.5.0 Phase 2** - Review scheduling polish (status-bar count, menu `r` shortcut, inline easy/hard/missed, rich `due` table, `/review --due`)
- [x] **v0.5.0 Phase 3** - PDF/DOCX/URL import + `--scan` folder auto-import with checksum dedupe
- [x] **v0.5.0 Phase 4** - Coding drill sandbox (`/drill`, `/check`, curated bank, pytest integration)
- [x] **v0.5.0 Phase 5** - CLI review prompt, real per-concept Ebisu SRS, import dedupe, User-Agent, `.bak` snapshots, config-mask audit, happy-path integration test
- [x] **v0.5.0 Phase 6** - YouTube suggestions (zero-dep `ytInitialData` parser), `/videos` + `openlearn videos`, opt-in `suggest_videos`, version bump to 0.5.0 + `--version`
- [x] **v0.5.0 test hardening** - pexpect workflow smoke tests + JSON tutor regression evals for observed REPL/tutor behavior failures
- [x] **Menu import parity** - new-course + Context-files menus now offer file/URL/folder import via a shared import core; fixed mislabeled `.txt` options, version-tracked User-Agents, video-suggestion dedup on fetch failure
- [x] **Dry-run prompt preview** - `chat`, `resume`, `next`, and `review` support `--dry-run` to print rendered prompts without calling the API or mutating local files
- [x] **Session resilience** - transient provider failures retry with bounded backoff, failed REPL answers stay available for Enter resubmission, and `openlearn repair` fixes simple corrupt JSON frontmatter with `.bak` backup
- [x] **PyPI release automation** - `vX.Y.Z` tags build and verify distributions, publish through trusted publishing, and create GitHub releases with artifacts
- [x] Phase 1-4 (v0.4.0): tutor correctness, system-prompt quality, session compression, test coverage
- [x] v0.4.0: Answer evaluation loop, dynamic prompt, /done UX, review scheduling scaffold, Rich UI groundwork, version bump + tag
