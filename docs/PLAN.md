# openLearn Plan

## Product Positioning

openLearn is an open-source, local-first AI learning workspace that turns any supported LLM into a long-term adaptive tutor.

It is not just another AI chat app. The core value is persistent learning state: what the user is learning, where they are in the curriculum, what they know, what they keep forgetting, and what needs review.

## Trust Promise

Your learning data stays local by default. Bring your own model key, or optionally use hosted credits with transparent usage-based pricing. Sync and hosted services should be convenience features, not requirements.

## License Direction

The open core is AGPLv3-or-later. This keeps the project open and makes it harder for a closed SaaS clone to privatize improvements.

AGPL does not forbid commercial hosting, but it requires modified network-hosted versions to provide corresponding source code to users.

## MVP Scope

- Local `learning-topics/` folder.
- One Markdown file per topic with JSON metadata frontmatter.
- Bring-your-own OpenAI API key.
- Local `config.json` for saved API key, default model, and OpenAI-compatible base URL.
- GPT-backed `chat` and `review` commands.
- Append session logs back into the topic file.
- Active-topic tracking for one-command resume.
- Keep full account systems, hosted credits, sync, and course search out of the first version.

## Core User Workflow

The product should optimize for ultra-fast reentry.

First-time setup:

```bash
openlearn init
openlearn new vim --goal "Use Vim comfortably for real editing"
```

Daily use:

```bash
openlearn resume
```

The app should know the active or most recent topic, summarize where the learner left off, give the best next action, and ask one active-recall question.

Switching topics should be explicit but fast:

```bash
openlearn recent
openlearn active operating-systems
openlearn resume
```

Manual editing should remain first-class:

```bash
openlearn edit
```

This opens the active topic file in `$EDITOR`, preserving the local-first workflow.

## Efficient Course Start Flow

The tutor should not repeatedly recap an empty topic. New topics should move from course name to structured learning with as few turns as possible.

Target flow:

- Create a topic with a name and goal.
- Later, support optional file upload or source import before the course starts.
- If the active topic has not started, the menu shows `Start course` instead of `Resume`, `Next step`, `Ask`, `Review`, or `Status`.
- `Start course` asks the model for a compact scope: what the course covers, what it excludes, assumptions, and planned units.
- The user accepts or rejects the scope with a simple yes/no confirmation.
- Once confirmed, openLearn saves the course plan into the topic file, marks the course started, teaches the first lesson, and enters the REPL for the learner's answer.
- After a course is started, `Resume`, `Next step`, `Ask`, `Review`, and `Status` become available again and should use the saved course plan to avoid restarting from generic goals.

Efficient implementation notes:

- Store `course_started` and course-plan text in the topic file so this is durable and local-first.
- Keep prompts strict: no generic recap before a course plan exists; no repeated “what is your goal?” once the goal is known.
- Prefer one model call for scope and one model call for lesson one. Avoid a loop unless the user rejects the scope.
- Future file upload should feed source summaries into the same scope-generation prompt rather than becoming a separate course-start path.

Ideas to get from course name to learning faster:

- Infer a default college-course scope from the topic name, but require one confirmation before teaching.
- Offer optional setup presets later if the goal is too broad, but keep v0.2.0 goal-only.
- Save the accepted outline and current unit so `learn AI`-style responses advance the course instead of triggering fresh recaps.
- Later, add quick presets such as `college intro`, `exam prep`, `project based`, and `crash course` to reduce free-form setup.

## Product Constraints Through 1.0

- Keep the core lightweight. Add durable primitives that users can build on, not heavy workflows they must remove.
- Prefer local files, plain Markdown, and small JSON metadata over databases or hidden state.
- Keep important actions one or two keystrokes away in the menu.
- Keep command names short and memorable; avoid making users type long commands for daily study.
- Make advanced features optional and quiet by default.
- Avoid modal complexity. If a flow needs many choices, collapse it into a simple menu or yes/no confirmation first.
- Preserve manual editability. A user should be able to understand and repair topic files by opening them in an editor.
- Treat accessibility as a core requirement: readable text, keyboard-first navigation, no color-only meaning, and clear prompts.
- Prefer fewer, composable actions: start, continue, answer, quiz, review, switch, edit, delete.
- Every release should reduce repetitive tutor behavior and improve reentry speed.

