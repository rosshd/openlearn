# Architecture

This is the human-readable architecture summary.
Agents should use `.claude/skills/openlearn-architecture/` for operational rules.

## Current Shape

openLearn is a Python CLI with one package, `openlearn`.
`src/openlearn/cli.py` still owns most behavior: commands, REPL, menu flow, topic storage orchestration, prompt construction, imports, and provider calls.
First-run provider setup lives in `src/openlearn/onboarding.py` and is invoked only by bare `openlearn` when provider configuration is not yet usable.

Supporting modules:

- `constants.py`: prompt constants, defaults, limits, profile values, and option labels.
- `models.py`: dataclasses for topic and pending-context state.
- `onboarding.py`: provider presets, credential validation, first-run configuration persistence, and initial destination launch.
- `stats.py`: read-only aggregation helpers for the stats dashboard.
- `text.py`: parsing, trimming, metadata-update helpers, answer-key extraction, and context compaction.
- `ui.py`: terminal formatting and Rich output helpers.

Split only when it pays for itself.
Likely split points are provider calls, topic storage, import handling, and tutor policy.

## Storage

Topic files are user-owned Markdown with JSON metadata between `---` separators.
JSON avoids a YAML dependency and keeps the file editable.
The user-facing format and sharing boundary are documented in [TOPIC_FORMAT.md](TOPIC_FORMAT.md).
`repair` normalizes missing metadata defaults and can recover simple corrupt JSON frontmatter such as trailing commas or missing closing braces/brackets.
When it rewrites a topic, it first writes the original text to `<slug>.md.bak`.

```md
---
{"topic": "Vim", "known": [], "weak_spots": []}
---

# Vim
```

The slug is the stable file identifier at `learning-topics/<slug>.md`.
Runtime state can also live in `<slug>.state.json`, `<slug>.events.jsonl`, `state.json`, imported context directories, and drill directories.
Event logs are append-only.
The stats dashboard reads event logs to derive activity dates, streaks, session spans, and current-week study minutes.
Writes use per-topic lock files with `fcntl.flock` on POSIX and `msvcrt.locking` on Windows.

Important dynamic metadata includes pending questions, an in-flight learner prompt, answer status, concept attempts, rolling pass rate, quiz state, active drill path, imported checksums, learner preferences, structured course completion, and per-slide concept coverage.
The in-flight prompt is stored only in the selected topic's state file immediately before REPL provider dispatch and is removed after the complete tutor response is appended.
Recovery is at least once: a process exit between transcript append and state cleanup can offer the prompt again.
Pending questions may be multiple choice with an answer key, multiple choice without a stored key, or free response.
Learner preferences capture explicit navigation choices such as skipped material and should constrain future tutor turns.
Quick Learn topics also store `learning_mode`, `quick_source_type`, `quick_source_label`, and `coverage_contract` so they can remain visibly separate and enforce source-grounded concept coverage.

Interview prep is explicitly opt-in and stores its learner-owned profile in `<slug>.interview.json`, separate from shareable topic metadata, general learner preferences, and concept mastery.
The file contains a versioned profile revision, resumable placement status, opaque activity and evidence references, derived tri-state observations, a rubric version, a provisional gap assessment, and target-horizon-aware recommendations.
Raw calibration, code, tests, and reasoning pass through the validated coding activity adapter and remain in append-only namespaced activity evidence events rather than being copied into topic metadata or the interview profile.
Coding placement rubric v1 declares Python as its only structurally validated implementation language; other preferred-language implementations remain uncertain rather than being treated as failed evidence.
Interrupted activity transactions recover through the activity journal, and placement resume idempotently projects any recovered evidence into the profile before asking for the next stage.
Profile publication takes the topic identity lock before the profile lock, so concurrent topic deletion cannot recreate an orphan profile.
Effective profile edits are validated before mutation and publish a topic-generation-aware edit journal while holding the topic identity lock, allowing an interrupted activity abandonment and profile reset to finish on the next profile read.
Recovery verifies that same generation again under the topic lock immediately before profile publication, so a stale edit cannot mutate a deleted and recreated topic with the same slug.
Profile edits invalidate affected recommendations and mark completed placement stale without deleting attempt events.
Topics without the adjacent file behave exactly as ordinary topics and receive no interview prompts.

## Interview Skill Graph

`openlearn.interview_skills` owns the versioned static interview-readiness model.
The bundled `coding-interview-v1.json` graph is canonical, while the algorithms course template is only a presentation seed.
The graph uses stable category-prefixed IDs for concepts, patterns, process skills, and communication skills.
It declares blocking and supporting prerequisite edges, versioned evidence policies, transfer expectations, and primary or supporting problem references.
Validation rejects unknown references, duplicate identities, invalid problem roles, and cycles before the graph can be used.

