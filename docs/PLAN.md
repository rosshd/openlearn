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

*Note: v0.6.0–v0.5.0 scope in PLAN.md was written before v0.5.0 shipped. Everything
through the original v0.7.0 (source import, provider controls) was completed in v0.5.0.
The roadmap below reflects the revised plan from v0.6.0 onward.*

---

### v0.6.0 Smart Feedback & Progress

Goal: make the tutor noticeably smarter and give learners visible evidence of progress.
See `docs/V0.6.0.md` for full phase breakdown.

- **Structured evaluation pipeline**: tutor returns a JSON verdict (`correct/partial/wrong`,
  score 0–1, explanation, gap, hint) instead of relying on substring matching. Wrong answers
  always produce an explanation and a guiding hint before the answer is revealed.
- **Progress tracking**: session stats (study time, streak, per-concept accuracy), streak
  counter in the status bar, `openlearn stats [topic]` with terminal charts via `plotext`.
- **Adaptive difficulty**: three tiers (struggling / on track / mastering) based on a
  rolling accuracy window. Struggling tier gets worked examples; mastering tier gets
  harder free-response and "why?" questions. Grounded in ZPD + expertise reversal research.
- **First-run wizard**: `openlearn init` walks through API key, base URL, model selection,
  and connection test. Finishes in under 60 seconds.
- **Course templates**: ~8 curated outlines shipped with openlearn (Python, Git, Linux CLI,
  Algorithms, SQL, HTTP, Networking, Vim). `openlearn new <topic> --template <name>` skips
  AI outline generation for common topics.
- **DeepEval conversation quality tests**: 6 AI-simulated student scenarios (stuck learner,
  overconfident learner, off-topic question, prerequisite gap, etc.) catch tutor regressions
  that smoke tests miss.

---

### v0.7.0 Multi-Provider & Distribution

Goal: work with any model — including fully local ones — and be easy to install.

- **LiteLLM integration**: replace the current OpenRouter-only call with a provider-agnostic
  layer. Users can point openlearn at Ollama (local, free, private), Anthropic native,
  Groq (fast + cheap), OpenAI, or any OpenAI-compatible API by changing one config value.
- **`openlearn init` provider presets**: during init, offer provider quick-picks
  (OpenRouter, Ollama, Anthropic, OpenAI) with model suggestions per provider.
- **Dry-run prompt preview**: `--dry-run` flag on any model-backed command prints the full
  system + user prompt without calling the API — essential for debugging and power users.
- **Export / portable course format**: `openlearn export <topic>` produces a self-contained
  `.zip` (Markdown + context files + metadata) that can be shared, version-controlled, or
  imported on another machine. `openlearn import-course <file>` is the inverse.
- **Homebrew tap**: `brew install rosshd/tap/openlearn` as the primary Mac install path.
  Removes the Python/pip requirement for non-developer users.
- **Cost estimation**: before large import/summarization calls, show estimated token count
  and approximate cost for the configured provider.

---

### v0.8.0 Interface & Ecosystem

Goal: make openlearn visually impressive and give the community a way to build on it.

- **Textual TUI mode** (`openlearn tui`): optional full-screen dashboard with course list,
  progress heatmap calendar, per-concept accuracy charts, and lesson view. Built on
  `textual` (MIT). The existing CLI remains the default; TUI is opt-in.
- **Community course library**: a public GitHub repo (`openlearn-courses`) with a simple
  JSON index. `openlearn courses` lists available community courses; `openlearn courses
  install algorithms` downloads and imports one. No server required — raw GitHub CDN.
- **Prompt style presets**: `openlearn style set <socratic|direct|coach>` configures the
  tutor's teaching style without editing config files. Each preset adjusts a small set of
  `TUTOR_FORMAT_RULES` toggles.
- **`openlearn repair` improvements**: detect and fix corrupt JSON frontmatter, merge
  duplicate session entries, and report what was changed.
- **Web companion** (stretch): `openlearn serve` starts a minimal local web server
  (FastAPI + htmx) for users who prefer a browser interface. Same local files, no cloud.

---

### v0.9.0 Stabilization & Polish

Goal: harden everything before 1.0. No new features — quality and distribution only.

- Full accessibility pass: non-color status markers, screen-reader-friendly prompts,
  keyboard-only navigation, clear contrast ratios in Rich output.
- Freeze the topic file format. Write a migration script for pre-v0.9.0 files.
- Error message quality: every user-facing error has a plain-English explanation and a
  suggested fix. No stack traces in normal operation.
- Expand pexpect smoke tests to cover the complete happy path end-to-end.
- Documentation site (MkDocs + Material theme): quick-start guide, command reference,
  topic file format spec, provider setup guides for OpenRouter/Ollama/Anthropic.
- PyPI package polish: README, classifiers, `openlearn[all]` extras meta-package.

---

### v1.0.0 Readiness Bar

The 1.0 release should feel like a small, dependable study tool that someone would
recommend to a friend without caveats.

- A learner can go from `brew install openlearn` to first lesson in under 2 minutes.
- The tutor adapts visibly to the learner's performance — easier when struggling, harder
  when mastering.
- Progress survives across sessions and is visible at a glance in `openlearn stats`.
- The tutor does not repeatedly ask for goals or recap empty context.
- Common actions are one or two keystrokes away in the menu or REPL.
- Topic files remain readable, editable, portable, and local by default.
- Fully local operation is possible via Ollama with zero cloud dependency.
- Tests cover storage, state transitions, model prompt contracts, menu paths,
  failure cases, and AI conversation quality.
- A 90-second demo recording shows the complete first-session flow without any rough edges.

---

### Marketing Differentiators (v1.0.0 pitch)

These are the things that make openlearn worth using over "just ask ChatGPT":

1. **Persistent learning state** — it knows where you are, what you've struggled with,
   and what's due for review. ChatGPT forgets everything between sessions.
2. **Adaptive tutor** — questions get harder as you improve, and easier when you're
   stuck. The difficulty adjusts automatically.
3. **Spaced repetition built in** — review scheduling based on your actual forgetting
   curve, not arbitrary intervals.
4. **Local-first, bring your own key** — your notes never leave your machine unless you
   want them to. Works fully offline with Ollama.
5. **Import your class materials** — paste a syllabus, import a PDF, or scan a folder.
   The tutor grounds the course in your actual material.
6. **Open source, no subscription** — AGPLv3. You own your data. No monthly fee for the
   core tool.

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
