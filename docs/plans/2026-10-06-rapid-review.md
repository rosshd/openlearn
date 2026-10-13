# Rapid review third pass

## Outcome and ownership

Ross accepted the screenshot critique and requested planning and implementation of the next pass.
The accepted outcome is a focused question, an immediately recognizable complete answer, optional explanation, compact ratings, and visible session progress.
This is a standalone local pass continuing this chat's local-main merge endpoint.
Remote shipping, GitHub CI, release, live provider calls, and changes to real learner data are outside this pass.
Root chat: `01a11191-d2de-7a10-b8d9-e1174593c625`.
Candidate worktree: `/Users/ross/.codex/worktrees/review-readability/openlearn`.
Candidate branch: `codex/review-rapid`.
Exact start and intended main base: `6c1dd878bf65bfef31d0756555f1c3e98a3809e0`.
Preserve previous branches, worktrees, evidence, the primary .gitignore edit, and original untracked files.

## Evidence

The screenshot shows an older saved question with a lengthy answer and an explanation that repeats its definitions and caveats.
All five paragraphs have similar emphasis, and the ratings plus separate Skip row consume substantial height.
Revealing the answer has scrolled the session count out of view.
Existing generation instructions already request brevity and non-repetition, but limits apply only during new preparation.
Saved cards must retain their complete text and remain usable without a provider call.

[Anki's studying flow](https://docs.ankiweb.net/manual/studying) supports question, answer comparison, then self-rating with interval previews.
[W3C's disclosure pattern](https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/) supports keyboard activation and an accessible expanded/collapsed state for optional content.

## Decisions

1. Use one compact session bar containing Review and the existing remaining/reviewed count.
   Keep that single count visible while scrolling at ordinary desktop and mobile sizes.
   Place the bar beneath sticky navigation and account for its height in reveal/recovery focus clearance.
   Let oversized bars use document flow on short or enlarged layouts when sticking would obstruct content.
   Do not add duplicate counters or additional page/card subheaders.
2. Rename the primary reveal control to Show answer.
   Reveal the complete reference answer with clear foreground contrast and modest typographic emphasis.
   Retain the question, quiet course context, and topic heading.
   Use the minimum reveal scroll needed to expose the focus target, keeping question and topic in view when the card fits.
   Do not parse arbitrary Markdown, invent emphasized terms, summarize saved text, truncate content, or hide required answer points.
3. Put conceptual explanation behind one collapsed native details/summary disclosure.
   The summary says Show explanation when closed and Hide explanation when open.
   Opening it displays all saved explanatory prose without calls, state writes, or changes to rating eligibility.
   Support Enter, Space, pointer, accessible expanded state, and a visible focus indicator.
   Reset to collapsed when moving to another card or reloading.
   Omit the disclosure when the server supplies no conceptual explanation.
   Preserve the existing exclusion of old self-rating/checklist explanation content.
4. Keep four rating buttons and their interval previews, with no suggested/default rating.
   Reduce excess padding while preserving at least 44px targets and readable labels.
   Place Skip beside the ratings on roomy screens and use a compact wrap on narrow screens.
   Retain the ordinary-document-flow sticky footer, safe-area padding, focus clearance, and short/enlarged-layout fallback.
   Target a footer under 100px at 1280x800 and under 200px at 375x812 with the default font size.
   No nested answer scrolling or fixed-height card.
5. Refine new-generation instructions around one retrieval task and a minimum complete answer targeting 25-35 words.
   Put an example or a causal reason only in the explanation, without repeating the answer's definitions or conclusion.
   Retain source-specific qualifiers when needed, supporting excerpts, factual caveats, units, numbers, and formulas.
   Retain hard limits of 24/60/40 words and all existing validation and single-call behavior.
   Do not regenerate or rewrite saved cards automatically.
   Prompt compliance beyond deterministic validation remains unverified without live generation.
6. Preserve safe transient errors, persistent recovery, exact pending rating payloads, committed receipts, stale-card refresh, and scheduling.
   Optional explanation interaction must not accidentally grade or skip a card through global keyboard shortcuts.
   Recovery must remain visible and usable with the new session bar and tighter footer.

## Scope and implementation order

The UI worker owns review-only controller/CSS/template changes and `tests/test_web_review_browser.py`.
Use a new branch in its retained clean managed worktree from the committed parent plan; retain the old branch.
The root owns this plan, the generation prompt in `src/openlearn/review_cards.py`, and real-service integration tests.
The root integrates the worker's scoped local commit and verifies the combined candidate.
The existing independent UX reviewer gets one read-only review after the final gate.
Workers do not delegate, modify main, push, run CI, use the real browser/server, or touch learner data.

No API/storage/schema, card identities, source selection, scheduling, mastery, dependency, course-creation, or unrelated page changes are needed.
Do not introduce a regeneration/editor workflow or a generic disclosure/header framework.

## Acceptance and verification

- Existing long cards immediately show the complete answer with explanation hidden by default.
- Opening the explanation exposes every saved character, including formulas and malicious-looking strings rendered safely as text.
- Disclosure toggles perform no request and change no card content, due date, receipt identity, or review progress.
- Reveal and disclosure labels/announcements accurately describe the new state.
- Keyboard activation opens/closes explanation without grading, skipping, or re-revealing.
- New cards, older conceptual cards, old checklist cards, offline reload, relearning, two tabs, and uncertain-save retry remain supported.
- The single progress count stays visible during long-content scrolling and updates only after the existing committed review flow.
- Answer/recovery focus and the last expanded sentence remain unobscured by the session bar and footer.
- Default desktop/mobile footers meet the height targets and all controls remain reachable at 320x480 and enlarged text.
- No horizontal overflow, low-contrast explanatory prose, nested scroll region, or extra visible Answer/Explanation headings.
- Existing generation boundary/security/persistence tests still pass, with source text and caveats unchanged.

Run focused tests while iterating and inspect screenshot comparisons of the supplied long-card shape, both collapsed and expanded.
Run explicit card/browser/real-service/security checks on the combined candidate, then make check once on the final committed candidate.
Record exact HEAD, command, counts, runtime, logs, screenshots, and independent review disposition under .artifacts/review-rapid/.
Allow one in-scope repair and targeted rereview if the independent reviewer finds a concrete defect.
Recheck main's exact base and unrelated edits, retain a recovery branch, and use a guarded local fast-forward after a clean review.
Rerun focused checks against main source using the validated candidate runtime if main still lacks browser tooling.

## Limits

Existing question and answer wording remain faithful to saved source-backed content.
The shorter default view comes from optional explanation and layout changes, not automatic rewriting of old cards.
Native Firefox/Safari, screen-reader announcements, and live-model semantic non-repetition are not claimed by isolated Chromium and mocked-provider checks.
The underlying live preparation failure from the preceding pass remains outside this change.
