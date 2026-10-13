# Review-card evaluation

This opt-in runner generates fresh cards from eight versioned synthetic fixtures, evaluates their semantic quality with a distinct judge model, and creates an isolated native Review deck.
It never reads learner topics or writes to the configured learner home.
Live provider calls are excluded from `make check`; provider-mocked harness tests run in the normal gate.

## Run

Use a new, nonexistent run root outside the configured learner home.
The runner reads the configured provider credentials into memory before switching all calls and writes to `<run-root>/home`.
It creates no credential-bearing config copy.
Provider environment precedence remains unchanged, and live mode rejects an active `OPENLEARN_MOCK` setting.

```sh
OPENLEARN_HOME=/path/to/provider-config-home make review-card-eval \
  RUN_ROOT=.artifacts/review-evaluation/new-run \
  GENERATOR_MODEL=deepseek/deepseek-v4.1-flash \
  JUDGE_MODEL=openai/gpt-5.4-mini LIVE=1 MAX_CALLS=24
```

The equivalent script accepts `--live`, `--generator-model`, `--judge-model`, and `--max-calls`.
Without `LIVE=1` or `--live`, it sends no provider requests and exits with status 2.
Choose models available from your configured OpenAI-compatible provider; the runner does not query a model catalog.
The judge identifier must differ from the generator identifier.
Each request has one attempt, and the entire run permits at most 24 provider calls.
The usual full run costs 18 calls: three calibration judgments, eight generation requests, and seven generated-card judgments.
Unexpected generation for the insufficient-source case requires a nineteenth judgment and fails that case.

For one scoped repair in a new run root, add `CASE=noise-outlier-overlap MAX_CALLS=6`, or pass `--case noise-outlier-overlap` to the script.
The script permits repeated `--case` selections.
All three calibration cards are judged again; one selected supported case normally uses five calls.
Reusing an existing run root is rejected, including an empty directory or symlink.

## Criteria and calibration

`tests/evals/fixtures/review_cards_v1.json` contains seven supported topics and one deliberately insufficient source.
Each fixture has an authoritative synthetic source, a focused retrieval goal, intended answer points, and quality criteria.
The judge evaluates completeness relative to the actual generated question, source fidelity, question focus, and explanatory added value.
A requested reason must appear in the answer itself.
Intended fixture points define question scope; a question omitting that scope fails relevance rather than imposing unasked requirements on its answer.
Numeric questions must show needed inputs or clearly ask recall of the named source experiment.
This is semantic evaluation, not keyword coverage.

The judge must pass a minimum-complete calibration card and identify the specific missing-reason and unsupported-claim failures in two negative cards.
Expected calibration labels are withheld from its prompt.
Missing, malformed, or misclassified calibration judgments taint the entire run.
Judge output requires exact boolean criteria and finite numeric scores in `[0, 1]`, with every passing dimension scoring at least `0.75`.
A genuine production `InsufficientReviewSource` refusal passes the insufficient-source case; a transport, JSON, or validation error does not.
Live outcomes are bounded evidence for these fixtures, not a guarantee of model quality.

## Evidence and native deck

The private run root contains `report.json`, `summary.md`, and `home/learning-topics/review-evaluation.md`.
The report binds fixture/rubric versions and SHA-256 hashes to the exact Git HEAD, tracked dirty-state flag, and generator/runner source hashes.
It records mode, model identifiers, actual provider-call count, calibration observations, per-case status, fixed failure codes, and actual generated card contents.
A fixture snapshot makes the report's source assumptions inspectable.
Reports are checkpointed before calls and after results, so interruptions or network failures leave partial evidence rather than a false pass.
Generated credentials and terminal controls are redacted; any changed card is explicitly marked and cannot pass.
Raw provider exceptions and malformed judge text are never copied into reports.
Files use mode `0600` beneath a fresh `0700` root.

Every successfully generated card enters the native deck, including quality failures and cards whose judge failed.
Refusals and failed generation do not become substitute or mock cards.
Production generation creates fresh card identities and timestamps, and the report/deck preserve those identities.
Calibration cards never enter the deck.
Mock runs are explicitly labeled `mock` and provide harness evidence only.
To inspect the deck, launch the normal local web interface with `OPENLEARN_HOME=<run-root>/home` on a separate port and open Review for the `review-evaluation` course.
The runner itself starts no server or browser.

Exit status is 0 only for a complete, calibrated passing run, 1 for completed runs with failed or missing evidence, and 2 when the run cannot start.
Read per-case outcomes even after a failed run; successful generated cards remain available for human inspection.
