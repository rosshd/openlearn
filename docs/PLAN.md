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

## v0.2.0 Merge-to-Main Deliverables

Simple core mechanics:

- Default launch opens the menu and supports a complete happy path without memorizing commands.
- Menu learning actions automatically continue into the REPL when the tutor asks for a learner response.
- Active topic is reliable across `new`, `resume`, `next`, `review`, `chat`, `status`, `edit`, menu, and REPL flows.
- Topic files append readable session logs for prompts, model responses, and review sessions without corrupting metadata.
- `resume`, `next`, `review`, and REPL questions all use the same local topic context and terminal-friendly response style.
- Recent-topic listing and active-topic switching are fast enough to make topic hopping practical.
- Deleting a topic requires explicit confirmation and clears active-topic state when needed.
- Configuration for API key, model, and base URL works locally with environment-variable precedence.
- Tests cover the menu happy path, REPL command handling, topic persistence, active-topic fallback, deletion safety, config precedence, and model-response parsing.

## Near-Term Features

- `update` command that asks the model to suggest metadata changes after a session.
- Simple review scheduling based on weak spots and last review date.
- Import from text, Markdown, or pasted syllabus content.
- Cost estimate before model-backed commands.
- Provider abstraction for Anthropic, Gemini, OpenRouter, and Ollama.
- More tests for topic parsing, slugging, file updates, active-topic resolution, and model response parsing.

## Later Features

- Course template registry and search.
- User-uploaded materials under each topic's `sources/` folder.
- AI-generated course setup from syllabi, schedules, docs, and notes.
- Optional encrypted sync.
- Hosted usage-based credits.
- Opt-in anonymous aggregate learning insights.
- CLI/TUI plus desktop or web interface.

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
