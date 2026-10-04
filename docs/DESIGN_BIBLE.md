# OpenLearn design bible

OpenLearn helps a learner turn their own topic or source into a first lesson, then return to saved work. Make that path obvious before adding visual detail.

## Principles

- Give each page one clear identity and each work area one primary action. Course creation means Own topic, Source course, or Quick Learn. Do not offer built-in curricula, prefill their names, or revive them from browser drafts. Preserve existing saved courses and progress.
- Keep the first useful control within the first viewport. Use a compact page bar, readable forms, and short explanations beside the action they explain.
- Use space and separators to group related content. Reserve panels for focused forms, navigable course objects, lessons, alerts, and overlays. Avoid nested boxes and repeated titles.
- Keep learning content readable: bounded line lengths, comfortable line height, and scrolling inside code, math, and long source previews.
- State what happened and what the learner can do next. Preserve drafts on cancel, back, retry, and provider recovery. Never describe generated content as demonstrated mastery.
- Consent and safety are part of the interface. Keep screened source disclosures and explicit approval visible. Visual polish must not change provider restrictions, screening, tutor policy, data storage, or import limits.

These choices follow ApplyQuest's actual compact page-bar and flat-work-area guidance in `docs/plans/2026-08-27-quest-workspace-layout-redesign-plan.md`, its `QuestPageHeader` component, and its shared surface, type, spacing, control, and focus tokens. OpenLearn keeps its own orange/teal identity and local system fonts.

## Tokens

The canonical values live in `src/openlearn/web/static/openlearn.css`. Use existing variables instead of adding page-specific colors.

| Role | Token / rule |
| --- | --- |
| Canvas, panels, raised content | `--canvas`, `--surface`, `--surface-raised`; warm neutral light surfaces and subdued dark surfaces |
| Text | `--graphite` body/heading, `--graphite-soft` secondary; never reduce essential text with opacity |
| Boundaries | `--line` quiet separators, `--line-strong` controls and overlays |
| Primary action | `--orange` with `--action-ink`; `--orange-ink` for small accent text |
| Saved/source/progress state | `--teal`, `--teal-soft` |
| Errors/destruction | `--error`; `--danger` is its compatibility alias |
| Radius | `--radius` 10px controls; `--radius-panel` 16px panels/dialogs |
| Spacing | 4/8/12/16/24/32px rhythm; `--space-section` 20–32px; form field label gap 0.4rem |
| Typography | Local system sans; `--type-page` 1.65–2.25rem, `--type-section` 1.15–1.4rem; body 1rem/1.55; lesson copy up to 62ch/1.76 |
| Controls | At least 44px high; form input text at least 16px; readable file-control text at 0.9rem |
| Focus/motion | 3px visible outline with 3px offset; respect reduced motion; no decorative layout displacement on primary-button hover |

Light, dark, and system themes must use the same geometry. Local fonts and checked-in assets only; no remote fonts or CDNs.

## Components and states

- **Navigation:** one shared header, readable destination labels, visible theme control, skip link. Narrow navigation may wrap into a second row. Do not hide necessary destinations to fit.
- **Dashboard:** saved course list, selected preview, then quiet management actions. Previewing a course must not activate it. New course choices always use learner-owned topics or sources. Empty state shows these three choices without preset examples.
- **Creation forms:** compact title and back link; group source selection before course details; use one source type at a time in JavaScript. The non-JavaScript form must remain usable with explicit labels. Disabled source inputs never submit. Files are not persisted to browser storage.
- **Forms:** group labels with controls; distinguish optional input; show an error summary and actionable status; preserve user text during failures. Show one submit action and a quiet cancel/back action.
- **Lesson and tools:** lesson remains readable beside tools on desktop. On phones, the opened tool appears before the lesson so its input is reachable. One visible title per tool; source imports use flat sections. Keep all existing answer, progression, and request locks.
- **Source consent:** title, disclosure, full screened request, cancel, and explicit send. Long request text scrolls inside a bounded region. Do not trim or conceal request content to improve layout.
- **Provider recovery:** bounded modal, readable reason, same connection form as setup, cancel returns to the learner's input. Saving unverified configuration must not unlock teaching.
- **History/progress:** use saved learner-facing records and honest empty states. Drawers scroll vertically and keep the close control reachable. No mastery claims derived solely from course setup or generated lessons.
- **Loading/error:** describe the current operation; expose the supported retry/cancel path. Preserve drafts and saved operation identity. Use live regions for status and alerts for errors. Never rely on color alone.

## Viewport criteria

| Viewport | Acceptance |
| --- | --- |
| 390×844 phone | No page overflow; controls at least 44px; single-column forms; source chooser and primary input appear early; active tool above lesson; dialogs fit with internal scrolling |
| 768×1024 tablet | No narrow three-column dashboard; readable lesson/tool stack; all navigation and management actions remain available |
| 1280×800 desktop | Useful content in first viewport; three dashboard columns fit without clipping; lesson and tool inputs remain readable |
| 1600×1000 wide desktop | Content stays bounded; no oversized headings or panels stretched merely to fill height |

Also check 320px width and the intermediate 1100px dashboard breakpoint with long saved course names. Theme changes, open menus, and dialogs must not shift the page horizontally.

## Screenshot and interaction QA

Use synthetic data and `OPENLEARN_MOCK=1` in a fresh isolated home. Clear inherited API credentials. Use the existing `tests/test_web_browser.py` Playwright harness. Save actual PNGs and a manifest containing state, viewport, theme, page width, and browser errors; DOM assertions alone are not screenshot evidence.

Capture before and after at all four viewports in both themes:

- Empty and populated dashboard, selected course, new-course menu.
- Own topic; source File, folder, and GitHub choice; Quick Learn.
- Source import error and corrected retry; provider recovery with retained text.
- First own-topic lesson; source lesson/chat, consent preview/cancel, explicit mock approval, reload/resume.
- Sources, Options, History, Progress, tutor setup, and data page.

Inspect pixels for clipped text, crowded controls, duplicated headings, source-selection clarity, long request handling, contrast, and excessive blank space. Check keyboard navigation, visible focus, cancel/back/reopen, reduced motion, no-JavaScript fallback, and preserved input. Run focused regressions and `make check`, then obtain independent review of the exact tested commit. Record known gaps; do not claim live model or native-platform coverage from mock browser tests.
