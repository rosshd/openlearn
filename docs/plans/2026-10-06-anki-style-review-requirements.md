# Anki-style review requirements

Status: UI/UX design and local implementation plan approved for execution by Ross on October 6, 2026.
Endpoint: independently reviewed, tested local candidate; no push, PR, GitHub CI, merge to main, release, live provider test, or real learner-data mutation.

## Problem and outcome

The browser review page displays a concept label and immediate Easy, Hard, and Missed buttons.
It provides no retrieval question or reference answer, so the learner cannot reliably compare recalled knowledge with the expected answer.
Ross wants the familiar Anki question, answer reveal, and self-rating flow.
Ross explicitly selected answer reveal and self-rating only, without typed answers or AI grading.

## Decisions

1. Present one review card at a time with a specific question and a hidden reference answer.
   Show answer and explanation reveals the answer and a short explanation of the essential distinction or reasoning.
   Ratings become available only after reveal.
   A learner who cannot recall the answer can reveal immediately and choose Again.
2. Use Again, Hard, Good, and Easy.
   Again means incorrect or forgotten; Hard means correct with substantial difficulty; Good means correct with effort; Easy means correct with little effort.
   Display the actual next review interval for each choice.
   Space reveals the answer; 1 through 4 select a rating after reveal.
   Ignore shortcuts while typing in a control and prevent a held key from revealing and grading in one action.
3. Persist each card's identity, content version, question, reference answer, explanation, and course/concept provenance.
   Reopening a review reuses the same content instead of regenerating the question.
   Keep the existing local Markdown plus JSON storage boundary.
   Prepare card content from existing teaching material or imported course sources, without inventing missing source facts.
   AI may prepare content through the existing configured provider, but ordinary review of a prepared card must work offline.
4. Again returns the card to a short relearning queue within the session rather than simply removing it until tomorrow.
   Keep that return visible through a remaining count and the next eligible review time.
   Let the learner leave and resume without losing the relearning obligation.
   Correct ratings update the longer-term schedule.
   Support all four outcomes explicitly rather than silently equating Good with Hard or Easy.
5. Record self-ratings as self-reported recall for scheduling.
   Do not promote a concept to demonstrated mastery or record an objectively judged answer from these ratings.
   Revealing, opening, skipping, or abandoning a card does not count as a completed review.
6. Make each rating an atomic, retry-safe operation against the current card version and review occurrence.
   Double clicks, request retries, stale tabs, and simultaneous sessions must not apply a rating twice or overwrite a newer schedule.
   Preserve existing review history and due dates during migration.
   Existing concept-only reviews need prepared card content before they become rateable.
   Insufficient source material or preparation failure leaves the item due and offers a clear retry or skip action.

## Example shape

This example illustrates the interaction; its final wording must be checked against the course material.

- Front: How does an outlier differ from noise, and can an outlier be a valid observation?
- Back: An outlier is an observation unusually far from the overall pattern and can be valid.
  Noise is unwanted variation or error that obscures the signal.
  Being unusual alone does not establish that an observation is noise.
- Explanation: An unusual observation may reflect a real rare event; it is not automatically a measurement error or unwanted variation.
- Controls after reveal: Again, Hard, Good, Easy, with the corresponding next review times.

## Scope and non-goals

Cover card preparation, legacy review compatibility, the browser review session, scheduling outcomes, and review evidence.
Preserve the existing CLI review interface unless shared scheduling changes require compatibility work.
Do not add typed-answer assessment, AI grading, Anki synchronization, deck import/export, media card types, or unrelated tutor changes.
Do not replace the scheduler with FSRS as part of this change.
Again returns after one minute.
The fixed scheduler preserves Hard at two days and Easy at seven days, and adds Good at four days.
The optional Ebisu scheduler retains its existing algorithm and legacy outcomes, with an explicit Good success outcome and four-day initial half-life.
Again records a missed recall and persists its minute-level relearning obligation independently of the longer-term day-level due date.
All interval previews must come from the backend and match the committed schedule.
Do not describe the resulting scheduler as equivalent to Anki's algorithm.

## Acceptance checks

