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
