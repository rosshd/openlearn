# Learning outcome evaluation

The outcome lane measures delayed retrieval and teaching efficiency across bounded multi-turn scenarios.
It wraps the existing live tutor-behavior harness, preserving isolated temporary homes, sanitized evidence, distinct tutor and judge models, and deterministic replay inputs.
It does not replace the single-turn or multi-turn tutor-behavior suites.

## Run the lane

The lane is opt-in and intentionally absent from `make check`.
It makes provider calls, so run it only when live outcome evidence is intended.
The output root must not exist before the run.

```bash
OPENAI_API_KEY="..." \
OPENLEARN_MODEL="tutor-model" \
make outcome-eval \
  RUN_ROOT="$(mktemp -d)/outcome-eval" \
  JUDGE_MODEL="independent-judge-model"
```

Use `SCENARIO=immediate_success_delayed_failure` for a deliberate partial run.
A partial run remains valid evidence and is labeled `partial` in the manifest and summary.

The four scenarios make fourteen generated tutor turns.
Each generated turn invokes the tutor, metadata extractor, and independent turn judge, and each scenario adds one sequence judgment.
Allow roughly two to five minutes depending on provider latency.

## Evidence and metrics

Each run writes the following private artifacts:

- `evidence/manifest.json` records coverage, models, aggregate metrics, proposed thresholds, and calibration status.

- `evidence/scenarios.jsonl` records state-linked turns, projected timestamps, durable events, scenario metrics, and visible diagnostics.

- `evidence/summary.md` is the human-reviewable outcome report.

- `behavior/evidence/` preserves the underlying tutor-behavior evidence unchanged.

Scenario fixtures declare `gap_days_before` values.
The outcome lane projects those gaps onto copied event evidence from a fixed UTC start time.
It never sleeps, changes the system clock, or mutates learner-owned state.
The outcome lane reuses the core event timestamp, concept, and retrieval-source helpers while leaving the existing aggregate unchanged.
It applies the stricter qualifying-evidence rule when classifying delayed success.
The delayed-retrieval denominator includes every scheduled retrieval with valid source, concept, timestamp, and minimum spacing.
Recognition-only, gamed, hint-supported, and worked-example-supported retrievals remain visible in that denominator as failures.

The lane reports:

- delayed or scheduled retrieval success;

- novel transfer success;

- turns to criterion;

- tutor words per turn and per mastered concept;

- repeated, redundant, and excessive probes;

- hint and worked-example dependency;

- concepts covered without false mastery; and

- deferred concepts recovered during retrieval.

Judge prose is not a metric input.
The metric inputs are persisted answer events, mastery and deferral events, selected tutor moves, state-linked turns, and persisted tutor output.
Criterion, novel-transfer success, and deferred recovery all use the same qualifying evidence rule.
The answer must pass as production-grade evidence, must not be flagged as gaming, and must be independent of a disqualifying hint or worked example.
Transfer and recovery additionally require their matching novel-transfer or scheduled-retrieval semantics.
False mastery, excessive probing, redundant probing, unresolved support dependency, delayed failure, and unrecovered deferral remain visible in each scenario's diagnostics even when other metrics are strong.
A premature mastery event remains false mastery if the learner later recovers independently; the later recovery is recorded separately instead of rewriting history.

## Baseline and calibration

The deterministic contract baseline is recorded in `tests/evals/fixtures/outcome_baseline_v1.json`.
It intentionally contains delayed failure, false mastery, redundant probing, and unresolved support dependency so regressions cannot make those failures disappear.
It is not evidence of tutor-model quality.

The initial proposed thresholds are:

- delayed retrieval pass rate at least `0.70`;

- novel transfer pass rate at least `0.70`;

- median turns to criterion at most `4`;

- redundant probes at most `1` per scenario;

- unresolved hint or worked-example dependency rate at most `0.20`; and

- false mastery count exactly `0`.

These thresholds are recorded in every manifest but do not affect the command exit status or release status.
Before enabling release blocking, record at least three complete provider-backed runs with the intended tutor and judge model pair, review variance and failure examples, and approve revised thresholds.
Until that calibration is recorded, the lane is diagnostic.

## Policy and release timing

When live evaluation is separately authorized, run the lane before merging a tutor-policy, answer-judging, mastery, remediation, quiz, SRS, or retrieval-scheduling change.
Compare the candidate run against a run from the current `main` using the same tutor and judge model pair.
Review the human summary and the underlying scenario records before attributing a metric change to the policy.

Run the lane during v1 release checks after `make check` and before the final release decision.
Until calibration is complete, attach the summary as diagnostic release evidence.
Once calibration is approved, a complete outcome run is required evidence for tutor-policy changes and v1 release checks.
Provider-backed failures must never be hidden by the deterministic contract baseline.

## Bounded core-learning comparison

`bfs_queue_trace` and `noncoding_percentage_base` use this same outcome and behavior harness.
Each fixture specifies prior knowledge in its persona and imported lesson context, a misconception, initial pending question and review state, two practice responses, one unfamiliar transfer, and a retrieval response with a projected three-day gap.
BFS tests marking on enqueue and queue contents on a converging graph.
The noncoding case tests successive percentages applied to changing bases, with population change as transfer.
The initial assistant question is setup, followed by four generated tutor turns.
Scripted learner answers stay fixed regardless of the generated question; inspect transcript alignment before interpreting any model result.
These fixtures test response handling and metric contracts, not whether a tutor caused a person to learn.

Before an authorized paired diagnostic comparison, save one private comparison record with:

