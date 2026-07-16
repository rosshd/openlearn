# Agent Runs

This document keeps long-run agent behavior durable without making `AGENTS.md` large.
Read it before starting fleets, long autonomous runs, review passes, or PR shipping loops.

## Default Mode

Default to one agent working in the current repo.
Do not spawn fleets, subagents, background loops, browser QA, no-mistakes, or PR automation unless the user explicitly asks for that mode.
If the user asks for status, diagnosis, or review, inspect and report before changing anything.

## Repository State Model

Use these terms precisely in status reports and decisions:

- `main` is the local integration branch and the only source of truth for completed work.
- `origin/main` is the local remote-tracking snapshot of GitHub's `main`; agents do not edit it directly.
- A branch is a line of commits, not a checkout or a copy of files.
- A worktree is one checkout attached to one branch or commit.
- A tag names a release commit; a GitHub Release publishes that tag with release metadata and artifacts.

The root checkout should remain on `main`.
Implementation work should use one short-lived branch with one matching worktree under `.worktrees/<task>`.
Default to one active feature worktree and never exceed two without explicit user approval.
Do not use a version branch such as `v0.7.0` as a catch-all development branch.
Create a version branch only while actively preparing that release.

## Start-of-Task Preflight

Before creating or changing a branch or worktree, inspect the current topology:

```bash
git status --short --branch
git worktree list
git branch --sort=-committerdate -vv
git fetch origin main
git rev-list --left-right --count origin/main...main
```

If both divergence counts are nonzero, stop and report that local and remote `main` have diverged.
Do not guess whether to merge, rebase, reset, or force-push.

If local `main` is only behind, fast-forward it before branching:

```bash
git switch main
git pull --ff-only origin main
```

For an authorized implementation task, create a meaningful branch and matching worktree from current `main`:

```bash
git worktree add -b <type>/<task> .worktrees/<task> main
```

Use names such as `feat/quick-review`, `fix/repl-resubmit`, or `docs/agent-workflow`.
Do not create a worktree for status, diagnosis, review-only work, or a one-command read-only check.

## One-Task Lifecycle

Each task branch owns one scoped deliverable.
Do not accumulate unrelated fixes, release preparation, and repository cleanup on the same feature branch.

Before handoff or shipping:

1. Inspect `git status --short` and the complete diff against `main`.
2. Run the relevant focused verification while implementing.
3. Run `make review` before a PR or manual merge.
   For an automatically reviewed fleet branch, the lightweight fleet gate below satisfies the pre-merge review requirement.
4. Commit only the scoped files with a value-focused message.
5. Push or open a PR only when the user requested shipping.

Prefer a PR for behavior changes, even in a solo-maintainer repository.
The PR is the durable place for CI, the final diff, and the merge decision.
Do not bypass a required status check or push directly to `main` unless the user explicitly requests that exact action.
If GitHub reports that a rule was bypassed, report it in the handoff.

After the branch is merged, return to the root checkout on `main` and finish cleanup in the same run when authorized:

```bash
git pull --ff-only origin main
git worktree remove .worktrees/<task>
git branch -d <type>/<task>
```

Do not leave a merged worktree or local feature branch behind for a later agent.
If cleanup is not authorized, provide the exact cleanup commands in the handoff.

## Branch and Worktree Cleanup

Git history is the archive for merged work.
Delete merged local branches instead of accumulating an `archive/` namespace.
Archive a branch only when it contains intentionally deferred work that is not represented on `main`.

Before removing a dirty worktree or force-deleting an unmerged branch:

1. Inspect its dirty files and commits.
2. Compare its behavior and targeted diff with `main`.
3. Check whether equivalent work landed under different commit hashes after a rebase, squash, or rewrite.
4. Preserve the ref and stop if equivalence cannot be demonstrated safely.

Never bulk force-delete unmerged branches.
Never treat `git branch --merged` as complete proof when agents may have recreated or squashed equivalent work.

## Release Discipline

Normal feature work targets `main` through short-lived task branches, regardless of the planned release number.
Use a version branch only for active release preparation.

Before publishing, align all release identifiers:

- `src/openlearn/__init__.py` contains the intended version.
- The release commit is on `main`.
- The `vX.Y.Z` tag points to that release commit.
- The GitHub Release uses the same tag.

Do not bump the version merely to begin general work for the next milestone.
Do not reuse or move an existing release tag.
Run the release verification described in `docs/DEVELOPMENT.md` before pushing the tag.

## Repo Commands and Captain

Use the repository commands for local interactive work:

```bash
make repo-status
make worktree NAME=quick-review TYPE=feat
make finish NAME=quick-review
```

`make repo-status` reports the version, cached `main` divergence, worktrees, dirty state, and branches not merged into `main`.
`make worktree` refuses to start unless the root checkout is clean, local and remote `main` match, and fewer than two linked worktrees exist.
`make finish` refuses to remove a dirty worktree or a branch that is not merged into `main`.

Ross's captain commands are the outer-loop control surface and complement these repo-local commands:

```bash
captain status
captain watch
captain brief <slug>
captain start <slug>
captain done <worktree-path>
captain review "<intent>"
captain dispatch "<task>"
```

Use `captain status` for the one-shot cross-repo, fleet, Treehouse, gate, and GitHub view.
The Fish alias `deck` runs `captain status`.
Use `captain watch` for the live dashboard; the Fish alias is `watchdeck`.
Use `captain brief` and `captain start` for bounded autonomous fleet work.
Use `captain review` to run the no-mistakes gate for interactive-lane work.
Use `captain done <path>` for Treehouse or captain-managed worktrees.
Do not use `make finish` for Treehouse paths outside this repository's `.worktrees/` directory.

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
