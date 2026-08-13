# Technical Interview Prep curriculum journey

This smoke flow verifies the accelerated placement, first technical lesson, provider recovery, CLI handoff, side chat, and long-term continuation contract.
Use an isolated learner home so the test cannot read or modify real courses, provider configuration, or credentials.

## Start an isolated Maker Bench

```bash
export OPENLEARN_HOME="$(mktemp -d)"
export OPENLEARN_MOCK=1
openlearn web --no-browser
```

Open the printed loopback URL.
Choose Technical Interview Prep from Starter courses.
The setup must explain that placement is a rapid confidence survey and must offer Start placement, Skip placement, and a back action.
It must not ask for an editor, coding language, container runtime, or executable coding sample.

## Complete accelerated placement

Start placement.
Choose a role family, target level, and interview mix.
For each visible topic, select one confidence value from 1 through 5.
Only one topic should be active at a time, and selecting a number should advance immediately.
Use the review screen to change one answer, then submit it.

The suggested course outline must begin with a technical target from the pinned route.
Coding, balanced, and system-design focuses must show different relevant topics and route ordering.
Communication, edge cases, complexity, testing, and interview habits should be embedded in technical sections rather than presented as a long behavior-first opening unit.
Choose Change course outline and verify that only bounded role, level, focus, date, study-time, confidence, pacing, and optional-unit controls are available.
Preview the change, then confirm it.

Repeat the journey once with Skip placement.
Skipping must create a conservative baseline route and must not claim mastery, readiness, or coding fluency.

## Verify the first technical lesson

With mock mode still enabled, confirm the outline and wait for the first lesson.
The large lesson title must be the canonical skill label, not `Current lesson`, `Step 1`, or a tutor-request count.
The first card must teach technical content immediately while embedding only the relevant interview habit.
If the card does not require an answer, it must show Continue, Skip for now, and Ask a question without an answer textarea.

Select Continue.
The committed lesson must remain readable while the next target is reserved and generated.
A separate status area must name the exact next target.
When generation commits, the card should swap once without clearing unsent text or snapping through intermediate heights.

Select Skip for now on a passive lesson.
The interface must explain that the skill was deferred without mastery credit and show that it will return after other work or in a later session.
First-pass coverage may increase from a committed lesson, while readiness work remains a separate count.

## Verify provider recovery

Run this part with a temporary local endpoint or a non-secret test provider configuration that can be made unavailable.
Never enter a maintainer key or another person's credential.
Start Continue, make the endpoint unavailable, and wait for the provider error.
The last committed lesson and exact saved target must remain visible.
The page must offer Retry same target, Cancel, and Provider settings.

Restore the endpoint and choose Retry same target.
The same target and operation must commit once without skipping or duplicating a curriculum skill.
If the response had already reached the generated checkpoint before interruption, recovery must not call the provider again.

## Hand off between Maker Bench and CLI

Stop the web server and keep the isolated learner home.
Run:

```bash
openlearn status technical-interview-prep
openlearn resume technical-interview-prep
```

The CLI must show the same unit, section, skill, first-pass coverage, readiness work, and revision that Maker Bench showed.
It must not print a generic unit-slide position or ask the model to choose the next topic.
If the course is caught up, the CLI must offer `/practice` instead of advancing beyond the route.

Restart Maker Bench with the same isolated learner home.
The course must reopen at the same committed lesson without repeating a completed target.
Open Ask a question and submit a question about the visible lesson.
Advance the course before the answer finishes when practical.
The side-chat answer must remain labeled with the original lesson occurrence and must not change the curriculum cursor or course revision.

## Verify long-term continuation

Advance until at least one skill is exposed, one is deferred, and one check is answered incorrectly.
Reload the page and restart the CLI between turns.
The incorrect skill must remain weak or due for verification rather than being treated as covered mastery.
The deferred skill must return after another committed target or a new study session.

After every accepted route skill has one committed first pass, ordinary Continue must disappear.
The interface must show caught-up state and a real next retrieval date only when matching scheduled review data exists.
Choose Practice now or `/practice`.
Practice must select a covered skill without moving the saved forward cursor or awarding mastery by itself.

## Inspect durable state

Use only the isolated learner home.

```bash
openlearn interview placement technical-interview-prep status
openlearn status technical-interview-prep
openlearn data inventory
```

The placement profile must report confidence-placement v4 and `mastery_update_applied: false`.
The topic state must contain one pinned curriculum bundle and version, one stable full skill reference, and no active generic slide-based progression authority for the interview course.
The topic transcript and event log must remain parseable after every restart.

Create an ordinary algorithms course as a compatibility check:

```bash
openlearn new ordinary-algorithms --goal "Learn algorithms outside interview prep" --template algorithms
```

The ordinary course must not create an adjacent interview profile, canonical interview route, rapid placement survey, or interview-only recovery controls.