- Exact baseline and candidate commit SHAs, fixture hashes, and the single teaching change under comparison.
- The same provider endpoint, exact tutor model ID, distinct judge model ID, rubric version, mastery profile `proficient`, and resolved nonsecret configuration for both arms.
- Unchanged request defaults, token limits, metadata extraction and judge configuration from the recorded commits.
  The harness does not set a sampling seed or temperature, so record provider defaults and do not claim deterministic model output.
- Fresh isolated homes in both arms, the same fixture state and lesson context, no imported private source or previous learner history, and the same four scripted inputs in order.
- One run of each arm for each of these two cases, with baseline first for BFS and candidate first for percentages.
  Report each case and candidate-minus-baseline differences before any pooled result; retain failures and incomplete runs.
- A budget of four learner responses and at most four generated tutor turns per case, with no extra coaching or retry chosen because an answer was poor.
  Network retries remain the unchanged harness defaults and are recorded as operational context, not extra learner attempts.

Keep storage, selected-course context, navigation, remediation limits, models, budgets and grading settings fixed across arms.
Only the issue's teaching change is the treatment.
If unrelated commit differences change those controls, stop the comparison and select comparable commits before running it.
The harness already records state-linked turns and append-only event evidence; keep the comparison record alongside that evidence rather than adding an evaluation framework or modifying learner history.
No comparison or human study was performed by adding these fixtures.

## Report denominators and missing observations

Use a per-case, per-arm report with numerators, denominators and missing observations.
Do not treat a missing answer or incomplete run as a zero-cost success.
For this fixed four-turn protocol, separately report completion as observed responses out of four planned responses.

| Measure | Definition and denominator |
| --- | --- |
| Correctness | Passing `answer_judged` events divided by all observed judged answer events, retaining incorrect and recognition answers; also list unjudged planned responses. This raw tally is read from event evidence, not a new aggregate metric. |
| Qualifying evidence | Independent, nongamed, passing production events divided by observed judged answers; report recognition and supported answers separately. The existing metric reports first qualifying turn as `turns_to_criterion`; absence is missing criterion, not zero turns. |
| Unfamiliar transfer | Existing `novel_transfer.passed / attempts`; attempts require independent, nongamed production explicitly marked `is_transfer`. Supported and gamed answers are excluded from that eligible-attempt denominator, so also report eligible events out of planned transfer responses and list every excluded or missing response. Do not silently relabel familiar recall as transfer. |
| Delayed retrieval | Existing `delayed_retrieval.passed / attempts`; denominator requires valid retrieval source, concept, timestamp and minimum spacing. Recognition, gaming and disqualifying support remain failures in that denominator. Report planned retrievals lacking valid evidence separately. |
| Hints and examples | Count observed `remediation_hint` and `worked_example` selected moves separately out of generated tutor turns from behavior evidence. Existing dependency metrics count supported-correct concepts and unresolved concepts; they are not hint counts. |
| Tutor words | Existing word total and total divided by generated tutor turns. Words per mastered concept is undefined when no mastery event exists; inspect false mastery before interpreting it. Exclude the fixed setup assistant question as the harness does. |
| Turns | Count generated tutor responses out of four planned, and report first qualifying turn separately. A turn is one scripted learner input plus its generated tutor response. |
| Elapsed study time | For fixtures, report unavailable. Projected event gaps and provider latency are not learner study time. For a consented human run, record actual start, stop and paused durations with a local monotonic timer; report total active seconds per completed case and missing timing explicitly. |

The empty-denominator rate is unavailable, never zero or one.
Do not derive human correctness from a model judge's prose.
Independent transfer requires an actual unassisted response to a withheld problem; a fixture's phrase "Independently" alone cannot establish independence.
The metric tests use synthetic event inputs solely to prove these distinctions and do not calibrate thresholds.
Retain the uncalibrated diagnostic status and existing proposed thresholds until separately approved empirical calibration.

## Optional human protocol

Recruitment and live runs require separate permission, even if no paid provider is used.
Before a run, explain the task, data recorded, use of any external provider, voluntary participation, withdrawal and deletion options, and that this is a small comparison with no proven learning benefit.
Obtain explicit consent before recording any answer or sending it to a provider.
Use participant codes and synthetic lesson material; omit names, contact details and private imported sources from transcripts.
Keep the consent record separate from the private local evidence, agree on retention and deletion dates before starting, and never commit either to Git.

Use the same two concepts and four-response budget, with a maximum ten active study minutes per concept and no assistance during transfer or retrieval.
Counterbalance arm assignment across consenting participants: one group receives baseline BFS and candidate percentages, and the other receives candidate BFS and baseline percentages.
Do not teach the same concept twice to one person as if the second arm had the same prior knowledge.
Record an initial unassisted attempt and prior knowledge before teaching; record differences instead of claiming the two concepts are equivalent.
Freeze the prompts, answer keys and scoring criteria before collecting results.
The diagnostic fixtures expose answers in context; for humans, withhold transfer and retrieval problems and keys until their scheduled assessments, and never preload scripted learner responses.
Score queue contents plus marking timing for BFS, and calculation plus changing-base explanation for percentages.
Use the same scorer and criteria across arms and blind the scorer to arm when practical; report raw counts for this small sample.

Schedule an actual no-notes retrieval three days later, record the real interval, and do not send reminders containing lesson answers.
Report missing follow-up as attrition with the original assigned denominator alongside observed-attempt results; do not replace it with a projected timestamp or an invented response.
Record actual active study timing and assistance separately from the waiting interval and provider latency.
Stop on withdrawal, distress, exhausted time or response budget, accidental private-data disclosure, provider failure, or an explicit request to skip or end.
Preserve explicit navigation and bounded remediation; label stopped cases incomplete and never mark exhaustion as mastery.
No participant recruitment, provider run or human retention result is implied by deterministic tests or model-judged diagnostics.
