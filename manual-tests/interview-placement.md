# Interview-prep learner journey

This workflow exercises the public CLI from the premade Technical Interview Prep course through the first lesson.
Use an isolated home so the replay cannot read or modify personal learner state or provider configuration.

```bash
export OPENLEARN_HOME="$(mktemp -d)"
unset OPENAI_API_KEY OPENLEARN_BASE_URL OPENLEARN_MOCK
openlearn
```

Complete provider onboarding if it appears, or open the menu with an already configured provider.
Press `s` for Starter courses and select `Technical Interview Prep`.
The menu description must identify the LeetCode-style algorithms and data-structures course.
The entry screen must explain that placement is a short offline reasoning conversation and must offer start, defer, or back before creating the course.

Choose start.
Course creation must use the premade goal and safe interview-profile defaults without asking for a course name, goal, target level, schedule, editor, language, or container runtime.
Placement must say that it takes about five minutes and that there is no coding task or editor.

At `clarification>`, enter one question per line:

```text
clarification> Can width exceed the text length?
clarification> Should I return the zero-based start index?
clarification> /show
clarification> /done
```

Each answer line must be saved without advancing the stage.
`/show` must display the complete saved draft.
`/done` is the only normal command that submits the section and advances.
A blank line must leave the learner at the same prompt.

At `reasoning>`, dictate or paste the solution route across several lines:

```text
reasoning> I would use a sliding window and a set of seen characters.
reasoning> I would test width one, repeated characters, and no valid window.
reasoning> The scan is O(n) time and O(width) space.
reasoning> /done
```

The response must stay in the reasoning section until `/done`.
The completed result must show a course-start passport with a starting route, first activity, reasoning signals, practice priority, and later verification target.
It must state that coding fluency was not observed and must not grant mastery or claim interview readiness.

To verify recovery, repeat the flow and use `/stop` after saving at least one line in clarification.
Exit openLearn, then run:

```bash
openlearn resume technical-interview-prep
```

The resumed placement must return to clarification, report the saved draft line count, and preserve `/show` output.
Submit clarification, add at least one reasoning line, and use `/stop` again.
The next process must return to reasoning with that draft intact and without duplicating the completed clarification evidence.
EOF or an interrupted terminal should preserve the same state.

Initial placement must never open an editor, create a drill workspace, execute code, or require Docker or Podman.
Existing coding-placement v1 and v2 records remain readable and continue under their recorded lifecycle rather than being reinterpreted as reasoning evidence.

After placement, temporarily remove the provider configuration and run resume.
The command must show `Placement: provisional (2/2)`, confirm that all work is saved, and print both `openlearn config set-key` and the exact resume command.
Provider setup is required for course planning and teaching, not for placement.

Restore the provider or enable deterministic mock teaching:

```bash
export OPENLEARN_MOCK=1
openlearn resume technical-interview-prep
```

The command must go directly to `Course outline` without offering the ordinary optional placement quiz.
Accept the outline and confirm that `First lesson` plus visible lesson content is rendered.

Finally, inspect the durable result:

```bash
openlearn interview placement technical-interview-prep status
```

Expected status includes `Placement: provisional` and `evidence 2/2`.
The adjacent profile must report the coding-fluency gap as `uncertain`, require a later unaided implementation and test in the passport verification target, and set `mastery_update_applied` to false.

Real coding belongs to later course practice.
Use a course coding drill when the learner is ready to implement, test, and revise a solution in the configured editor.
Secure `/check` execution still requires the locally available pinned runner image plus Docker or Podman, and a controlled-editor mock-interview mode remains a later interview experience rather than an onboarding dependency.

As an isolation check, create the ordinary algorithms starter with `openlearn new ordinary-algorithms --template algorithms`.
It must not create an adjacent interview profile or show reasoning-placement prompts.
