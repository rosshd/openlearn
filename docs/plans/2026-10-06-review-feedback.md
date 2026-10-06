# Minimal review feedback and headings

## Outcome and ownership

Ross approved the notification and preparation recommendations, asked for fewer subheaders, and requested this planning and implementation pass.
Continue the established endpoint of a verified local-main merge.
No remote push, GitHub CI, release, or real-preview restart is included.
Root chat: `01a11191-d2de-7a10-b8d9-e1174593c625`.
Candidate: `/Users/ross/.codex/worktrees/review-readability/openlearn`, new branch `codex/review-feedback`.
Exact start and intended main base: `b938e59dd71d512947e0bf07ae9686738831ea7d`.
Retain the previous `codex/review-readability` branch, gate evidence, and screenshots for recovery.
Preserve the primary `.gitignore` edit and all original untracked files.

## Evidence and research

The supplied preparation-failure screenshot repeats its failure heading in the body, repeats remains-due text, leaves Prepare card alongside Retry preparation, and appends a large error section below Skip.
The screen also has Practice, a page headline, course context, topic context, and Prepare this review card competing for attention.
The revealed state adds separate Answer and Explanation subheaders despite the desired minimal card.

- [Carbon notifications](https://www.carbondesignsystem.com/building-blocks/core/components/notification/guidelines) recommends concise messages without repeating their title and keeping timed notification content available elsewhere.
- [W3C alert pattern](https://www.w3.org/WAI/ARIA/apg/patterns/alert/) describes alerts that communicate important messages without interrupting the user's task or moving focus.
- [W3C timing guidance](https://www.w3.org/WAI/WCAG22/Understanding/timing-adjustable.html) informs keeping required recovery available independently of a timed notice.

Eight seconds is a project choice for transient notice visibility, not a standards claim.

## Decisions

1. Simplify review only in this pass.
   Use a short page title Review, progress text, and one visible topic h2 per card.
   Keep course context quiet.
   In question mode show the question as readable prose below the topic, without turning every text block into a subheading.
   In preparation mode remove Prepare this review card and the separate provider paragraph.
   Use one sentence: Create a review question from your course material.
   The button starts as Prepare card and becomes Preparing… while busy or Try again after failure.
2. Remove visible Answer and Explanation subheaders.
   Preserve meaningful accessible labels and a visible, unobscured programmatic focus target for revealed content.
   Use whitespace and a modest section boundary to separate the answer and explanatory prose.
   Preserve exact saved content, readable contrast, existing paragraph grouping, and the reachable ratings/Skip dock.
3. Review errors produce one compact top-right notification beneath the navigation.
   Reuse existing surface, error, border, radius, shadow, and focus tokens.
   Include an error icon, one short safe message, and a 44px dismiss control.
   Width is bounded on desktop and fits within narrow-screen gutters and safe areas.
   Do not stack repeated notices or include provider details, internal prompts, raw server errors, or credentials.
4. Notices fade after eight seconds, pause while hovered, focused, or the document is hidden, and respect reduced motion.
   Expiry and dismissal must never remove the required recovery action or the essential failure information.
   Do not move focus into a newly shown notification.
   Do not emit duplicate live announcements through both the toast and global status region.
   Manual dismissal from the close control returns focus to a sensible connected card control without unexpected navigation.
5. Each failed operation retains a single short status by its action, without an error heading or a large appended error panel.
   Preparation and reveal reuse their existing primary control for retry.
   Missing-source status retains Open course alongside the action; generic preparation errors do not show Open course.
   Use known payload state/code to choose safe messages, and a generic honest fallback otherwise.
   Do not infer a provider or network cause solely from generic preparation_failed.
6. An uncertain rating save remains persistent until an exact retry resolves it.
   Preserve pending payload, submission ID, selected rating, and disabled alternative ratings/Skip.
   Keep exactly one enabled Retry saving action available on the card or action dock after notice expiry.
   No grade, skip, refresh, or keyboard shortcut may substitute a new request while the outcome is uncertain.
   Successful retries require a committed receipt before advancing.
7. A stale occurrence keeps a persistent Refresh review action and concise status after its notice disappears.
   Refresh failures retain their retry action without adding repeated sections.
   Clear obsolete notification/status/timers when an operation succeeds, a card changes, a retry starts, or the learner skips.
   Retain late-response guards and normal waiting/resume behavior.

## Scope and worker contract

One bounded UI worker owns review-only JS/CSS, `review.html`, and `tests/test_web_review_browser.py`.
Use a new child branch in the retained clean UI worktree, keeping its previous source branch intact.
The root owns the canonical plan, real-service integration tests, combination, final gate, independent review, and local merge.
The UI worker does not delegate, push, change main, or touch the real browser/server/data.
The existing UX reviewer remains independent and performs one read-only pass on the exact tested final head.

No backend, API schema, generation limits, scheduling, receipts, source selection, mastery, storage, or dependency changes are needed.
Do not roll out notifications or heading changes to unrelated pages in this pass.
Do not create a generic notification framework or notification history screen.

## Acceptance

- Preparation has one visible topic heading, one short explanatory sentence, one primary preparation/retry button, and Skip.
- Question and reveal retain topic/question context without visible Answer or Explanation subheaders.
- The notification is visible at desktop and mobile top corners without horizontal overflow or covered recovery controls.
- Preparation, reveal, rating-save, and refresh failures do not append large titled error panels or duplicate due/failure copy.
- Fake-clock tests prove expiry, hover/focus/hidden-page pause, dismissal, reduced-motion behavior, duplicate replacement, and obsolete notice cleanup.
- Failure does not move focus into the toast; retry/close/focus remain usable with keyboard controls.
- After expiry, concise failure information and the correct recovery control remain available.
- Missing-source failures expose Open course; generic failures do not.
- Exact uncertain retries preserve the original full request and lock competing actions after notification expiry.
- Stale refresh, reload, relearning, short/enlarged viewports, and safe plain-text rendering remain correct.
- Real-ASGI preparation-failure/retry and persisted-review journeys run with temporary homes and mocked providers.

## Verification and merge

The worker runs focused browser checks, lint, and diff checks, then supplies one clean scoped local commit.
The root runs focused card/browser/real-service/security checks on the combined candidate, inspects preparation/error/revealed viewport screenshots, and runs the canonical `make check` once on the final candidate.
Obtain one independent review on that exact committed head after the gate.
Allow at most one in-scope repair and targeted rereview if required.
Before merge, recheck main's exact head and unrelated pending edits, retain a recovery branch, and use a guarded fast-forward.
After merge, rerun focused checks against main source using the validated candidate runtime if main's existing environment still lacks browser tools.
Record evidence under `.artifacts/review-feedback/` and retain source branches/worktrees.

## Limits

This pass improves failure presentation and recovery, not the underlying live preparation failure shown in the screenshot.
Its actual cause remains unknown and must not be invented by notification copy.
Native Firefox/Safari and screen-reader behavior remain unverified unless separately exercised.
No automatic regeneration, provider calls, or learner-data mutation are authorized by these UI changes.
