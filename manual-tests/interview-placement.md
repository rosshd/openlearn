# Technical Interview Prep journey

This journey verifies rapid placement, course outline confirmation, the first technical lesson, optional checks, provider recovery, side chat, and CLI handoff.
Use an isolated learner home so the test cannot read or modify real courses or credentials.

## Start an isolated web app

```bash
export OPENLEARN_HOME="$(mktemp -d)"
export OPENLEARN_MOCK=1
openlearn web --no-browser
```

Open the printed loopback URL.
Choose Technical Interview Prep from the starter courses.
The setup must describe placement as a short confidence survey.
It must offer Start placement, Skip placement, and a back action.
It must not ask for an editor, container runtime, or executable coding sample.

## Complete rapid placement

Choose a role family, target level, and interview focus.
Coding, balanced, and system-design focuses must show different relevant topics.

Rate one visible topic at a time from 1 through 5.
Selecting a value must advance immediately.
Review all ratings at the end, change one answer, and submit.

The proposed course path must start with a useful technical topic.
Communication, edge cases, complexity, and testing should appear alongside technical lessons instead of forming a long behavior-first opening.

Choose Change course outline.
The editor must limit changes to the supported role, level, focus, schedule, confidence, pacing, and optional-topic controls.
Preview the new path before confirming it.

Repeat once with Skip placement.
Skipping must create a conservative route without claiming mastery, readiness, or coding fluency.

## Verify the first lesson

Confirm the course path and wait for the first lesson.
The lesson title must name the current technical idea.
The first card must teach that idea before asking the learner to use it.

When a check is present, verify these actions:

- Send answer submits the learner's response.
- I understand this - next concept advances without awarding mastery or marking the check as passed.
- Review this later defers the concept and explains that it will return.
- Ask a question opens side chat without replacing the lesson.

Choose I understand this - next concept.
The old check must leave the screen when generation starts.
The next lesson must replace the old card automatically.
The new check must appear only in its styled check box.
There must not be two visible checks or a second Show next lesson step.

Submit one answer on another lesson.
The saved answer must remain visible until feedback loads.
Feedback must replace the response area cleanly and keep the next response field usable.

## Verify side chat

Open Chat, ask about the visible lesson, and submit.
The answer must appear in the side panel while the lesson stays visible.
The question must remain tied to the lesson occurrence that was open when it was asked.

Click Chat again.
The side panel must close and the lesson must return to its normal width.
Open it once more and confirm the conversation remains available.

## Verify provider recovery

Use only mock mode, a temporary local endpoint, or a non-secret test provider account.
Never enter a maintainer credential.

Start a lesson transition and make the endpoint unavailable.
The committed lesson and saved target must remain available.
The page must offer retry and provider-settings recovery without losing the course position.

Restore the endpoint and retry.
The saved target must commit once without skipping or duplication.

## Verify CLI handoff

Stop the web server and keep the isolated learner home.

```bash
openlearn status technical-interview-prep
openlearn resume technical-interview-prep
```

The CLI must show the same current topic, coverage, readiness work, and revision.
It must not ask the model to choose a separate course position.

Restart the web app with the same learner home.
The course must reopen at the same committed lesson without repeating a completed target.

## Inspect durable state

```bash
openlearn interview placement technical-interview-prep status
openlearn status technical-interview-prep
openlearn data inventory
```

Placement must report confidence-placement v4 with `mastery_update_applied: false`.
The topic state must keep one pinned curriculum bundle and stable skill references.
The transcript and event log must remain parseable after restarts.

Create an ordinary algorithms course as a compatibility check:

```bash
openlearn new ordinary-algorithms --goal "Learn algorithms outside interview prep" --template algorithms
```

The ordinary course must not create an interview profile, confidence survey, or interview-only recovery controls.
