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
Each persisted tutor cue is registered with its session-entry occurrence and originating unit and slide.
Blank Enter succeeds only when that exact latest occurrence remains unconsumed and the learner is still at its origin.
Claiming the cue remains durable if loading the next lesson fails, while mastery or course rewrites invalidate it by changing position.
A later tutor turn can reuse identical cue copy safely because it has a different occurrence token.
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

## Practice Activity Evidence

The tutor may select a hands-on activity as its next move, but selection only creates a side-effect-free proposal.
The learner must explicitly accept before openLearn creates a workspace, opens a resource or application, or executes code.
Direct learner commands such as `/drill` count as explicit acceptance and remain available when model-selected behavior is unavailable.

An activity result is observable candidate evidence, not an answer judgment.
The domain adapter validates and records bounded namespaced evidence, then supplies only the result needed for the teaching decision.
The authoritative judge and move-selection loop decides whether that evidence warrants a learner-state update.
Activity completion never advances mastery on its own, and a tool failure creates no mastery evidence.
Practice and mastery-check purposes remain distinct throughout this flow.

Cancellation and adapter failures preserve the surrounding tutor session.
The learner can continue chatting and use manual `/drill` and `/check` fallbacks without a generic shell, arbitrary executable, or arbitrary path permission.
Activity state and its teaching evidence recover together after an interrupted write through the durable activity-update journal.

Coding drills use one allow-listed `start_coding_drill` action with validated objective, language, difficulty, scaffolding level, source metadata, and practice-or-mastery purpose.
The action cannot contain commands, executable names, or filesystem paths.
The visible tutor turn only offers the activity; the application captures consent separately before creating the topic-owned workspace or launching the configured editor.

Scaffolding ranges from an unaided inert function stub through a planning prompt, partial TODO cues, and worked-example cues from a different instance.
Failed test attempts return bounded test output and the saved learner artifact to the tutor, which gives targeted feedback and reveals at most the next progressive hint before a retry.
Passing tests remains candidate production evidence.
A mastery-check drill requires separately judged explanation, reflection, or later unaided transfer before normal mastery policy can advance.

Original, curated, and licensed exercises preserve source and license metadata independently of the workspace format.
Official LeetCode integration is link-out only: openLearn validates the official HTTPS problem URL, opens it after consent, and creates a local learner-owned solution scaffold without copying the remote statement, examples, or tests.
The durable manual dogfood flow for Neovim and a graphical IDE is in `manual-tests/tutor-coding-drills.md`.

## Interview Skill Evidence

Interview readiness uses a versioned static skill graph rather than model prose as its source of skill identity and prerequisites.
The graph separates concept, pattern, process, and communication skills and keeps learner evidence in append-only local history.
A problem declares primary and supporting skills.
An unaided attempt may infer only evidence kinds allowed by the primary skill's policy.
Supporting skills receive no automatic credit and require an explicit bounded check.
Complexity, edge-case, testing, debugging, and communication claims always require explicit evidence.

Pattern readiness requires unaided production, novel transfer, and delayed retrieval under the skill's current policy.
Recognition, editorial reading, a worked example, copied structure, partial code, or hint-dependent success can guide the next move but cannot satisfy independent production or transfer.
Assistance and completion provenance use closed validated values rather than free-form model labels.
Historical delayed retrieval must satisfy the delay and qualifying-prior relationship from its source graph and mastery-policy bundle.
The same independent, unassisted, novel, complete delayed-observation rule controls both passing credit and whether a failed latest check makes a skill due.
Repeated success for one stable problem or one canonical problem family counts once, so family renames, near-duplicate problems, and spoofed family labels do not manufacture breadth.
An identical replay of one evidence ID is processed once, and conflicting duplicate IDs invalidate the assessment input.
Communication evidence remains separate from algorithm correctness even when both come from one interview attempt.

The tutor may present a skill as ready, provisional, weak, due, unassessed, or blocked only with the deterministic assessment's learner-visible reasons.
A failed latest delayed check makes the skill due and provisional even when older evidence was strong.
A blocking prerequisite must itself be selection-ready, so blocks propagate through longer prerequisite chains.
Blocking still follows bounded remediation and deferral instead of endless drilling.
Graph and mastery-policy versions remain attached to historical evidence when the canonical graph changes, and historical qualification is resolved against that exact immutable bundle.

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

## Bounded Repeated-Miss Recovery

Repeated misses follow one durable progression for the active concept: attempt, hint, worked example, faded check, then defer.
Each miss advances exactly one stage, and resuming the topic restores the saved stage instead of restarting the failed prompt.
The hint gives one targeted cue without revealing the answer.
The worked example uses a different instance, and the faded check uses a new isomorphic production task with less support.
After the faded check fails, openLearn clears the stale question, schedules the concept for review, records a `concept_deferred` event, and tells the learner when it will return.
The tutor must not claim mastery at any active or deferred remediation stage.

A detected prerequisite gap is blocking.
It remains in `pending_remediation` until the learner earns a correct score of at least 0.7 on that concept or explicitly skips or advances.
An explicit skip records `remediation_skipped`, clears the learning gate, and preserves the learner's navigation preference when the wording expresses one.
Recovery records `remediation_recovered`; a later mastery claim still has to satisfy the normal profile gate.

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
