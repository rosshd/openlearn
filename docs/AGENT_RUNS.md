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

The owner task stops after local verification unless the issue and current request authorize shipping or another external action.

## Canonical gate and evidence

`make check` is the one canonical local gate required before push.
CI invokes that gate and also runs the repository's cross-platform, package, browser, and security jobs before the aggregate `test` check passes.

`make review` is optional.
It collects the diff and a fresh `make check` log under `.artifacts/review/`.
Its artifacts may support a review, but running it is not an independent review and does not approve a change.

Run `git diff --check <base>...HEAD` before shipping.
Do not weaken or bypass a failing gate.

## Independent review

After `make check` passes, assign one bounded reviewer who did not implement the change.
Give the reviewer the issue, exact tested HEAD, base branch, diff, repository instructions, and validation evidence.
The reviewer checks acceptance criteria, regressions, security and data boundaries, test coverage, and repository conventions.

Record the reviewer identity, reviewed commit, disposition, and findings.
A clean review applies only to that exact commit.
Any change to HEAD invalidates the gate and review evidence.
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

- The user explicitly authorized merge.
- Required CI passes on the exact reviewed head.
- Review findings are resolved or accepted within the issue's risk and permissions.
- The branch still contains only the issue's scope.
- The recovery path remains valid.

After merge, verify the merged commit on `main` and rerun the focused repository check named by the issue.
For a deployed service or installed artifact, verify the named live or installed behavior rather than inferring success from source or CI.
Record the merged SHA, verification evidence, and rollback path before closing the issue.
Clean the worktree or branch only when that cleanup is authorized.

## Risk and permissions

Low and medium risk work may merge only when the issue authorizes the merge and all required evidence is current.
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