## Release Roadmap To 1.0

### v0.2.0 Structured Course Start

Simple core mechanics:

- Default launch opens the menu and supports a complete happy path without memorizing commands.
- New topics start as unstarted courses with a name and goal.
- `Start course` generates a compact outline before any repetitive recap loop can happen.
- Course start asks for simple outline acceptance, then asks what should change when rejected.
- `course_started` changes only after the user accepts an outline.
- Accepted outline and first lesson are saved into the topic file.
- Menu learning actions continue into the REPL when the tutor asks for a learner response.
- Switch and delete use numbered topic lists instead of slug entry.
- Delete uses a simple irreversible y/n confirmation.
- Active topic is reliable across `new`, `resume`, `next`, `review`, `chat`, `status`, `edit`, menu, and REPL flows.
- Tests cover menu happy paths, course start, topic persistence, active-topic fallback, deletion safety, config precedence, and model-response parsing.

### v0.3.0 Durable Course State

Goal: make the accepted outline usable by the program, not just readable by the model.

- Parse the accepted outline into lightweight metadata: `course_units`, `current_unit`, `current_step`, and `completed_steps`.
- Keep the Markdown outline readable and editable.
- Add a short `where am I?` status summary that shows current unit, next action, and due review count.
- Make `continue` and menu option `1` advance from structured state instead of asking the model to infer progress from logs.
- Add a tiny state-repair fallback when metadata and Markdown disagree.
- Keep schema optional and tolerant so older topic files still work.
- Add tests for outline parsing, state persistence, current-unit advancement, and fallback behavior.
- UX additions: show progress as `Unit 1/6` in status/menu, keep current action one key away, and avoid verbose state dumps.

### v0.4.0 Answer Evaluation Loop

Goal: turn the REPL into a real study loop instead of generic chat.

- Track when the tutor is waiting for an answer to a lesson question.
- Evaluate learner answers as correct, partially correct, or needs work.
- Give concise feedback, one correction, and either advance or reinforce.
- Add first-class quick actions: `c` continue, `qz` quiz, `r` review, `h` hint, `s` status, `x` exit.
- Keep slash commands as aliases but make short keys visible in REPL help.
- Save answer attempts in readable session logs.
- Add metadata updates for `known` and `weak_spots` based on evaluations.
- Add tests for answer evaluation prompts, short-key routing, and metadata updates.
- UX additions: clear prompt labels like `Answer>`, `Tutor>`, and `Next>`; no long command discovery required.

### v0.5.0 Review Scheduling

Goal: make forgetting and review first-class without building a heavy spaced-repetition system.

- Add lightweight review items with concept, due date, difficulty, and source unit.
- Generate review items from weak answers and completed lessons.
- Add menu option or short key for due review within one keystroke.
- Keep scheduling simple: later today, tomorrow, three days, one week.
- Let users mark review results quickly: easy, hard, missed.
- Update `last_reviewed`, `review_due`, and weak spots after review.
- Keep all review data inside the topic file or a small adjacent local file only if needed.
- Add tests for review item creation, due filtering, answer result updates, and no-review states.
- UX additions: show `Reviews due: 3` in menu/status and make the next due review one key away.

### v0.6.0 Source Import And Course Grounding

Goal: support real class material while staying local-first and optional.

- Add import for pasted text and Markdown files.
- Add optional `sources/` folder per topic without requiring a new project layout.
- Summarize imported sources into concise local notes before course planning.
- Let `Start course` use source summaries when present.
- Keep file upload/import optional and never required for a course.
- Add source list and remove-source actions through numbered menus.
- Avoid PDF complexity unless a small, reliable dependency is acceptable; otherwise document Markdown/text first.
- Add tests for text import, source summary storage, source-grounded outline prompts, and source deletion.
- UX additions: `i` import, `ls` list sources, source selection by number, clear privacy messaging before model-backed summarization.

