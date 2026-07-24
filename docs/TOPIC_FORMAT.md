# Shareable Topic Format

An openLearn topic is a UTF-8 Markdown file with a JSON metadata object between two `---` delimiter lines.
The file can be copied, versioned, and edited with ordinary text tools.
Place it at `learning-topics/<slug>.md`, where `<slug>` is the stable lowercase, hyphenated identifier for the topic.

## Minimal Example

```md
---
{
  "topic": "SQL Fundamentals",
  "slug": "sql-fundamentals",
  "goal": "Write clear queries against relational databases"
}
---

# SQL Fundamentals

## Current Goal

Write clear queries against relational databases.

## Notes

- Add notes, links, questions, or source summaries here.

## Session Log
```

The opening delimiter must be the first line of the file.
The metadata must be one JSON object, and the closing delimiter must appear on its own line.
Everything after the closing delimiter is ordinary Markdown owned by the learner.

## Metadata

`topic` is the human-readable title.
`slug` should match the filename without the `.md` suffix.
`goal` describes the learner's intended outcome.
openLearn fills its other supported fields with defaults when the topic is read or repaired, so a shared topic does not need to include every internal field.

Custom JSON fields are allowed and are preserved when openLearn rewrites a valid topic.
Use namespaced keys such as `example_org_reading_list` to reduce the chance of colliding with future openLearn fields.
JSON values must use JSON syntax, including double-quoted strings and lowercase `true`, `false`, and `null`.

## Sharing

Share the Markdown topic file when you want to exchange a course scaffold, notes, or a reusable learning plan.
Review it before sharing because the Markdown body and metadata can contain private notes or source material.

The adjacent files `<slug>.state.json` and `<slug>.events.jsonl` contain machine-managed learner state and learning history.
They are not required to open the topic and should usually remain private.
Imported context under `learning-topics/context/<slug>/` and generated drills under `learning-topics/drills/<slug>/` are also separate from the shareable topic file.

After copying a shared file into `learning-topics/`, run `openlearn repair <slug>` if it came from an older openLearn version or contains only minimal metadata.
The repair command creates `<slug>.md.bak` before rewriting the topic.

## Course Templates

Bundled course templates are curated JSON package assets used to seed a new topic.
List them with `openlearn templates`, then create a topic with a command such as:

```bash
openlearn new interview-prep --template algorithms
```

The template supplies a default goal and suggested unit outline.
An explicit `--goal` still takes precedence.
The package template remains unchanged, and the new topic receives its own copy of the unit list.
