# Rapid-review readability

## Outcome and ownership

Ross approved research, planning, implementation, worker delegation, and merge of the agreed card improvements.
The endpoint is a verified local-main merge; remote push, GitHub CI, release, and running-preview activation remain outside this change.
Root chat is `01a11191-d2de-7a10-b8d9-e1174593c625`.
The root candidate is `/Users/ross/.codex/worktrees/review-readability/openlearn`, branch `codex/review-readability`, starting at main `38f0fe8c5044d580f4e0caeb0d2da35e280cd003`.
Preserve the primary checkout's pending `.gitignore` edit and all untracked files.
This is a bounded local improvement, not a remote factory shipment.

## Evidence and research

The supplied real-card screenshot shows a large, 38-word question, a dense answer, and a longer explanation repeating the same distinction.
The explanation is visually muted and ratings are near the bottom edge of the viewport.
Existing QA used short typical answers and only tested overflow for long question text, so it did not prove long revealed-content readability or action reachability.

- [Anki studying](https://docs.ankiweb.net/manual/studying) supports question first, explicit answer reveal, and rating buttons with interval previews.
- [W3C succinct text](https://www.w3.org/WAI/WCAG2/supplemental/patterns/o3p05-succinct-text/) recommends short blocks, one topic per paragraph, and the main point first.
- [W3C contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) specifies at least 4.5:1 for ordinary text.
- [W3C focus not obscured](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html) identifies sticky headers and footers as potential focus-obscuring content.

These sources inform layout and accessibility choices, not a claim of measured learning efficacy.
The numeric content limits below are project decisions to keep rapid review compact.

## Product decisions

1. Preserve one card, explicit Show answer and explanation, four self-rating controls with authoritative intervals, and Skip.
   No sources, recall checklist, self-rating instruction paragraphs, or new card-management controls appear.
2. New preparation asks one direct retrieval question, targeting 18 words and allowing at most 24.
   Avoid boilerplate such as According to the supplied materials while retaining qualifiers needed for a source-specific claim.
   The answer targets 35-45 words and allows at most 60, using two or three short paragraphs when multiple points are needed.
   The explanation targets 20-30 words and allows at most 40, adding one grounded rationale or example instead of repeating the answer.
   Numeric, formula, and source caveats remain intact.
3. Enforce length limits before saving a newly generated card.
   An oversized or malformed response fails honestly and leaves the item due; do not truncate, retry the provider, or silently save clipped content.
   Retain one bounded provider call and existing supporting-excerpt validation.
   Saved older cards remain valid regardless of the new preparation limits.
4. Saved cards receive layout improvements without regeneration or storage migration.
   Preserve every character of the question, answer, and eligible conceptual explanation.
   Render existing paragraph boundaries and break dense prose into short sentence-based visual paragraphs where reliable, preserving decimals, units, abbreviations, code, and formulas.
   Do not infer lists, insert semantic labels, remove qualifiers, or parse generated HTML.
5. Course context becomes a small, quiet mixed-case line; the topic receives greater prominence.
   The question uses moderate size and weight rather than occupying the entire visual hierarchy.
   Answer and explanation use readable foreground contrast, a roughly 55-60 character measure, and 1.65-1.7 line height.
   Distinguish the brief explanation with spacing and a quiet label rather than faint text.
6. Ratings and Skip share an opaque bottom action footer in document flow, sticky at the viewport bottom on normal-height screens.
   The footer belongs to the card and never appears before reveal except for the existing Skip action.
   Keep four desktop columns and two mobile columns, with at least 44px targets and safe-area padding.
   Avoid a second nested scrolling region.
   Reserve scroll/focus spacing so text and focused elements can be brought above the dock and below the site header.
   Revert the dock to ordinary flow on short-height or zoomed layouts where it would dominate the viewport.

## Scope and constraints

Affected production files are `src/openlearn/review_cards.py`, review code in `src/openlearn/web/static/openlearn.js`, and review rules in `src/openlearn/web/static/openlearn.css`.
Change `review.html` only if needed for the action-layout or accessibility contract.
Use the existing card, controller, and real-service test modules.
Do not change scheduling, metadata normalization, occurrence/reveal identities, rating receipts, submission identity, mastery, course-library behavior, storage format, dependencies, source selection, or tutor prompts outside review preparation.
No real learner data, configured API keys, live providers, or running Firefox operations are part of verification.

## Worker assignments

R1 owns the preparation content contract in `review_cards.py` and `tests/test_review_cards.py` only.
R1 preserves legacy card validity, one-call generation, provenance validation, due state, and request idempotency.
R1 supplies boundary and oversized-output tests, including source caveats and nonempty content.

R2 owns review JS/CSS and `tests/test_web_review_browser.py` only, with `review.html` permitted if necessary.
R2 implements balanced hierarchy, faithful paragraph rendering, ordinary contrast, and the shared action footer.
R2 proves visible dock controls with actual long answer and explanation fixtures, safe plain text, keyboard flow, no overflow, and readable last-content positioning.

Each worker uses its own branch/worktree, stops after its bounded change, runs focused checks, and returns a clean local commit.
Neither worker pushes, changes main, delegates further, or alters the other's write set.
The root combines the commits and owns any necessary real-service integration-fixture update.
The read-only UX researcher remains independent for one final exact-head review.

## Acceptance and verification

- New prepared cards satisfy 24/60/40 word limits and retain verified source excerpts.
- Oversized output is rejected without a second provider call, schedule change, or saved partial card.
- Older long cards still reveal and rate without rewriting their saved content.
- The screenshot-shaped long fixture uses a real long course title, long question, at least 65 answer words, and at least 100 explanation words.
- At 1280x800 and 375x812 in light and dark themes, rating buttons and Skip are reachable in the viewport immediately after reveal.
- The final answer/explanation sentence can be scrolled fully above the dock; focus is visible and not covered.
- At 320px width and a short viewport, content reflows without horizontal overflow and all controls remain reachable.
- Plain-text injection fixtures create no HTML nodes, decimals/units remain intact, and keyboard shortcuts preserve native/editable guards.
- Existing uncertain retry, stale occurrence, relearning, and real-service rating journeys remain green.

Run focused card/browser/integration/security checks while iterating.
Run `make check` once on the final combined candidate, inspect realistic screenshots, then obtain one independent review on that exact committed HEAD.
Repair at most one in-scope finding cycle and rerun affected checks and the canonical gate if needed.
Before local main merge, confirm its HEAD and pending changes remain compatible, retain a recovery branch, and fast-forward without overwriting unrelated work.
After merge, prove candidate ancestry and rerun the focused real-service and browser checks on local main.
Retain evidence under `.artifacts/review-readability/` and source worktrees for recovery.

## Risks and limits

Strict generation limits can reject an unusually verbose response; explicit preparation retry remains available.
Prompting cannot prove semantic non-repetition or live-model explanation quality without live output evaluation, so verification reports that limitation honestly.
Layout-only changes do not shorten existing stored prose.
Sticky controls need measured viewport and focus checks, especially on narrow or short screens.
Native Firefox, Safari, screen-reader behavior, and live provider quality remain unverified unless separately exercised.