Learner-specific evidence remains outside the graph in caller-owned append-only event history.
Each evidence record carries the graph and mastery-policy versions under which it was observed.
An immutable registry resolves that exact graph-and-policy bundle before deciding whether the observation qualified at the time.
Problem roles, explicit-check rules, and canonical transfer families therefore come from the historical bundle rather than the current graph or caller input.
Current assessment may explicitly apply current mastery minimums to already-qualified older evidence without rewriting the original record.
Evidence for a retired stable ID remains inspectable as orphaned history instead of being deleted or silently mapped to another skill.
Ordinary topics do not load the graph, receive graph metadata, or acquire interview-only learner state.

The deterministic assessment surface separates readiness from selection.
Readiness is `ready`, `provisional`, `weak`, or `unassessed`.
Selection is `ready`, `blocked`, `weak`, `due`, or `unassessed`, with learner-visible reasons for missing evidence, prerequisite blocks, hint-dependent work, and delayed-retrieval failures.
Evidence provenance uses closed assistance and completion values, and independent mastery rejects copied structure, partial code, worked examples, editorials, incomplete attempts, and prompted production.
Delayed retrieval uses the source bundle's skill policy and a qualifying prior observation from that same bundle before current aggregate mastery minimums are applied.
Passing counts and latest-failure due status use that same source-qualified delayed-observation collection, including independent, unassisted, novel, and complete provenance.
Transfer breadth is derived from stable problem IDs and canonical source-graph families, so repeated attempts, family renames, or caller-supplied labels cannot manufacture novelty.
Distinct problems in one canonical family still count as one transfer context.
Repeated append-only delivery of an identical evidence ID is idempotent, while conflicting records with the same ID fail validation.
Blocking prerequisites propagate through the graph and require each prerequisite to be selection-ready.
Blocking is a selection constraint rather than an instruction to drill indefinitely.

## Practice Activities

`openlearn.activities` defines the versioned, domain-neutral contract for hands-on practice.
An activity has a stable ID, objective, concept IDs, domain and kind, requested evidence, scaffolding level, purpose, lifecycle status, resource provenance, an adapter-owned payload, and opaque evidence references.
The purpose is `practice`, `mastery_check`, or `placement`; completing any purpose does not itself change mastery.

The lifecycle is `proposed` to `accepted` to `active`, followed by `completed`, `abandoned`, `cancelled`, or `failed`.
Transitions are validated and repeated transitions to the current state are idempotent.
Each changed state is persisted in the topic state file and projected into the append-only topic event log.
Evidence details live only in namespaced evidence events, while activity state stores opaque evidence IDs.
Resource source and license provenance are stored separately from learner evidence.
Activity mutations hold the topic-state lock for reload, activity-ID and revision comparison, validation, and write.
A durable per-topic activity-update journal records the complete next state and its event before either is published.
Reads and later mutations recover an interrupted journal idempotently, and the journal update ID deduplicates an event that was written before a crash.
This invariant prevents a recovered activity revision or evidence reference from existing without its corresponding lifecycle or evidence event.

The built-in adapter registry is explicit and does not dynamically import packages.
Adapters own their activity kinds, payload validation, evidence kinds, and narrow semantic tool actions.
The generic contract rejects unknown domains, unknown kinds, oversized payloads, arbitrary tool actions, and malformed JSON values before a tool can run.
Tool execution remains application code and can happen only after the activity reaches `accepted`.
Denied or cancelled proposals therefore have no workspace, launcher, or execution side effects.

The first adapter is `coding.python_drill`.
It allows only creating the owned drill workspace, opening the configured editor, and running the generated drill tests.
Drill paths are checked against `learning-topics/drills/<slug>/` before execution.
Model-generated function stubs are parsed as untrusted Python before writing and may contain only one undecorated inert function with a safe signature and a `pass` or `NotImplementedError` body.
The existing `/drill` command is explicit learner consent and remains the reliable manual fallback.
Future tutor-selected proposals must show the proposal and obtain an explicit learner acceptance before dispatch.
Instrument and electronics shapes are contract fixtures only; their tools are not implemented.
After the learner edits a drill, `/check` fails closed unless Docker or Podman and the digest-pinned Python runner image are already available.
The runtime image is never acquired implicitly.
Secure checks keep hidden expectations and the final completion decision in the trusted openLearn process.
Each check creates one bounded worker, imports the learner module once, then supplies only the current call's input over a monitored framed channel.
This preserves sequential function state without exposing future inputs.
Returned values use a bounded tagged non-executable encoding that preserves comparison-relevant Python container and dictionary-key types.
The child can inspect its input and worker protocol, but it never receives test expectations or a final success credential.
openLearn rejects missing, malformed, duplicated, deeply nested, oversized, or output-contaminated frames and compares the returned value with the hidden expectation itself.
Raw protocol output, learner stderr, and rendered feedback share one aggregate output budget.
OCI calls mount only the learner solution directory plus a separate read-only generic call worker.
The container receives no host credential directory, home directory, repository, or runtime socket mount.
It runs with no network, a read-only root, a non-root UID, dropped capabilities, no-new-privileges, bounded CPU, memory, process count, output, file size, and wall time, and a small writable tmpfs.
Timeout and cancellation force-remove the complete container.
The shared result distinguishes successful tests, test failures, compile errors, runtime errors, resource limits, cancellation, and runner infrastructure failures.
Infrastructure failures record no incorrect-answer evidence and leave the activity available for retry.

