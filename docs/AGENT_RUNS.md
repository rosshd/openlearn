# Agent runs

This runbook defines the normal repository workflow for implementation, review, shipping, merge, and post-merge verification.
The GitHub issue is the durable brief and source of truth.

## Dispatch contract

A ready issue must state Outcome, Acceptance checks, Constraints, Non-goals, Evidence, Risk, Permissions, Dependencies, and Verification.
Revalidate any named SHA, branch, failure, dependency, API, or external state before work starts.
Return an issue to planning if its acceptance checks or permissions are ambiguous, or if a dependency remains unresolved.

One issue maps to one Codex owner task and one managed worktree.
Before editing, record:

- GitHub issue number and URL.
- Codex owner task ID.
- Worktree path and branch.
- Exact start SHA and intended base branch.

Do not create a second task brief or local queue for the same work.
Do not combine unrelated issues in one branch.

## Owner task

1. Read the full issue and repository instructions.
2. Inspect the worktree status, branch, remotes, base SHA, relevant implementation, tests, CI, and release path.
3. Stop on dirty-state overlap, ambiguous ownership, stale evidence that changes scope, or permissions that do not cover the required action.
4. Implement the smallest coherent change that satisfies the issue.
5. Preserve unrelated work, dependencies, product behavior, generated files, and user-owned data unless the issue explicitly authorizes a change.
6. Run focused checks while implementing.
7. Inspect the complete diff and run `make check`.
8. Record the exact tested HEAD, gate command, result, and any intentionally skipped coverage.

Standalone implementation stops after local verification unless shipping is requested.
For factory work, Ross's standing low/medium-risk authorization covers permitted push, PR, CI monitoring, merge, and release verification steps.
Restrictive issue permissions, an explicit CI/budget pause, and current user constraints take precedence over standing authorization.
High-risk merge or activation requires Ross's explicit decision after review.

## Parent and child branches

Each delegated owner uses its own branch and worktree and opens its reviewed PR against the immediate parent's integration branch.
Record the root task, immediate parent task, intended PR base, exact base SHA, and dependencies on the issue.
The parent performs acceptance review and integrates child PRs with one writer per integration branch.
Child checks prove the child candidate only; the parent verifies and independently reviews the combined candidate before submitting upward.
The root owns `main` integration and release verification.
Retire a merged source branch only after proving the content is preserved, no dependent PR or active owner needs it, and cleanup is authorized.
Archive managed worktrees recoverably when available; a completed chat archive is not a process-termination mechanism.

## Fresh-worktree setup and isolated QA

Run `scripts/setup-worktree --browser` in the candidate worktree to create its own Python 3.11-3.13 environment and install development dependencies plus Chromium.
This uses `uv`; it does not link or modify another checkout's virtual environment.
Then run `make qa-smoke` for a fresh mock course, authenticated browser selection and reload, and server shutdown.
The launcher uses a private fixture home and a provider-free environment, selects an available loopback port, and records candidate, PID, port, logs, journey, and cleanup evidence under `.artifacts/qa/`.
It never uses the primary learner home or its running server.
Fixtures are intentionally retained as private debugging evidence, not copied into source or committed.
The server capability stays inside its private fixture and is excluded from handback evidence.
The browser smoke is opt-in and separate from the canonical CLI gate.

Native Codex local-environment setup/actions may point to `scripts/setup-worktree --browser` and `make qa-smoke`.
Host UI activation remains manual until the supported configuration schema is verified; do not invent `.codex` TOML.

