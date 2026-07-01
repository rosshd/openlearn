# openLearn Plan

## Positioning

openLearn is a local-first AI tutor for people who want durable learning, not answer delivery.
The product promise is simple: bring your own model key, keep your learning files, and let the tutor adapt from your actual answers.

## MVP Scope

- CLI-first course creation, tutoring, review, imports, drills, and progress.
- Markdown topic files with JSON metadata.
- Local learner state, event logs, and source summaries.
- OpenAI-compatible provider calls with configurable base URL and model.
- Mockable tests and smoke flows.

## User Workflow

```bash
openlearn new vim --goal "Use Vim comfortably for real editing"
openlearn resume
```

The tutor should then:

1. Resume from the active or recent topic.
2. Teach one compact step.
3. Ask for effortful recall or a hands-on check when useful.
4. Judge the answer.
5. Update local learner state.
6. Schedule review or advance only when evidence supports it.

## Fast Learn

Fast Learn is the next high-value workflow.
It turns one file, one folder, a coding repository, a study guide PDF, or a command list into an immediate learning session.

Target flow:

1. Choose `Fast Learn` from the main menu.
2. Pick a file or folder.
3. openLearn imports and summarizes sources.
4. For a new session, start teaching immediately with no course-outline confirmation.
5. For an existing or resumed source set, optionally run a placement check to decide where to restart.
6. Let the learner either follow the guided path or jump to the most relevant section.

Design requirements:

- Small files should start quickly.
- Large folders should scale depth without wasting turns on boilerplate.
- Coding repositories should teach architecture, entry points, workflows, and risky concepts before trivia.
- Test guides should extract topics, formulas, definitions, weak spots, and likely practice questions.
- Command lists should prioritize recall, usage context, and hands-on checks.
- The flow should create normal local topic files and context summaries so it remains inspectable.

## Product Constraints

- Local-first beats hosted convenience unless the user explicitly opts in.
- Storage remains inspectable and portable.
- Prompt changes need behavior tests or smoke evidence.
- Add dependencies only when they simplify real product behavior.
- Do not optimize for learner comfort at the expense of durable retrieval.

## Roadmap

| Area | Direction |
| --- | --- |
| Course start | Faster templates, clearer options, better imported-context use |
| Fast Learn | One-shot file or folder import that starts teaching immediately |
| Learner state | Stronger concept identity, event log, mastery evidence, rolling pass rate |
| Tutor quality | Calibrated judge, anti-gaming checks, explicit move policy |
| Practice | Cumulative retrieval, coding drills, video suggestions, due reviews |
| Providers | Keep OpenAI-compatible base; add broader provider ergonomics when needed |
| Interface | Preserve CLI speed; explore richer TUI after tutor quality is strong |
| Distribution | Package cleanly without compromising local-first defaults |

## Differentiators

- Own your learning data.
- Bring your own model.
- Transparent local memory.
- Tutor behavior optimized for retrieval, transfer, and mastery.
- No subscription required for the core local workflow.
