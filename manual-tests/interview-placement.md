# Interview-prep learner journey

This workflow exercises the normal public CLI from profile setup through an observable first lesson.
Use an isolated home so the replay cannot read or modify personal learner state or provider configuration.

```bash
export OPENLEARN_HOME="$(mktemp -d)"
unset OPENAI_API_KEY OPENLEARN_BASE_URL OPENLEARN_MOCK
openlearn config set-editor nvim
openlearn doctor
openlearn new "Leetcode Sweep" \
  --goal "Build consistent coding interview practice" \
  --interview-prep
```

The saved editor takes precedence over `EDITOR`, then `VISUAL`; openLearn falls back to `nvim`.
Before the normal coding path, `openlearn doctor` must report a ready Docker or Podman runtime and the already-present pinned runner image.
The image is never pulled automatically, so follow the explicit setup command from `doctor` if it is missing.

Accept the five profile defaults for role family, target level, optional interview date, weekly practice minutes, and session minutes.
The default schedule is exactly 120 minutes per week as three sessions of up to 40 minutes.
Confirm creation and start the offline placement.
At the first prompts, enter representative learner responses:

```text
calibration> I completed a data structures and algorithms course and have practiced LeetCode on and off.
clarification> Is the input a Python string, what should I return, and can I see example inputs and outputs?
plan> I would use a sliding window with a hashmap to count the characters in the current window.
implementation>
```

Press Enter at `implementation>` to open the persistent learner-owned workspace in the configured editor.
The file must contain the problem, examples, and inert function stub.
Implement the function, save the file, and close the editor.
openLearn must then run the hidden test cases through the secure runner and save actual passed or failed execution evidence for both implementation and testing.
The workspace remains under `learning-topics/drills/leetcode-sweep/` after placement.

After the runner returns an observed attempt, answer the complexity and follow-up prompts.
Placement must finish provisional with seven evidence references and no mastery update.

If the secure runner is unavailable, openLearn must preserve the workspace and keep placement at implementation.
It must print the workspace path, `openlearn doctor`, and the exact placement resume command rather than treating infrastructure failure as an incorrect answer.

To exercise the intentional skip path, enter `/skip` at `implementation>`.
Placement must not ask for tests, complexity, or follow-up after that skip.
It records all four dependent stages as uncertain, still finishes provisional with seven evidence references, grants no mastery, and points back to the main menu for course planning.

With the remote provider still unconfigured, run:

```bash
openlearn resume leetcode-sweep
```

The command must show `Placement: provisional (7/7)`, omit `Where you left off` and `No previous session yet`, confirm that all work is saved, and give `openlearn config set-key` plus the resume command.

Enable deterministic mock teaching and resume again:

```bash
export OPENLEARN_MOCK=1
openlearn resume leetcode-sweep
```

The command must go directly to `Course outline` without asking for the legacy optional placement quiz.
Accept the outline and confirm that `First lesson` plus visible lesson content is rendered.

Finally, verify durable placement state:

```bash
openlearn interview placement leetcode-sweep status
```

Expected status includes `Placement: provisional` and `evidence 7/7`.
