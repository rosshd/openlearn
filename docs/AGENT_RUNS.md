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

## Fleet Prompt Template

Use this shape for bounded fleet work:

```text
Implement only: <small deliverable>.
Scope: <files or behavior in scope>.
Out of scope: PR creation, no-mistakes, browser QA, unrelated refactors, dependency changes.
Verification: run <one relevant command>.
Stop after: one successful commit, or the first repeated agent/tool failure.
Handoff: report branch, commit, dirty files, verification result, and next review command.
```

Use this stop condition:

```text
Stop when the scoped change is committed and the named verification command has passed.
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
6. Decide whether the branch is ready for a normal code review, needs a small follow-up, or should be discarded.

Do not continue a failed fleet just to "see if it finishes".
If a previous run stopped after repeated agent exits, review the current diff manually before any restart.

## Current Openlearn Fleet Review Commands

For the current stats-dashboard fleet worktree:

```bash
cd /Users/ross/Developer/worktrees/openlearn/fleet-stats-dashboard
git status --short
git log --oneline main..HEAD
git diff --stat main...HEAD
sed -n '1,220p' .gnhf/runs/follow-the-brief-bel-6c22ab/notes.md
make check
```

If those checks look good, review the uncommitted edits separately from commit `7680b84`.
Do not restart the fleet until the dirty diff has been understood.
