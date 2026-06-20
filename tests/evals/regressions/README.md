# Tutor Regression Files

Each JSON file in this directory captures one observed tutor or REPL behavior
that should not regress.

Required fields:

- `name`: short slug used in test output.
- `turns`: ordered conversation turns.
- `requirements`: plain-language assertions checked against the captured output.

Optional fields:

- `description`: one sentence explaining the failure mode.
- `topic`: topic slug to create for the regression. Defaults to `name`.
- `goal`: topic goal used when creating the topic.
- `state_assertions`: exact metadata checks after replay.

Turn format:

```json
{"role": "user", "content": "student message"}
{"role": "assistant", "content": null}
```

The runner stores the most recent user message. When it sees an assistant turn
with `content: null`, it sends that message to OpenLearn. Messages beginning
with `/` are routed through the REPL command handler; other messages call
`ask_topic()`.

Requirement format:

- `tutor response must contain X`
- `tutor response must not contain Y`

Matching is case-insensitive. To add a new regression, copy an existing JSON
file, paste the shortest observed failing interaction, and add the smallest
requirements that catch the bad behavior without depending on unrelated prose.