- A due item shows a concrete question, with no reference answer or enabled rating before reveal.
- Reveal shows a usable reference answer and explanation without changing its due date or recording completion.
- Each rating has documented semantics and an interval preview consistent with the committed schedule.
- Again returns the item during relearning; exiting and reopening preserves its next eligible time.
- Prepared cards remain usable without provider access and retain their content across reloads.
- Legacy preparation failures do not erase, postpone, or falsely complete due reviews.
- Keyboard and pointer flows work, with accessible focus, reveal announcements, and retry feedback.
- Duplicate and stale submissions cannot create duplicate evidence or change a newer review occurrence.
- Self-ratings never create objectively assessed mastery evidence.
- Focused storage, scheduling, service, and browser tests pass, followed by the repository's canonical make check gate.

## Repository evidence

- src/openlearn/web/templates/review.html renders the concept label and immediate rating buttons.
- src/openlearn/web/services.py forwards browser ratings directly to schedule_review_outcomes.
- src/openlearn/cli.py normalizes review metadata without retaining question or answer content.
- The default scheduler currently maps missed, hard, and easy to 1, 2, and 7 days; Ebisu is optional.
- The CLI generates review questions but subsequently requests self-ratings without an answer-reveal flow.
- docs/TUTOR_INTERACTION.md and docs/LEARNING_SCIENCE.md distinguish demonstrated understanding from confidence and emphasize retrieval and specific feedback.
- [Anki's studying manual](https://docs.ankiweb.net/studying.html) documents question-first presentation, answer reveal, four ratings, interval previews, and keyboard controls.

## Execution and ownership

Root coordinator: Codex chat 01a11191-d2de-7a10-b8d9-e1174593c625, runtime /root.
Integration worktree: /Users/ross/.codex/worktrees/anki-review/openlearn.
Integration branch: codex/anki-review.
Exact start SHA: 1fdcf4abd04d0a009596e01b20b4867e3003fa95.
This is the reviewed lesson-transition build underlying the current preview, rather than the dirty primary checkout.
Uncommitted submission-identity work in another checkout is outside this change.
Use managed child worktrees and local recoverable commits, with the root as the sole integration writer.
This is standalone local implementation, so no factory issue or shipping lifecycle is introduced.
Maximum simultaneous implementation workers: two; no recursive delegation.
Each worker runs in a fresh subagent task, implements only its named item, and stops after its verified handback.
The read-only UX designer is /root/review_ux_design and can independently review the final implementation because it does not write code.

## Detailed UI/UX

### Page structure

Reuse the existing header, design tokens, borders, theme control, and polite live region.
Keep the title Review what is due, with restrained review-specific typography around clamp(2rem, 4vw, 3rem).
Use one stable article.work-panel with about 48rem maximum width and two-rem desktop padding.
Show a session summary such as 3 remaining · 1 reviewed, plus 1 returns soon when applicable.
Remaining counts unique outstanding items, including waiting and skipped items.
Reviewed counts accepted rating operations in this page session, including Again, without double-counting retried responses.
Avoid Card N of M because repetition and skips make that count misleading.
Keep Back to courses outside the panel and Skip for now in the panel footer.
Skip affects this page session only and never changes storage or schedule.

### Question and reveal

The panel shows course name as an eyebrow, concept as secondary context, and the actual question as its h2.
Show answer and explanation is the primary action.
Keep keyboard shortcuts available through accessible labels and control hints without separate visible instruction paragraphs.
The initial HTML and queue response must not include reference answers or source excerpts.
On reveal, retain the question and add one readable Answer section with the saved answer and a brief conceptual explanation.
Do not show source disclosures, a recall checklist, or self-rating instruction paragraphs on rapid-review cards.
Use comfortable line height, a constrained text measure, clear paragraph spacing, and a prominent answer with explanation beneath it.
New card preparation must produce conceptual reasoning or a relevant example instead of instructions to check recall.
Mark new explanations as conceptual; retain older saved answers and provenance, but omit their legacy checklist explanations from presentation.
Source references remain stored for grounding and validation.
Render all generated/source text as escaped plain text, never raw HTML.
Do not show correct/incorrect badges, scores, or mastery claims.

### Rating controls

Use four equally sized secondary buttons in this order: Again, Hard, Good, Easy.
Each button shows only the rating label and backend interval preview.
Keep the rating meaning available in accessible labels and tooltips.
Again means Forgotten or incorrect; Hard means Correct with difficulty; Good means Correct with effort; Easy means Correct with little effort.
Expose the 1 through 4 shortcuts accessibly without a separate visible hint paragraph.
Saving disables all competing panel actions until the result is known.
A successful HTTP response is insufficient: require an explicit committed result before advancing.

### Preparation and source failure

Legacy items show Prepare this review card and This concept needs a question and reference answer before you can review it.
The Prepare card action states that it uses the configured AI provider.
Page load must never start provider work.
Preparation considers only bounded saved course notes, imported text, and tutor teaching content, excluding learner answers and unrelated courses.
Select passages relevant to the due concept before applying source-window and total-input limits, so long notes or older unrelated lessons cannot crowd out the available supporting material.
Require supporting excerpts that can be verified against the selected source text.
Use existing provider configuration precedence and one bounded generation attempt, without an extra judge call.
Persist successful content before presenting its question; never generate a fresh variant on reload.
While pending, show Preparing question and answer and mark the panel aria-busy.
Missing material shows More course material is needed, explains the item remains due, and offers Open course, Retry preparation, and Skip for now.
Provider or storage failure shows Could not prepare this card, bounded safe detail, and the same retry/skip choices.
No generic answer inferred only from the concept label is acceptable.

### Relearning and completion

Again persists a UTC timestamp one minute after the accepted operation and retains the card in remaining.
Show another eligible card immediately if one exists.
Otherwise show Your next card returns soon, the local absolute return time, a quiet countdown, and You can leave and resume later.
Do not announce countdown ticks every second.
Use server time to account for client-clock drift and reconcile eligibility from the server when the timer expires or the page becomes visible again.
Reload returns to the question state and preserves the obligation and timestamp.
Nothing is due is reserved for an initially empty queue.
Review complete is reserved for a session with no outstanding cards, including no waiting cards.
Use distinct-card count in completion copy rather than repeated rating count.
All skipped items show Reviews remain due and Try skipped cards again, which clears only the local skipped set.
Waiting and skipped cards can coexist and must both remain visible in the summary.

### Retry and conflict

For an uncertain rating result, retain the answer and selected rating, show Could not confirm this rating was saved, and offer Retry saving.
Retry uses exactly the same submission ID, occurrence/version, reveal token, and rating.
Do not permit a different rating while the previous outcome is uncertain.
A stale occurrence shows This card changed in another tab and Refresh the review to continue from its current schedule.
Refresh must never reapply the old rating to the new occurrence.
Guard late responses with the active operation/card identity so they cannot replace newer UI state.
Backend mutation checks happen under the topic lock, not in a separate read-before-write window.

### Accessibility and mobile

Use the existing announce function for brief results rather than making the whole panel live.
Leave ordinary initial document focus intact.
After advancement focus the question heading; after reveal focus the reference-answer heading; after errors, waits, and completion focus the relevant state heading.
Make these headings focusable with tabindex=-1.
Space reveals only in question state; 1 through 4 rate only in revealed state.
Ignore repeated, composing, modified, editable-target, and native interactive-target key events.
Preserve normal button/link/disclosure activation with Enter and Space.
Prevent the existing lesson Enter handler from attempting tutor navigation on the review page.
At 375px use one-rem gutters/padding, a full-width reveal button, a two-column rating grid, and touch targets at least 44px high.
At desktop widths use four rating columns and an answer measure around 65ch.
Long questions, labels, and source names must wrap without page overflow.
Do not add fixed controls, card-flip animation, or new motion.

## Shared HTTP contract

The backend worker owns and freezes this contract before the UI worker finishes its adapter.
Small mechanical additions are allowed with an immediate parent and peer message; product changes return to root.

- GET /review renders the shell with a queue snapshot and course filter.
- GET /api/review/session?course=<optional slug> returns items, count, and server_time without answer content or provider calls.
- Every item has slug, course, concept, due, card_id, content_version, review_revision, state, question, and relearn_at.
- Item state is needs_preparation, question, or waiting.
- Legacy item card_id and question may be empty, but review_revision must identify its current occurrence.
- POST /api/review/prepare accepts slug, concept, and review_revision; success returns ok=true and the question-only item.
- POST /api/review/reveal accepts slug, concept, card_id, content_version, and review_revision; success returns ok=true, answer, explanation, sources, reveal_token, and ratings.
- Each source has label and excerpt; each rating has result, label, interval, and the authoritative next_due or relearn_at.
- Again previews a one-minute duration with interval_seconds=60 and relearn_at=null; its absolute return time is computed when the rating commits.
- Correct-rating next_due values are bound to the reveal receipt and remain authoritative at commit.
- POST /api/review accepts slug, concept, card_id, content_version, review_revision, reveal_token, result, and submission_id; result is again, hard, good, or easy.
- Rating success returns ok=true, state=committed, submission_id, next_due, relearn_at, and the surviving question-only item or null.
- Same-ID same-input rating replay returns the original receipt; changed input or stale occurrence returns HTTP 409 with state=conflict and safe error text.
- Missing preparation source returns a safe structured failure; no provider work occurs on session/reveal/grade.
- All endpoints reuse existing CSRF, namespace, slug validation, safe public mapping, and service-threadpool conventions.
- Server reveal receipts gate rating for the current version/occurrence and bind the preview to its eventual committed schedule.
- Card/occurrence identities and rating receipts live within the existing local learner storage boundary and survive normalization and reload.
- Browser dashboard and progress projections include outstanding relearning cards, so leaving to courses never hides the review entry needed to resume.

## Worker prompts and order

### R1 - durable cards and review service

Run in a fresh managed worktree from the integration plan commit.
Implement the shared HTTP contract end to end through isolated service tests.
Own src/openlearn/review_cards.py or an equally small dedicated module, bounded CLI normalization/scheduling changes, web schemas/routes/service adapters, and focused backend tests.
Do not edit review.html, openlearn.js, or openlearn.css.
Persist stable card identity/version/content/provenance, occurrence/relearning/reveal state, and replay-safe rating receipts.
Preserve legacy topic metadata, due dates, event history, CLI ratings, and config precedence.
Preparation must select and validate bounded supporting source excerpts and persist one successful card without silently regenerating it.
Test absent/malformed source content, provider failure, offline reload, reveal without schedule mutation, each rating and its preview, replay/conflicting replay, stale versions, two concurrent raters, and Again resume with a controlled clock.
Do not emit answer_judged or promote mastery from ratings.
Use temporary OPENLEARN_HOME and mocked providers only.
Read repository instructions and architecture/tutor-policy/validation skills.
Run focused tests and lint, make a scoped local recoverable commit, then hand back exact head, files, commands/results, contract details, and limits.
Stop after R1; no push, PR, CI, main merge, live server, real data, paid calls, or recursive delegation.

### R2 - complete browser review journey

Run in a fresh managed worktree from the same integration plan commit.
Implement the detailed UI/UX against the frozen contract, with mock-route browser tests for the complete journey.
Own review.html, review-only CSS, the JS review controller and minimal guard on global lesson keyboard handling, plus a focused browser-test module.
Do not edit backend/schema/storage code or tests owned by R1.
Reuse appUrl, requestJson, announce, safe escaping/textContent, theme tokens, and existing isolated Playwright patterns.
Cover legacy preparation, question/reveal, four ratings, session counts, skip/paused/finished, Again wait/resume, semantic failures, ambiguous-save retry, stale tabs, single-flight actions, and late responses.
Implement all keyboard/focus/mobile requirements, including no answer data in initial DOM, no shortcut repeat, and no 375px overflow.
Run focused browser tests and lint and inspect desktop/mobile screenshots from the isolated fixture.
Make a scoped local recoverable commit, then hand back exact head, files, commands/results, screenshot pointers, and limits.
Stop after R2; no push, PR, CI, main merge, paid calls, real learner mutations, live Firefox changes, or recursive delegation.

### R3 - parent integration and independent review

Root integrates R1 and R2 commits into codex/anki-review and resolves only mechanical contract mismatches.
Run combined focused backend/browser coverage against the real service, including an isolated one-card Again/reload flow and legacy preparation using mocked generation.
Run make check once on the final combined candidate and record exact HEAD and log.
Assign the non-implementing UX designer one bounded read-only independent review of that tested HEAD against the start SHA and this plan.
The reviewer checks engineering defects and spec fidelity separately, including source honesty, storage migration, reveal gates, scheduling previews, replay/conflicts, self-report evidence, browser safety, and mobile/keyboard behavior.
The reviewer does not edit or delegate.
Root may coordinate one in-scope repair and targeted rereview, rerunning affected checks and the gate if content changes.
Retain managed worktrees and local commits for recovery.
Leave the real preview, primary changes, and main untouched.

## Verification and remaining limits

Focused tests cover persistence, scheduling, HTTP contracts, and real-browser state transitions using temporary homes and provider-free environments.
Run OPENLEARN_BROWSER_TEST=1 for the browser lane after installing Chromium in the candidate environment.
Run make check from the integration worktree, using its own Python environment and Ruff on PATH.
Inspect at least desktop and 375px screenshots and verify no horizontal overflow.
No live model-quality claim follows from mocked preparation tests.
No real course will be prepared or rated during agent verification.
GitHub CI, release, and installed/main behavior remain outside this local endpoint.
