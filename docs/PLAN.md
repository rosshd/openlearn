# Product plan

This is the canonical current plan for openlearn.
GitHub issues hold scoped implementation work.
Git history holds completed milestone and implementation plans.

## Product direction

openlearn is a local-first tutor built around a general learning loop: set a goal, study one focused lesson, try an optional useful check, get feedback, save progress, and return to retrieve the idea later.
The local web app remains the default interface, and the keyboard-first CLI also supports this core loop.
They share teaching policy and learner state, while presentation features may differ by interface.
The optional TUI remains supported in its current form, with no further expansion planned.

Technical Interview Prep is a supported reference course for improving general tutor behavior and lesson design.
It is not a separate interview-training product scope.
The tutor must remain useful across subjects without assuming every course is academic or interview-focused.

## Current baseline

- Local course files and learner state remain the source of truth.
- Users bring their own hosted provider key or use a configured local endpoint.
- Course creation supports templates, custom topics, and Quick Learn imports.
- Technical Interview Prep remains available as a course with role context and rapid confidence ratings.
- Lessons teach one focused idea and keep checks optional for refreshers.
- Existing source imports, coding tools, interview records, and terminal workflows remain supported.
- Web and CLI use shared teaching policy and learner state; interface-specific presentation does not need exact feature parity.
- The CLI remains a complete keyboard-first interface over the same learner home.

## Before the first public release

1. Complete repeated manual learning journeys from a fresh learner home.
2. Close every blocker involving provider setup, course creation, placement, lesson progression, resume, and deletion.
3. Verify installation and the local web app from built wheel and source distributions.
4. Verify macOS, Windows, and Linux on supported Python versions.
5. Finish accessibility, dark-mode, responsive-layout, and plain-language review.
6. Run the public release dogfood gate with learner-owned provider accounts or local endpoints.
7. Build one immutable release candidate and publish only its matching tag and artifacts.

## Scope boundary

- Keep new work centered on the general tutoring loop and evidence from its use.
- Defer new interview simulations, additional language support, activity adapters, automatic research or diagrams, community features, and hosted or sync work until a later explicit scope decision.
- Existing imports, coding tools, interview records, templates, and terminal workflows continue to work; this boundary does not remove them.

This scope boundary does not change current release requirements or data ownership.

## Release standard

The first public release is ready only when a new user can install openlearn, configure a provider, start a useful course, complete a lesson, leave, and resume without maintainer help or lost work.
Automated checks support that decision.
Human learning journeys make the final call.
