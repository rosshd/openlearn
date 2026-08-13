openLearn Manual Test Toolkit

Purpose
These files make manual UX testing faster without using your real learning state.
Everything defaults to OPENLEARN_HOME=/tmp/openlearn-manual-vim unless you override it.

Files
- context/practical-vim-syllabus.txt
  Reusable context fixture for import/summarization/start-course tests.

- seed-vim-course.py
  Creates a Practical Vim Foundations course in an isolated OPENLEARN_HOME.
  Use --draft for Start course testing, or --started --with-session for Resume testing.

- run-menu-isolated.sh
  Opens the real menu against the isolated manual-test home.

- smoke-non-model.sh
  Runs quick scripted checks that should not call OpenAI.

- smoke-full.sh
  Runs a strict installed-artifact public-interface journey in an isolated
  openLearn home.
  It resolves `openlearn` from PATH by default and does not add `src/` to
  PYTHONPATH, so it can verify a wheel or source-distribution installation.
  Set OPENLEARN=/path/to/openlearn only to select a different installed binary.
  The script needs a POSIX shell, so run it from macOS, Linux, Git Bash, or WSL.

- public-release.md
  Defines the five-person fresh-user release gate, required platform and journey
  coverage, privacy-safe evidence, blocker triage, and the final GO/NO-GO record.

- interview-placement.md
  Exercises the accelerated Technical Interview Prep survey, canonical first
  lesson, provider retry, Maker Bench to CLI handoff, side chat, and caught-up
  practice behavior in an isolated learner home.

Fast Workflows
0. Shortest built-in workflow:
   openlearn test
   Then choose: 1. Start course

   If you are running from source without installing the package:
   PYTHONPATH=src python -m openlearn test

1. Draft course/start-course workflow with explicit script:
   python manual-tests/seed-vim-course.py --reset --draft
   bash manual-tests/run-menu-isolated.sh
   Then choose: 1. Start course

2. Resume workflow with the built-in command:
   openlearn test --reset --resume
   Then choose: 1. Resume

   From source:
   PYTHONPATH=src python -m openlearn test --reset --resume

3. Resume workflow with explicit script:
   python manual-tests/seed-vim-course.py --reset --started --with-session
   bash manual-tests/run-menu-isolated.sh
   Then choose: 1. Resume

4. Non-model smoke check:
   bash manual-tests/smoke-non-model.sh

5. Complete mocked application-interface journey:
   make e2e

   This checks every top-level and nested command help path, configuration,
   topic lifecycle, imports, Quick Learn, paste/edit, model-backed and dry-run
   actions, REPL routing, interview profile management, attempt listing, menu
   behavior, and deletion without using real learning data or provider calls.
   The same journey is part of `make check`.
   Set `OPENLEARN_E2E_KEEP=1` to preserve its temporary output artifacts, or
   provide an empty `OPENLEARN_HOME` to choose and preserve the test location.

6. Installed-artifact CLI and Maker Bench smoke:
   Install the release candidate into a fresh virtual environment.
   Run `openlearn --version`, `openlearn templates`, and
   `OPENLEARN_MOCK=1 bash manual-tests/smoke-full.sh --mock`.
   On macOS, Linux, Git Bash, or WSL, start Maker Bench in a separate terminal with
   `OPENLEARN_HOME="$(mktemp -d)" openlearn web --no-browser`.
   On Windows PowerShell, create a temporary directory and set `$env:OPENLEARN_HOME` before running `openlearn web --no-browser`.
   Open the loopback URL printed by the command, complete the setup or starter-course screen, and verify that the page loads its styles and scripts.
   Stop the server with Ctrl-C and remove only the temporary home you created.

Useful Environment Variables
- OPENLEARN_HOME
  Override the isolated state directory.
  Example: OPENLEARN_HOME=/tmp/openlearn-test-a bash manual-tests/run-menu-isolated.sh

- PYTHONPATH
  Source-only helpers set this when needed.
  smoke-full.sh deliberately does not, so installed-package tests cannot fall
  back to checkout code.

Recommended Manual Checks
- New course screen shows Name * and Goal *.
- Back with no required fields does not ask to save.
- Back with name+goal asks whether to save a draft.
- Context files screen lists practical-vim-syllabus.txt.
- Start course summarizes pending context, generates outline, allows rejection feedback, then starts lesson.
- Resume shows a short Where you left off block before continuing.
- Delete topic removes the topic and context folder while preserving the stable
  .practical-vim-foundations.md.lock synchronization identity.
