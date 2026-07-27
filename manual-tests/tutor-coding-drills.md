# Tutor-Orchestrated Coding Drills

Use a temporary `OPENLEARN_HOME` and a disposable course for this dogfood.
Do not use a real learner topic or private source material.

## Neovim

1. Set `EDITOR=nvim`.
2. Start a learning session on a Python or interview-preparation topic.
3. Discuss the active concept until the tutor offers a coding drill.
4. Confirm that no drill directory or editor appears before the separate `[y/N]` consent prompt.
5. Enter `n` and confirm that the proposal is cancelled without creating a workspace or opening Neovim.
6. Ask for another coding exercise, enter `y`, and confirm that Neovim opens a file under the topic-owned `learning-topics/drills/<slug>/` directory.
7. Confirm that the file's plan cues and starter support match the offered scaffolding level.
8. Make an incomplete attempt, save, exit Neovim, and run `/check`.
9. Confirm that feedback names the observed failure, reveals only one progressive hint, and asks for a retry without showing a complete solution.
10. Edit the same file, run `/check` again, and confirm that the next hint is more specific.
11. Make the tests pass and confirm that openLearn requests reflection or transfer evidence without claiming mastery from the test result alone.

## Graphical IDE

1. Set `EDITOR='code --wait'`, or save `["code", "--wait"]` as the configured editor adapter.
2. Repeat the accepted-drill flow and confirm that the IDE receives the generated file as one separate argument.
3. Close the IDE and confirm that the terminal session resumes with the same active drill.
4. Temporarily configure a missing graphical editor and accept a new drill.
5. Confirm that the workspace remains available, the terminal prints its manual-open path, and no mastery evidence is created for the launch failure.

## Official Link-Out Boundary

1. Accept a tutor-proposed drill whose source is an official `https://leetcode.com/problems/.../` URL.
2. Confirm that the browser opens only after consent.
3. Confirm that the local file contains the official URL and a learner-owned solution scaffold, but not a copied statement, examples, or tests.
4. Confirm that a non-LeetCode host, a non-HTTPS URL, or a URL outside `/problems/` is rejected before workspace creation or browser launch.
