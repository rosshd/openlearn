# openlearn Agent Map

openlearn is a local-first AI tutoring CLI.
Keep this file short and route deeper context to skills only when needed.

## Fast Context

- Entry point: `openlearn`, implemented mostly in `src/openlearn/cli.py`.
- Core helpers: `src/openlearn/constants.py`, `src/openlearn/models.py`, `src/openlearn/stats.py`, `src/openlearn/text.py`, `src/openlearn/ui.py`.
- Tests: `tests/`, with human smoke flows in `manual-tests/`.
- User-owned data: `learning-topics/*.md`, `*.state.json`, `*.events.jsonl`, `state.json`, `config.json`, and imported context files.

## Non-Negotiables

- Keep the product local-first.
- Preserve the Markdown plus JSON topic format unless explicitly changing storage.
- Respect config precedence: environment variables, then `config.json`, then defaults.
- Do not commit topic files, imported context, state files, config, API keys, or `.env`.
- Keep prompt, storage, and learner-model changes scoped and test-backed.
- For bug fixes, reproduce or reason from the user-visible flow before patching.

## Skill Routing

- Use `.claude/skills/openlearn-validate/` after code edits, before review, before push, or when asked to run checks.
- Use `.claude/skills/openlearn-architecture/` for storage, provider, import, event log, or module-boundary work.
- Use `.claude/skills/openlearn-tutor-policy/` for tutor prompts, answer judging, mastery, anti-gaming, quiz, SRS, and learning-science decisions.
- Use `.claude/skills/openlearn-phase-review/` when reviewing a phase implementation or writing the next phase prompt.

## Agent Run Hygiene

- Treat `main` as the only integration source of truth and `origin/main` as its remote-tracking snapshot.
- Keep the root checkout on `main`; do scoped implementation in a short-lived branch and matching `.worktrees/<task>` checkout.
- Default to one active feature worktree and never exceed two without explicit user approval.
- Finish the branch lifecycle in one handoff: verify, commit, ship only when authorized, then remove the merged worktree and local branch.
- Use version branches only for active release preparation; normal v0.x work still belongs in short-lived task branches.
- Use `make repo-status`, `make worktree NAME=<task> TYPE=<type>`, and `make finish NAME=<task>` for the repo-local lifecycle.
- When Ross is using the captain workflow, remember that `captain status`, `captain watch`, and `captain done <path>` own cross-repo, fleet, and Treehouse operations.
- Read `docs/AGENT_RUNS.md` before starting fleets, long autonomous runs, review passes, or PR shipping loops.
- Do not spawn fleets, subagents, background loops, browser QA, no-mistakes, or PR automation unless the user explicitly asks for that mode.
- For fleet prompts, keep the stop condition narrow: one scoped deliverable, one verification gate, one concise handoff.
- Stop and report after the first repeated agent/tool failure instead of retrying through expensive context reloads.
- Leave a status note with branch, worktree, commit, dirty files, verification, and next review command before handing off.

## Verification

Green gate:

```bash
make check
```

Before a PR:

```bash
make review
```

`make typecheck` is available but non-blocking.
Run slow AI-judge evals only when the task is about model-output quality.
