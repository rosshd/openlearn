# openLearn Plan

## Positioning

openLearn is a local-first AI tutor for people who want durable learning, not answer delivery.
The product promise is simple: bring your own model access, keep your learning files, and let the tutor adapt from your actual answers.

## MVP Scope

- CLI-first course creation, tutoring, review, imports, drills, and progress.
- Markdown topic files with JSON metadata.
- Local learner state, event logs, and source summaries.
- OpenAI-compatible provider calls with configurable base URL and model.
- First-run onboarding that can configure OpenAI, Anthropic-compatible APIs, Ollama, or a custom OpenAI-compatible provider.
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

## Quick Learn

Quick Learn is the shortest path from source material to active tutoring.
It turns one file, one folder, a coding repository, a study guide PDF, or a command list into an immediate learning session.

Target flow:

1. Choose `Quick Learn` from the main menu.
2. Pick a file, bounded folder, or public GitHub repository.
3. openLearn imports and summarizes sources.
4. For a new session, start teaching immediately with no course-outline confirmation.
5. Run on the efficient mastery profile throughout, optimizing for coverage per minute over deep mastery.
6. Use fewer, denser slides and a course-wide coverage ledger so concepts are not re-taught across units.

Design requirements:

- Small files should start quickly.
- Large folders should scale depth without wasting turns on boilerplate.
- Coding repositories should teach architecture, entry points, workflows, and risky concepts before trivia.
- Test guides should extract topics, formulas, definitions, weak spots, and likely practice questions.
- Command lists should prioritize recall, usage context, and hands-on checks.
- The flow should create normal local topic files and context summaries so it remains inspectable.
- Quick Learn topics should remain visibly separate from normal courses.
- Repository ingestion must be bounded, exclude secrets and generated files, and never execute imported code.

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
| Quick Learn | File, folder, or public GitHub import that starts teaching immediately |
| Learner state | Stronger concept identity, event log, mastery evidence, rolling pass rate |
| Tutor quality | Calibrated judge, anti-gaming checks, explicit move policy |
| Practice | Cumulative retrieval, coding drills, video suggestions, due reviews |
| Providers | Keep OpenAI-compatible base; add broader provider ergonomics when needed |
| Interface | Preserve CLI speed; explore richer TUI after tutor quality is strong |
| Distribution | Package cleanly without compromising local-first defaults |

## Differentiators

- Own your learning data.
- Bring your own model, whether hosted with a key or local/custom and keyless.
- Transparent local memory.
- Tutor behavior optimized for retrieval, transfer, and mastery.
- No subscription required for the core local workflow.
