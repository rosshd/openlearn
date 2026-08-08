---
title: Early Web Learning Tools - Plan
type: feat
date: 2026-08-07
topic: early-web-tools
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: local-web-tutor-mvp
execution: code
---

# Early Web Learning Tools - Plan

## Goal

Extend the validated text tutor with the first optional learning tools without turning Focus Bench into a permanent multi-panel workspace.
The default lesson remains one readable tutor move and one response composer.
Dual Surface appears only when the learner deliberately opens a tool.

## Product Contract

The early post-MVP release includes a simple version of four capabilities:

1. A reusable Dual Surface host that places one optional tool beside the lesson and collapses cleanly at narrow widths.
2. A browser code workspace for drafting and running small Python examples through the existing bounded local runner.
3. A privacy-conscious YouTube player that accepts only validated YouTube URLs and loads the external iframe only after explicit learner action.
4. Web access to the existing bounded file, folder, and public GitHub import pipelines for grounding a course in learner-selected sources.

Technical Interview Prep remains the baseline course, but no tool architecture may assume an interview-only topic.
All imported material, tool drafts, and results remain learner-owned local data.
Import and extraction do not contact a model provider; selected source excerpts may be sent later when the tutor uses them.
Imported code is inert until the learner explicitly runs an editable copy in the code tool.

## Scope Boundaries

- The code workspace supports Python source, explicit Run and Reset actions, bounded output, and no autocomplete or interview scoring.
- The video tool embeds YouTube only and does not search, download, transcribe, or track watch completion.
- Imports reuse current format limits, secret filtering, deduplication, shallow public-repository handling, and non-execution guarantees.
- Tools do not automatically award mastery.
- Mobile-native Guided Build, collaborative editing, arbitrary web embeds, full IDE behavior, and domain-specific tools remain deferred.

## Implementation Phases

### Phase 1 - Shared tool contract and Dual Surface

- Add typed tool descriptors and web service methods that are independent of templates.
- Add a tool rail and a responsive Dual Surface container to Focus Bench.
- Preserve the current single-surface layout until a tool is selected.
- Keep tool state URL-addressable and restore keyboard focus when tools open or close.

Acceptance:

- A course opens exactly as the MVP does when no tool is selected.
- Opening and closing each tool does not lose an unsent tutor response.
- At 320 pixels the tool replaces the lesson surface instead of causing horizontal overflow.

### Phase 2 - Local code workspace

- Expose one bounded Python draft per course using existing learner-owned activity storage.
- Run drafts through the existing code-runner safety boundary and return structured stdout, stderr, status, and timeout results.
- Require explicit execution and provide no autocomplete, hidden test, placement, or mastery side effect.

Acceptance:

- A learner can edit, run, revise, and reset a small Python example from Technical Interview Prep.
- A timeout or invalid program returns a bounded readable result without blocking the tutor server.
- Draft and result recovery survives a page refresh.

### Phase 3 - YouTube player

- Validate normal YouTube and youtu.be URLs into one canonical video identifier.
- Render a consent surface before creating the iframe.
- Use a restrictive iframe sandbox, referrer policy, and Content Security Policy allowlist.

Acceptance:

- Invalid or non-YouTube URLs never become embeds.
- The page makes no YouTube request until the learner selects Load video.
- Closing the tool destroys the iframe and returns focus to the opener.

### Phase 4 - Course source imports

- Add a course Sources tool with file upload, local folder path, and public GitHub URL inputs.
- Reuse existing import services rather than shelling out from HTTP routes directly.
- Show per-source imported, skipped, and failed results plus the durable source list.

Acceptance:

- Supported files and folders respect current size, count, symlink, hidden-path, generated-folder, and secret-name boundaries.
- Public GitHub import remains shallow, disables prompts and hooks, and never executes repository code.
- Duplicate sources are reported as skipped without duplicating context.

### Phase 5 - Integrated validation and local-main merge

- Add service, HTTP, security, browser, package, and CLI-continuity regression coverage.
- Exercise Technical Interview Prep and one generic course.
- Run `make review`, complete a report-only final review, fix actionable findings, and rerun the gate.
- Commit on the feature branch, fast-forward local `main`, and remove the merged worktree and branch.

## Security and Privacy Invariants

- Every tool route remains behind the per-launch capability namespace, Host checks, same-origin policy, and CSRF protection.
- User-controlled paths resolve through existing project-home and course boundaries.
- Browser uploads and provider responses remain size bounded.
- YouTube is the only new remote browser origin and is opt-in per load.
- Code execution uses the existing isolated runner contract and never executes imported repository files directly.

## Verification

- Focused unit and integration tests for every service and route.
- JavaScript syntax and responsive browser tests for open, close, focus, refresh, and error recovery.
- Package smoke proving all templates and static assets ship in wheel and sdist installs.
- Final green gate: `make review`.

## Stop Condition

Stop after Dual Surface, the simple code workspace, the consent-based YouTube player, and file/folder/public-GitHub course imports are implemented, reviewed, merged into local `main`, and their feature worktree is removed.
Do not continue into richer IDE behavior, video search or transcription, mobile work, or additional specialized tools.
