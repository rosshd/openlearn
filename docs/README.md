# Documentation

This index separates current product documentation from implementation references and test procedures.
Completed plans remain available in Git history instead of living beside current instructions.

## Use openlearn

- [Install](INSTALL.md) covers supported platforms, first launch, upgrades, and uninstalling.
- [Data and privacy](DATA_AND_PRIVACY.md) covers the learner home, backup, restore, move, reset, and deletion.
- [Troubleshooting](TROUBLESHOOTING.md) covers installation, providers, the local web app, and optional code execution.
- [Topic format](TOPIC_FORMAT.md) documents the local Markdown and JSON course format.

## Understand the product

- [Product plan](PLAN.md) is the canonical current scope and release sequence.
- [Architecture](ARCHITECTURE.md) documents storage, providers, tutor state, curriculum, imports, and interfaces.
- [Tutor interaction](TUTOR_INTERACTION.md) defines judging, move selection, checks, mastery, and progression.
- [Learning science](LEARNING_SCIENCE.md) records the learning rules behind tutor behavior.
- [Dependencies](DEPENDENCIES.md) records approved, rejected, and deferred dependencies.

## Develop and release

- [Development](DEVELOPMENT.md) lists the local loop and verification commands.
- [Agent runs](AGENT_RUNS.md) defines branch, worktree, review, and shipping discipline.
- [Releasing](RELEASING.md) defines artifact creation, verification, publication, and correction.
- [Interview problem catalog](INTERVIEW_PROBLEM_CATALOG.md) defines rights and versioning for bundled practice problems.

## Evaluation

- [Dogfood evidence](DOGFOOD_EVIDENCE.md) documents the opt-in Codex explorer and tutor behavior harness.
- [Outcome evaluation](OUTCOME_EVAL.md) documents delayed learning-outcome evaluation.
- [`manual-tests/`](../manual-tests/README.txt) contains isolated human smoke journeys.

Historical milestone and implementation plans are intentionally absent from the current tree.
Use Git history when a past decision needs investigation.