After registering ownership on the issue, run `scripts/repo-workflow register <task-id> <issue-url>` from the linked worktree.
For child work, add `--base <parent-branch>` to preserve the local dependency guard.
The finish helper refuses registered active owners or children; separately verify remote PR dependencies before authorizing removal.
Use `scripts/repo-workflow retire` only after that owner's work stops; it changes the local supplemental index, not GitHub state or files.
The index lives in the shared Git directory and is not a second task queue.
Capacity counts active registered owners and unresolved external work, not every historical worktree.
Clean merged unregistered worktrees are settled; dirty, unmerged, missing, or invalid worktrees remain unresolved conservatively.
Set `OPENLEARN_ACTIVE_LIMIT` only to the concurrency budget Ross approved.
No inventory command deletes historical branches or files.
Use `make worktree NAME=<task> BASE=<parent-branch>` to start a child from a committed parent candidate without changing the primary checkout.
The default base is freshly fetched `origin/main`, not unpublished or dirty primary work.

## Canonical gate and evidence

`make check` is the one canonical local gate required before push.
CI invokes that gate and also runs the repository's cross-platform, package, browser, and security jobs before the aggregate `test` check passes.

`make review` is optional.
It reuses a passing receipt only when candidate content, file modes, interpreter, installed dependencies, gate command, and relevant environment match, and the recorded log is intact.
Source, dependency, environment, command, or log changes invalidate reuse.
Otherwise it runs the gate once and collects the diff plus receipt under `.artifacts/review/`.
Use `make review BASE=<parent-branch>` for a child PR.
Its artifacts may support a review, but running it is not an independent review and does not approve a change.

Run `git diff --check <base>...HEAD` before shipping.
Do not weaken or bypass a failing gate.

## Independent review

After `make check` passes, assign one bounded reviewer who did not implement the change.
Give the reviewer the issue, exact tested HEAD, base branch, diff, repository instructions, and validation evidence.
The reviewer checks acceptance criteria, regressions, security and data boundaries, test coverage, and repository conventions.

Record the reviewer identity, reviewed commit, disposition, and findings.
A clean review applies only to that exact commit.
Any change to HEAD invalidates independent review evidence.
Gate evidence may remain valid after a metadata-only commit when the content and environment binding is unchanged; record the current exact HEAD in the handoff.
Integration changes the candidate and requires new parent gate evidence.
If fixes are authorized, rerun focused checks and `make check`, then allow at most one targeted rereview of those fixes.

## Pull request and CI

Push without force only when the issue and current request authorize it.
Open one pull request that references the issue without closing it early.
The pull request must record:

- Owner task, worktree, branch, start SHA, and exact head SHA.
- Scope and important decisions.
- Exact `make check` result and focused checks.
- Independent review evidence.
- Risk and remaining risk.
- Required CI status for the exact head.
- Product, release, deployment, or installed-artifact verification that applies.
- Recovery or rollback steps.

Require the strict GitHub `test` check on the exact reviewed head.
Do not treat CI from an older commit as evidence for the current pull request.

## Merge and post-merge verification

Merge only when all of these conditions hold:

- Standing low/medium factory authorization or the current request covers merge, and the issue does not restrict it.
- Required CI passes on the exact reviewed head.
- Review findings are resolved or accepted within the issue's risk and permissions.
- The branch still contains only the issue's scope.
- The recovery path remains valid.

After merge, verify the merged commit on `main` and rerun the focused repository check named by the issue.
For a deployed service or installed artifact, verify the named live or installed behavior rather than inferring success from source or CI.
Record the merged SHA, verification evidence, and rollback path before closing the issue.
Clean the worktree or branch only when that cleanup is authorized.

## Risk and permissions

Low and medium risk work may continue through allowed shipping steps when the issue permits them and all required evidence is current.
High risk requires an explicit human decision after review and CI.
Treat production actions, secrets, purchases, destructive cleanup, data mutation, and permission expansion as separate authorization boundaries.
Prepare repository-host settings for review and change them only with explicit authorization.

## Handoff

Every owner or reviewer handoff must include:

- Issue, task ID, branch, worktree, start SHA, and current HEAD.
- Dirty files and scoped commit list.
- Exact commands run and their results.
- Review disposition and applicable reviewed SHA.
- CI state for the current SHA.
- Remaining actions, required authority, and recovery path.
