---
name: openlearn-phase-review
description: >
  Use when reviewing a phase implementation, deciding whether to block or carry
  work forward, or writing the next self-contained phase prompt.
---

# openlearn phase review

## Review Loop

1. Read the phase brief and required deliverables.
2. Inspect `git diff --stat` for scope.
3. Read every changed line relevant to the phase.
4. Run the required gate, usually `make check`.
5. Verify each deliverable directly.
6. Classify findings as blocking, carry-forward, minor, or clean.
7. Write the next phase prompt with carry-forward items first.

## Block Now

- Test, lint, or smoke failures.
- Data corruption or unsafe storage migration.
- Prompt behavior that breaks the next phase.
- Missing deliverable required by the next phase.
- Any change that touches user-owned files outside an isolated test home.

## Carry Forward

- Additive improvements where tests pass and behavior is not broken.
- Missing coverage that does not hide a known bug.
- Low-risk polish or ergonomics that does not block the next phase.

## Phase Prompt Requirements

- Include exact paths, functions, and contracts.
- State what to add and what not to touch.
- Specify tests to add or update.
- Define a verifiable stopping condition.
- Put carry-forward work at the top under a clear heading.

## Manual Passthrough

Use near version boundaries, not after every small change:

```bash
OPENLEARN_MOCK=1 OPENLEARN_HOME="$(mktemp -d)" ./manual-tests/smoke-full.sh --mock
```

Check cold start, topic creation, course start, answer feedback, `/review`, `/drill`, `/check`, `/videos`, `/status`, `resume`, `stats`, and deletion.
