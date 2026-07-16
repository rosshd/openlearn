# Agent Runs

This document keeps long-run agent behavior durable without making `AGENTS.md` large.
Read it before starting fleets, long autonomous runs, review passes, or PR shipping loops.

## Default Mode

Default to one agent working in the current repo.
Do not spawn fleets, subagents, background loops, browser QA, no-mistakes, or PR automation unless the user explicitly asks for that mode.
If the user asks for status, diagnosis, or review, inspect and report before changing anything.

## Token Hygiene

Use narrow prompts.
Give the agent one scoped deliverable, one verification gate, and one handoff format.
Avoid broad stop conditions such as "green PR" unless the user explicitly wants the full shipping pipeline.

Cap retries.
After one repeated agent or tool failure, stop and report the failing command, observed state, and recommended next step.
Do not keep restarting a fleet when the agent exits with no useful output.

Keep context small.
Load only the files needed for the task.
Prefer `rg` and targeted file reads over broad transcript, log, or directory dumps.
Summarize long logs instead of pasting them back into the model unless exact lines are required.

Separate implementation from shipping.
For uncertain work, ask for implementation plus local verification first.
Run no-mistakes, browser QA, PR creation, or babysitting only after the implementation is reviewed or the user explicitly asks for shipping automation.
Every fleet must pass an independent lightweight diff review and its named verification gate before its branch is eligible for integration.
A capped, failed, dirty, or unreviewed fleet must never be integrated automatically.

Use no-mistakes as the full shipping gate, not as the default review for every small fleet.
Run it after integrating a meaningful batch, before a push, PR, or release, after a high-risk cross-cutting change, or when the user explicitly asks for full shipping validation.
If several small fleets are headed to the same release, review each fleet lightly and run no-mistakes once on the integrated release branch.

## Fleet Prompt Template

Use this shape for bounded fleet work:

```text
Implement only: <small deliverable>.
Scope: <files or behavior in scope>.
Out of scope: PR creation, no-mistakes, browser QA, unrelated refactors, dependency changes.
Verification: run <one relevant command>.
Stop after: the scoped outcome is complete and the verification command passes, or the first repeated agent/tool failure.
When running under GNHF, set `should_fully_stop=true` when that end state is achieved and let GNHF create the iteration commit after the response.
Handoff: report branch, commit, dirty files, verification result, and next review command.
```

Use this stop condition:

```text
Stop when the scoped change is complete and the named verification command has passed.
Under GNHF, set `should_fully_stop=true` in that final iteration; GNHF commits successful changes automatically after the response, so do not wait for the commit to exist and do not commit manually.
If an agent/tool failure repeats once, stop and report instead of retrying.
```

Avoid this stop condition unless the user asks for full shipping:

```text
Stop when the project green gate passes, the change is committed, no-mistakes has produced a green pull request, and CI is green.
```

## Required Handoff

Every long run or fleet handoff should include:

- Branch and worktree path.
- Latest commit hash and message, or "no commit".
- Dirty files from `git status --short`.
- Verification commands run and their results.
- Any failures or retries, including token-heavy loops.
- Exact next command for review.

## Review Checklist

For a branch or worktree produced by a fleet:

1. Inspect status with `git status --short`.
2. Inspect commits with `git log --oneline main..HEAD`.
3. Inspect the diff with `git diff --stat main...HEAD` and then targeted `git diff`.
4. Read the run notes if present under `.gnhf/runs/*/notes.md`.
5. Run the relevant verification command, usually `make check`.
6. Perform an independent lightweight review of the changed behavior, tests, security boundaries, and project standards.
7. Fix concrete findings on the fleet branch and rerun the verification gate.
8. Mark the branch integration-ready only when the review is clean and the gate is green.
9. Decide whether the branch should be integrated, needs a follow-up, or should be discarded.

Do not continue a failed fleet just to "see if it finishes".
If a previous run stopped after repeated agent exits, review the current diff manually before any restart.

## Current Checkout Review Commands

Run review commands from the checkout or worktree that the user gave you:

```bash
git status --short
git log --oneline main..HEAD
git diff --stat main...HEAD
if [ -d .gnhf/runs ]; then find .gnhf/runs -maxdepth 3 -path '*/notes.md' -type f -print; fi
make check
```

If run notes exist, read only the notes for the run being reviewed.
Review uncommitted edits separately from committed changes.
Do not restart a fleet until the current diff has been understood.

## Integration And Shipping Gates

Use the lightweight fleet gate before bringing any fleet branch into the current working branch:

```bash
git diff --check main...HEAD
make check
```

The reviewer must also inspect the actual diff and record a disposition of `reviewed` or `review-failed`.
Reserve `ship-ready` for a successful full `green-pr` shipping gate.
Passing tests alone is not an approval.

Use the full gate when the integrated work is about to leave the local repository:

```bash
make review
no-mistakes
```

Do not run the full gate repeatedly on unchanged commits.
Rerun it when the integrated diff changes, a prior gate fails, review fixes land, or the push target changes.
