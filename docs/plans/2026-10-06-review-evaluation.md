# Review hierarchy, regeneration, and evaluation

## Outcome and ownership

Ross requested implementation of the accepted fourth Review pass, fresh generation, a reusable topic evaluation set, and an open browser for human review.
Continue this chat's verified local-main endpoint without remote shipping or GitHub CI.
Root owner is chat `01a11191-d2de-7a10-b8d9-e1174593c625`.
Candidate worktree is `/Users/ross/.codex/worktrees/review-readability/openlearn`, branch `codex/review-evaluation`, starting at `723df2d75e0c8a5e2f8065f1a65fbb29d55d3500`.
Preserve retained worktrees and branches, primary's .gitignore edit, local files, and real learner data.
Use this canonical brief for standalone local work, rather than adding a factory issue or remote lifecycle.

## Evidence

The visible card was prepared at 21:23 UTC on October 6, before current generation instructions.
Its answer is 59 words and its explanation is 116 words.
Preparation intentionally reuses valid existing cards; restart and reveal do not regenerate them.
Current generated explanations are limited to 40 words.
A controlled fresh-generation probe demonstrated that length and verbatim-excerpt validation accepts an answer that omits a question's requested reason.
Answer and explanation share their text color, while the complete answer receives bold weight.

## Decisions

1. Keep the minimal course/topic, question, answer, optional explanation, rating, and Skip flow.
   Give the answer a subtle warm surface and an accent edge, with regular readable body text instead of blanket bolding.
   Use spacing and a slightly smaller but readable explanation to establish hierarchy without new Answer/Explanation subheaders.
   Use restrained red, amber, green, and blue rating accents with labels and intervals retained, no recommended/default rating, and verified contrast in both themes.
   Preserve plain-text fidelity, keyboard controls, responsive targets, progress, footer, and notification/recovery geometry.
2. Add a quiet Card options disclosure on the question side of prepared cards, containing Regenerate card and a short provider-use explanation.
   Regeneration calls the existing prepare endpoint with an explicit strict boolean regenerate flag.
   Ordinary preparation continues to reuse existing cards.
   Regeneration produces a new identity from current selected sources, preserves the schedule and history, and returns the card to the question side.
   Keep the prior card usable when provider or validation fails, reject stale revisions, and exclude the action during grading, uncertain save, or future relearning.
   Do not automatically rewrite saved cards.
3. Strengthen generation instructions to answer each requested aspect, put a requested reason in the answer itself, cover both sides of a comparison, and retain source-specific context.
   Keep single-call preparation and existing 24/60/40 word caps and source validation.
   Do not add a judge call to every ordinary review or pretend deterministic validation proves semantic completeness.
4. Add a versioned, self-contained evaluation set with seven supported topics and one insufficient-source case.
   Cover data-quality desirability, noise/outlier overlap, precision versus bias with numbers, normalization, classification precision/recall, BFS's edge-weight limitation, and idempotency.
   Each fixture records a focused retrieval goal, authoritative source snippets, required answer points, and quality criteria.
   Add known complete, incomplete, and unsupported calibration cards so an evaluator's ability to detect failures can be checked.
5. Add an opt-in runner and Makefile target that generate new cards through the production generator, independently judge completeness, source fidelity, question focus, and added explanatory value, and write sanitized JSON/Markdown evidence plus an isolated native Review deck.
   Distinguish live generation, mock tests, expected refusals, judge failures, and incomplete cases in results.
   Use the configured DeepSeek generator and a distinct provider-advertised `openai/gpt-5.4-mini` judge for this requested live run.
   Bound this pass to 24 provider calls, with one attempt per request and no automatic unbounded reruns.
   Keep slow live evals outside make check.
   Run fixture-only generation with configured provider access; never use learner answers or copy credentials into reports or tracked files.
6. Open a separate local native Review session containing the freshly generated examples for Ross's inspection.
   Use its private fixture home and a free port so grading or skipping examples cannot affect real study data.
   Keep all generated output under ignored .artifacts/review-evaluation, not committed fixture outputs.
   Review fresh output and rendering before claiming success, and report real quality failures rather than substituting mock cards.

## Bounded workers

The UI worker owns review controller/CSS changes and browser coverage for hierarchy, options, refresh success/failure/stale behavior, focus, locks, and enlarged recovery.
The evaluation worker owns reusable fixtures, an opt-in runner, runner/calibration tests, a Makefile target, and its concise usage documentation.
Root owns this plan, prepare-request/backend regeneration, generation instructions, service integration coverage, live execution, candidate integration, and local-main merge.
The existing independent reviewer gets one bounded read-only review of the combined tested head.
Workers use retained clean managed worktrees on new child branches from the committed plan, do not delegate, and do not alter main, run live provider calls, push, or use real learner data.

## Acceptance and verification

- Both light and dark Review themes show a clear answer/explanation hierarchy with no additional visible subheaders.
- Complete saved text remains faithful and readable at desktop, mobile, short, and enlarged layouts.
- Rating labels and intervals remain legible, all controls retain adequate targets, and no action is suggested as the correct rating.
- Regeneration changes only card identity/content and preserves due date, review schedule, mastery, and committed rating history.
- Failed or stale regeneration never replaces a good saved card or grades a learner.
- Concurrent grading/source updates and late browser responses preserve existing revision/lock protections.
- Regeneration is absent during revealed/rating recovery and resumes safely on the question side.
- Fixture/calibration tests distinguish semantically incomplete or unsupported examples from valid complete ones.
- The live report records exact generated output, fixture/rubric versions, models, call count, source/head binding, and per-case outcome.
- The browser review deck contains fresh generation outputs with fresh identities and timestamps; failures remain visible in the report.

Run focused root and worker checks while iterating.
Inspect browser screenshots and live generated examples, then run combined card/browser/service/security/eval-harness checks and make check on the final committed candidate.
Record exact tested HEAD, gate, live quality results, browser evidence, and review disposition under .artifacts/review-evaluation.
Apply at most one in-scope review repair and targeted rereview, then use a guarded local fast-forward with a retained recovery branch.
Verify main source after integration and open the isolated evaluation deck in Firefox.

## Limits

This pass does not add typed-answer grading, mastery promotion, automatic card migrations, new storage formats, a generic notification framework, or unrelated course UI work.
Live quality judgments are evidence for this bounded set, not guarantees for every possible model response.
Native Firefox display will be checked for Ross's handoff; wider browser and screen-reader coverage remains outside this pass.