Test cases remain in validated activity state outside the learner-editable drill workspace.
Learner code may submit any return value, as any implementation can, but cannot inspect or emit the trusted pass/fail decision.

`/check --reduced-isolation` is a per-command explicit fallback.
It uses the same host-owned per-call protocol with a scrubbed subprocess environment plus best-effort resource and process-tree controls, prints a residual-risk warning, and is never described as a sandbox.
It does not prevent filesystem or network access.
Docker and Podman may enforce the Linux container contract through their normal VM on macOS and Windows.
`openlearn doctor` reports missing runtimes, unavailable services, unpinned image configuration, and an absent local image without installing or pulling anything.
`make oci-live` is the opt-in pre-provisioned security-fixture lane.
It never acquires an image and exercises every ready Docker and Podman runtime against environment, mount, network, process, resource, timeout, cancellation, and cleanup boundaries.

## Model Calls

Model-backed commands send only selected-topic context:

- Topic metadata and relevant learner state.
- Bounded notes and recent session history.
- Imported context summaries when relevant.
- The current learner prompt or generated instruction.

Configuration precedence is environment variables, then `config.json`, then defaults.
Provider calls target OpenAI-compatible chat completions at `{base_url}/chat/completions`.
Transient provider failures, including HTTP 429, HTTP 5xx, URL errors, and timeouts, are retried up to three attempts with bounded exponential backoff and jitter.
Non-local provider base URLs require an API key, while localhost OpenAI-compatible endpoints may be keyless.
When no key is configured for a keyless endpoint, requests omit the `Authorization` header; a 401 response is reported as an API-key-required endpoint.
Bare `openlearn` skips first-run onboarding for saved keys, environment keys, `OPENLEARN_MOCK=1`, or keyless localhost providers with a configured model.
For `chat`, `resume`, `next`, and `review`, `--dry-run` prints the rendered system and user messages instead of calling the provider or mutating local files.
Learner-metadata extraction can use `OPENLEARN_EXTRACTOR_MODEL` or `extractor_model`; otherwise it uses the tutor model.
Extractor calls send a reduced metadata snapshot limited to pending checks, focus, known concepts, weak spots, and review due items.

## Source Ingestion

Normal imports save source summaries and deduplicate by checksum.
Quick Learn accepts one file, one folder, or a public GitHub repository URL, then creates a new topic, writes selected source context, summarizes it, generates a source-grounded course plan, and starts the first lesson without placement or outline approval.
Folder and repository ingestion is bounded to 32 supported files, 200 KB per file, 240,000 selected characters, and a 60,000-character bundle for summary grounding.
The selector prefers README files, package manifests, docs, then non-test source files, and skips hidden/generated directories, secret-like filenames, symlinks, binary files, and unsupported suffixes.
Public GitHub repositories are shallow-cloned with terminal prompts, system config, global config, and hooks disabled, and imported code is never executed.

## First Run

Bare `openlearn` starts onboarding when no saved key, environment key, or fully configured keyless localhost provider is available.
`OPENLEARN_MOCK=1` and already usable environment configuration skip onboarding.
Onboarding validates credentials with `{base_url}/models`, persists settings through the existing config commands, then can launch Quick Learn, the Vim starter course, or the menu.

## Interactive UI

The REPL is line-oriented but coalesces quick multiline paste into one learner message on POSIX terminals.
Windows does not support `select.select` on stdin, so the same input path falls back to one line per learner message.
After a tutor response, learner-metadata extraction is deferred so the next prompt appears immediately.
The REPL restores a topic's valid in-flight learner prompt after process restart so Enter resubmits it and typing replaces it.
Provider and turn failures retain that prompt, while successful natural navigation clears it as intentionally abandoned.
Natural navigation phrases such as `continue`, `move on`, and `skip` advance the current slide instead of being graded as answers.
Tutor output renders in a Rich panel for interactive terminal sessions, streaming updates redraw the same panel as tokens arrive, and hidden answer or coverage markers are stripped before display.
Multiple-choice options are normalized onto separate lines before Rich Markdown rendering.

## Tests

`make check` is the gate.
Tests use temporary `OPENLEARN_HOME` directories and mock mode where needed so they do not touch real user data.
Provider-configuration tests also clear provider environment variables, mock saved config reads, and reset the config cache.
GitHub Actions also runs `python -m unittest` on Ubuntu, Windows, and macOS for Python 3.11 and 3.13.
Workflow tests that require `pexpect` and a POSIX pty are skipped on Windows with explicit reasons.
