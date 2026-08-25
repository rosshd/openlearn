# Product plan

This is the canonical current plan for openlearn.
GitHub issues hold scoped implementation work.
Git history holds completed milestone and implementation plans.

## Product direction

openlearn is a local-first tutor for learning a broad topic through focused lessons, useful checks, and persistent progress.
The local web app is the default interface.
The CLI remains a complete keyboard-first interface over the same learner home.

Technical Interview Prep is the reference course used to improve tutor behavior and lesson design.
The tutor must remain useful for other subjects without assuming that every course is academic or interview-focused.

## Current baseline

- Local course files and learner state remain the source of truth.
- Users bring their own hosted provider key or use a configured local endpoint.
- Course creation supports templates, custom topics, and Quick Learn imports.
- Technical Interview Prep uses role context and rapid confidence ratings instead of a placement coding test.
- Lessons teach one focused idea and keep checks optional for refreshers.
- The web lesson page supports side chat and early optional tools without making them prerequisites.
- The CLI supports the same course, provider, progress, and data-management workflows.

## Before the first public release

1. Complete repeated manual learning journeys from a fresh learner home.
2. Close every blocker involving provider setup, course creation, placement, lesson progression, resume, and deletion.
3. Verify installation and the local web app from built wheel and source distributions.
4. Verify macOS, Windows, and Linux on supported Python versions.
5. Finish accessibility, dark-mode, responsive-layout, and plain-language review.
6. Run the public release dogfood gate with learner-owned provider accounts or local endpoints.
7. Build one immutable release candidate and publish only its matching tag and artifacts.

## Early work after the first release

- Improve the code workspace for real course practice and interview simulation.
- Improve consent-based video lessons and source grounding.
- Add math rendering when math-heavy courses become a tested priority.
- Add more specialized course templates based on observed learning bottlenecks.
- Explore community course discovery and ratings after template quality and moderation rules exist.

## Hosted product direction

The downloadable Community edition remains bring-your-own-provider and local-first.
A later hosted subscription may provide managed model usage, sync, and simpler setup.
Hosted work must not weaken local data ownership or place a maintainer API key in a public client.

## Release standard

The first public release is ready only when a new user can install openlearn, configure a provider, start a useful course, complete a lesson, leave, and resume without maintainer help or lost work.
Automated checks support that decision.
Human learning journeys make the final call.
