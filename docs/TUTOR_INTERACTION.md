# Tutor Interaction

openLearn should behave less like a helpful answer bot and more like a measured tutoring system.
The durable advantage is the loop: learner state, answer judging, move selection, and delayed retrieval measurement.
Agents changing this behavior should read `.claude/skills/openlearn-tutor-policy/`.

## Design Commitments

- Elicit before telling when the learner can still make progress.
- Treat production and transfer as stronger evidence than recognition.
- Do not advance from one fast correct answer or self-reported confidence.
- Do obey explicit navigation requests such as skip, continue, or move on.
- In Quick Learn, optimize for coverage per minute: ask at most one check per slide and use the Enter-to-continue cue after a correct or adequate answer instead of probing the same concept repeatedly.
- Detect shallow copying with deterministic signals where possible.
- Tune toward delayed retrieval, not in-session smoothness.
- Keep all learner state local and inspectable.

## Enter-to-Continue Contract

When the tutor has determined that the learner is ready to advance, it uses an explicit `**Next:**` cue: `Press Enter to continue, or type what you want more help with.`
Blank Enter after that cue follows the same deterministic navigation path as `/done`, including slide advancement, chapter quizzes, coverage checks, and transition event logging.
Stored learner preferences remain intact.
Any non-empty response stays on the current concept and is sent to the tutor before navigation.
Blank Enter is a no-op without the explicit cue.
Blank Enter never clears or bypasses a `pending_question`.
A preserved learner answer takes priority and blank Enter resubmits it.
`/done` remains available as a backward-compatible explicit navigation command, but normal tutor copy and default help prefer Enter.
The Enter cue belongs under `**Next:**` and must not create pending grading state.

## Per-Turn Loop

1. Ingest learner message, pending question, recent tutor text, and timing signals.
2. Classify the turn as answer, question, request, confusion, or other.
3. Judge understanding with a structured score, status, misconception, and gap.
4. Detect gaming or shallow copying.
5. Update learner state: attempts, rolling pass rate, misconceptions, SRS, difficulty, quiz state, and events.
6. Select the next tutor move from state.
7. Generate one concise terminal-friendly tutor turn.
8. Advance only after mastery evidence.

## Learner State

| Scope | Examples |
| --- | --- |
| Concept | attempts, rolling correctness, last seen, misconceptions, mastery, SRS due date |
| Unit | difficulty, lock state, slide or chapter position |
| Session | consecutive correct or missed answers, last answer score, pending checks |
| Behavior | latency, overlap with recent tutor text, help-before-attempt |
| Goal | mastery profile: efficient, proficient, or deep |
| Preference | explicit skipped material, durable constraints from learner navigation |

## Move Policy

| State | Move |
| --- | --- |
| Struggling | Narrow the concept, lower load, ask for an attempt, then give a worked example if needed |
| On track | Keep difficulty near the 80-85 percent success band with production and transfer checks |
| Mastering | Add edge cases, prediction, novel transfer, and harder checks |
| Suspected gaming | Ask an immediate transfer check and withhold advancement |
| Explicit skip or move on | Clear stale learning gates, advance, and remember durable preferences |
| Ready to advance | Require passed production or transfer evidence |
| Quick Learn adequate answer | Affirm briefly and move toward the next uncovered concept |

## Judge Requirements

- Scores must be calibrated across topics.
- Stored multiple-choice answer keys are authoritative.
- Pending questions can be free response or multiple choice without a stored key.
- Misconceptions should be specific enough to change the next tutor move.
- Recognition, recall, explanation, transfer, and hands-on production are not equivalent.
- Fast high-overlap answers can be correct but should not count as mastery evidence.

## Quick Learn Coverage

Quick Learn plans must stay grounded in imported source summaries.
Each unit has a `Concepts:` contract, each lesson response hides an exact `<!-- covered: ... -->` marker, and openLearn stores per-slide coverage so later prompts avoid re-teaching covered concepts.
When a unit or course would otherwise end with uncovered concepts, openLearn can add bounded make-up slides before marking the course complete.

## Context Fidelity

For learner-specific tools, keybindings, and setup, the tutor must trust explicit context over generic defaults.
If a binding is not documented in the learner context, say it is not documented and point to where to verify it.

## Evaluation

Default tests cover deterministic logic and mocked tutor flows.
Slow AI-judge evals should focus on judge calibration, move quality, anti-answer-giving behavior, and delayed retrieval outcomes.

## Roadmap Focus

1. Harden the judge and learner-state updates.
2. Strengthen deterministic gaming detection.
3. Encode move policy in prompt fragments and pure selection logic.
4. Tune cumulative quiz thresholds and expand retrieval coverage.
5. Expand slow-lane eval fixtures for judge calibration and tutor-move quality.
