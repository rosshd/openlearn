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

Useful Environment Variables
- OPENLEARN_HOME
  Override the isolated state directory.
  Example: OPENLEARN_HOME=/tmp/openlearn-test-a bash manual-tests/run-menu-isolated.sh

- PYTHONPATH
  The scripts set this to src automatically where needed.

Recommended Manual Checks
- New course screen shows Name * and Goal *.
- Back with no required fields does not ask to save.
- Back with name+goal asks whether to save a draft.
- Context files screen lists practical-vim-syllabus.txt.
- Start course summarizes pending context, generates outline, allows rejection feedback, then starts lesson.
- Resume shows a short Where you left off block before continuing.
- Delete topic removes the topic, context folder, and .practical-vim-foundations.md.lock if present.
