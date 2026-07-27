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

The two initial scenarios make six generated tutor turns.
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
The existing event-log delayed-retrieval function computes its metric from those projected durable events.

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

Run the lane before merging any tutor-policy, answer-judging, mastery, remediation, quiz, SRS, or retrieval-scheduling change.
Compare the candidate run against a run from the current `main` using the same tutor and judge model pair.
Review the human summary and the underlying scenario records before attributing a metric change to the policy.

Run the lane during v1 release checks after `make check` and before the final release decision.
Until calibration is complete, attach the summary as diagnostic release evidence.
Once calibration is approved, a complete outcome run is required evidence for tutor-policy changes and v1 release checks.
Provider-backed failures must never be hidden by the deterministic contract baseline.
