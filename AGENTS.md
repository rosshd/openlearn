# openlearn agent map

openlearn is a local-first AI tutoring application.
Keep this file short and follow [`docs/AGENT_RUNS.md`](docs/AGENT_RUNS.md) for implementation, review, shipping, merge, and verification work.

## Fast context

- Entry point: `openlearn`, implemented mostly in `src/openlearn/cli.py`.
- Core helpers: `src/openlearn/constants.py`, `src/openlearn/models.py`, `src/openlearn/stats.py`, `src/openlearn/text.py`, and `src/openlearn/ui.py`.
- Tests: `tests/`, with human smoke flows in `manual-tests/`.
- User-owned data: `learning-topics/*.md`, `*.state.json`, `*.events.jsonl`, `state.json`, `config.json`, and imported context files.

## Protected boundaries

- Keep the product local-first.
- Preserve the Markdown plus JSON topic format unless the issue explicitly changes storage.
- Respect config precedence: environment variables, then `config.json`, then defaults.
- Keep prompt, storage, and learner-model changes scoped and test-backed.
- Reproduce or reason from the user-visible flow before fixing a bug.
- Never commit topic files, imported context, state files, config, API keys, or `.env`.
- Treat `.artifacts/`, `dist/`, `build/`, and `*.egg-info/` as generated output.
- Recreate generated output with its owning command instead of editing it.

## Skill routing

These `.claude` skills are optional local installations; if absent, use the current repository code, tests, and `docs/AGENT_RUNS.md` instead of assuming their contents.

- Use `.claude/skills/openlearn-validate/` after code edits, before review, before push, or when asked to validate work.
- Use `.claude/skills/openlearn-architecture/` for storage, provider, import, event log, or module-boundary work.
- Use `.claude/skills/openlearn-tutor-policy/` for tutor prompts, answer judging, mastery, anti-gaming, quiz, SRS, and learning-science decisions.
- Use `.claude/skills/openlearn-phase-review/` when reviewing a phase implementation or writing the next phase prompt.

## Owner workflow

1. Use one GitHub issue as the durable brief for each bounded change.
2. Map that issue to one Codex owner task and one managed worktree.
3. Record the task ID, issue, worktree, branch, and exact start SHA before editing.
4. Keep changes within the issue's permissions, acceptance checks, constraints, and non-goals.
5. Run focused checks first, then run the canonical local gate with `make check`.
6. Record the exact tested HEAD and obtain one bounded independent review of that same HEAD.
7. When shipping is authorized, open a pull request targeting the immediate parent's branch (or `main` for root-owned work) with gate, review, risk, CI, verification, and rollback evidence.
8. Require the strict GitHub `test` check on the exact reviewed HEAD.
9. Follow the authority precedence in `docs/AGENT_RUNS.md`; required exact-head CI, restrictive issue permissions, CI pauses, and high-risk approval remain binding.
10. After merge, verify the repository and any deployed or installed behavior named by the issue before closing it.

Do not duplicate the issue in a repository plan or add repository-specific task automation.
Treat secrets, purchases, destructive cleanup, production deployment, and permission expansion as separate authorization boundaries.

## Verification

`make check` is the one canonical local gate.
It runs lint, pytest (including unittest-based cases), and mocked CLI smoke coverage.

`make review` is an optional evidence collector.
It reuses valid content- and environment-bound gate evidence, or runs the gate when that evidence is stale, then writes a diff and receipt under `.artifacts/review/`.
It does not replace the bounded independent review.

For fresh-worktree setup, isolated mock browser QA, owner registration, and evidence reuse, follow `docs/AGENT_RUNS.md`.

`make typecheck` is available but non-blocking.
Run slow AI-judge evals only when the issue covers model-output quality.

## Release rules

Follow [`docs/RELEASING.md`](docs/RELEASING.md) for release work.
Release only from an explicitly authorized, exact commit on `main` after its required checks pass.
Never reuse or move a published release tag, rebuild an immutable candidate during promotion, or include learner-owned data in an artifact.
