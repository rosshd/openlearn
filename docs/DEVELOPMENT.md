# Development Workflow

## The Implementation Loop

Each version of openlearn is built in phases. Phases are implemented by a builder AI
(ChatGPT, Claude, or any capable model) using a detailed prompt, then reviewed before
moving forward. This document defines the review rules so the loop stays tight and
consistent across sessions.

---

## Phase Lifecycle

```
Write phase prompt → Builder implements → Reviewer assesses → Decision → Next phase
```

### Step 1 — Write the phase prompt
The prompt must be self-contained and include:
- Exact file paths and function names / line numbers
- What to add, what NOT to touch
- Test cases to write
- A clear stopping condition ("all N tests pass, no regressions")

### Step 2 — Builder implements
Builder AI implements the phase from the prompt alone. No conversation context assumed.

### Step 3 — Reviewer assesses (this session's job)
After implementation, the reviewer (Claude Code in this session) does:

1. `git diff HEAD --stat` — scope check. Flag if too many files changed.
2. `git diff HEAD` — read every line of the diff.
3. `python -m unittest` + `pytest tests/workflows/` — tests must pass.
4. Verify each specific deliverable from the phase prompt was hit.
5. Identify issues and classify them:

| Class | Definition | Action |
|-------|-----------|--------|
| **Blocking bug** | Logic error, data corruption, silent failure, test regression | Fix immediately before moving on |
| **Missing deliverable** | Prompt said to do X, it wasn't done | Fold into next phase prompt as "carry-forward fix" |
| **Minor / low-risk** | Awkward but harmless, style, one-liner improvement | Fold into next phase prompt as "carry-forward fix" |
| **Clean** | Implemented correctly and completely | Note as confirmed, no action needed |

### Step 4 — Decision

**Block and fix now** if:
- Any test is failing
- Data can be corrupted (e.g. wrong metadata key cleared)
- The missing piece is required by the NEXT phase (would break Phase N+1 if skipped)

**Carry forward** if:
- Tests pass and nothing is broken
- The gap is an additive improvement (e.g. missing test coverage, missing context line)
- The next phase prompt can include "also do this leftover from Phase N" without adding risk

**Move on clean** if:
- All deliverables confirmed
- Tests pass
- Nothing carried forward

### Step 5 — Output next phase prompt
Prepend a "Carry-forward from Phase N" section to the next prompt listing any
items being folded in. This way the builder sees them as required, not optional.

---

## End-of-Version Manual Passthrough

After all phases of a version are complete, do a manual passthrough before tagging:

1. `openlearn` — cold start, check menu renders correctly
2. Create a new topic, start a course, answer questions (correct, wrong, partial)
3. `/review`, `/drill`, `/check`, `/videos`, `/status`
4. `openlearn resume` — verify resume header shows correct context
5. `openlearn stats` — verify charts render
6. Delete the topic and verify clean state

Note any rough edges. Fix blocking ones. Log cosmetic ones as backlog items in TODO.md.
Tag the version only after a clean passthrough.

---

## What to Skip

- Don't refactor working code just because you noticed something cleaner.
- Don't add error handling for cases that can't happen in practice.
- Don't write tests for code that was already covered by earlier tests.
- Don't block progress on style issues. Carry them forward or drop them.

---

## Prompt Quality Checklist

Before handing a phase prompt to a builder AI:
- [ ] Every function mentioned exists (verify line numbers)
- [ ] Every new function has a test case specified
- [ ] "Do not change X" is stated for anything the builder might accidentally touch
- [ ] The stopping condition is verifiable: "run `python -m unittest`, N tests pass"
- [ ] Carry-forward items from the previous phase are at the top, labeled clearly