### v0.7.0 Provider And Cost Controls

Goal: make model use predictable and provider-neutral without bloating the core.

- Add a small provider abstraction for OpenAI-compatible APIs first.
- Keep OpenAI/OpenRouter-style config lightweight: base URL, model, API key.
- Add per-command model override only where useful, not everywhere.
- Show estimated request size or simple cost warning before large imports/summarizations.
- Add `config` menu shortcuts for common setup and status.
- Keep secrets local and masked in all output.
- Add dry-run prompt preview for debugging and power users.
- Add tests for provider selection, config precedence, masked secrets, and prompt preview.
- UX additions: one-screen config status, clear missing-key messages, and no stack traces for provider failures.

### v0.8.0 Extensibility Without Weight

Goal: let users add behavior without making the default app complicated.

- Define a tiny course template format using Markdown plus JSON metadata.
- Support local template discovery from a user folder.
- Add optional prompt snippets for teaching style, pacing, or quiz style.
- Add export/import of a topic as portable Markdown plus sources.
- Add hooks only where stable: before course start, after lesson, after review.
- Keep plugins/config opt-in and file-based; no plugin marketplace in core.
- Add tests for template loading, invalid template handling, export/import round trips, and prompt snippet inclusion.
- UX additions: `templates` list by number, `style` selection by number, and safe defaults when templates fail.

### v0.9.0 1.0 Stabilization

Goal: harden the core study loop and remove rough edges before 1.0.

- Audit all menu paths for one or two keystroke access to common actions.
- Improve error messages for missing topics, missing API key, malformed topic files, and failed model calls.
- Add topic backup before destructive metadata rewrites.
- Add migration checks for older topic files.
- Add accessibility pass for prompt wording, screen-reader friendliness, non-color status markers, and keyboard-only operation.
- Add command aliases only when they reduce friction without increasing confusion.
- Expand smoke tests for the full path: create topic, start course, answer, continue, review, switch, import, delete.
- Freeze the topic file format expected for 1.0.
- Document the minimal core and optional extension points clearly.
- UX additions: concise first-run help, `?` help available everywhere, and a no-surprises delete/cancel pattern.

### 1.0 Readiness Bar

The 1.0 release should feel like a small, dependable study tool, not a general AI chat wrapper.

- A learner can create a topic, accept a course plan, study lesson by lesson, answer questions, get feedback, and review weak spots.
- Progress survives across sessions and is visible at a glance.
- The tutor does not repeatedly ask for goals or recap empty context.
- Common actions are one or two keystrokes away in the menu or REPL.
- Topic files remain readable, editable, portable, and local by default.
- Optional imports, templates, providers, and hooks do not complicate the default workflow.
- Tests cover storage, state transitions, model prompt contracts, menu paths, and failure cases.

## Learning From User Experience

This should be opt-in only. The default should never upload raw conversations, API keys, notes, class materials, or private source documents.

Potential opt-in aggregate data:

```json
{
  "topic": "vim",
  "concept": "macros",
  "difficulty_rating": 4,
  "attempts_to_master": 3,
  "helpful_exercise_type": "guided drill",
  "prerequisite_gap": "registers"
}
```

This could later improve course recommendations while preserving the local-first trust model.

## Suggested File Model

Current MVP:

```text
learning-topics/
  vim.md
  operating-systems.md
```

Possible future structure for richer topics:

```text
learning-topics/
  operating-systems/
    topic.md
    state.json
    reviews.json
    sessions.md
    sources/
      syllabus.pdf
      lecture-03.md
```

## Monetization Direction

Free/open-source core:

- Local topic files.
- BYO API key.
- Basic model provider integrations.
- Course template format.
- Review engine.
- Import/export.

Optional paid services:

- Encrypted sync.
- Hosted credits.
- Mobile/web companion.
- Backups.
- Private shared course spaces.
