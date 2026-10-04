# Public release candidate dogfood

This gate validates the immutable Openlearn Community release candidate with five fresh testers before any public tag or publication.
It is not complete until all required journeys pass without maintainer intervention and every reported failure is classified.

## Safety and consent

- Ask each tester for consent before collecting a result.
- Give testers the exact wheel or source distribution plus its matching line from `SHA256SUMS`.
- Each tester uses their own provider account and accepts any provider charges, or chooses a local keyless Ollama setup.
- Never provide a maintainer API key, shared credit, or preconfigured learner home.
- Do not record API keys, launch URLs, screenshots, raw prompts, course content, imported content, event logs, backup archives, or terminal transcripts.
- Collect only the result fields in the evidence template below.
- Use a new empty `OPENLEARN_HOME` so the test cannot alter existing learner data.

## Coverage roster

Recruit five people who have not used this release candidate and who can follow the public documentation without private coaching.
Across the five testers, include at least one current macOS environment, one Windows environment, and one Linux environment.
The automated matrix covers Python 3.11 through 3.13, but record the tester's actual supported Python version.

Assign the supplemental journeys before testing:

| Tester | Required supplemental journey |
| --- | --- |
| A | Skip Technical Interview Prep placement, then begin the baseline lesson. |
| B | Complete the rapid Technical Interview Prep confidence placement. |
| C | Create and begin a broad custom course outside the bundled templates. |
| D | Complete Quick Learn from a small non-sensitive local file. |
| E | Recover from an unavailable optional code runtime and continue with text-only learning. |

At least one hosted-provider tester must recover from a rejected placeholder key before entering their real key.
At least one tester must recover from an unreachable provider or local endpoint without losing course state.
At least one tester should use a local keyless Ollama setup when practical.

## Candidate handoff

The maintainer records the release commit, intended version, artifact filename, and SHA-256 before sharing a candidate.
The tester verifies the artifact hash before installation.

macOS:

```bash
shasum -a 256 /path/to/openlearn-X.Y.Z-py3-none-any.whl
```

Linux:

```bash
sha256sum /path/to/openlearn-X.Y.Z-py3-none-any.whl
```

Windows PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 C:\path\to\openlearn-X.Y.Z-py3-none-any.whl
```

Stop if the result does not match `SHA256SUMS` exactly.

## Core journey for every tester

The tester performs this journey without maintainer intervention.
Documentation links are allowed because documentation discoverability is part of the test.

1. Follow `docs/INSTALL.md` to create a clean environment and install the supplied artifact.
2. Run `openlearn --version` and confirm it matches the candidate version.
3. Create an empty test home and set `OPENLEARN_HOME` to it for the rest of the journey.
4. Start `openlearn web` and reach Maker Bench through the printed or opened loopback URL.
5. Read the Community cost and privacy explanation.
6. Configure a learner-owned hosted provider or a local keyless endpoint, or intentionally defer setup where the chosen offline flow allows it.
7. Find Technical Interview Prep in starter courses and complete the assigned skip or rapid confidence-placement route.
8. Reach one useful teaching interaction and respond once.
9. Leave the course, reopen Openlearn, and resume without losing the active objective.
10. Return to the course library, identify the active course, preview another course without activating it, and use Continue learning to switch deliberately.
11. Open the selected course path, settings, and Utilities menu without encountering a duplicate Quick Learn, settings, data, or empty-review panel.
12. Change one reversible course setting through preview and confirmation, then verify the course address and prior progress remain intact.
13. Open permanent deletion, verify the exact course name and ID are required, follow the backup link, and leave without deleting the course.
14. Locate the active learner-data home from the application and confirm it with `openlearn data inventory`.
15. Create a credential-redacted verified backup outside the test home.
16. Complete the assigned supplemental journey.

Measure time from first Openlearn launch to the first teaching interaction.
The target is under five minutes excluding external provider-account creation and local-model download time.

## Course-library presentation probes

Run these checks on the course-library dashboard after at least two courses exist.

- At a normal desktop width, select the non-active course and verify its preview updates while the active marker stays on the original course.
- Use browser Back, Forward, and Reload and verify the URL-selected preview is restored without changing the active course.
- At approximately 760 pixels and 320 pixels wide, verify the library stacks before the preview and the View selected course preview link moves focus deliberately.
- Increase browser text size to at least 150 percent and verify course names, status chips, path items, menus, and actions remain readable without horizontal page scrolling.
- Enable reduced motion and verify selection and disclosure change immediately without sliding, resizing, or flashing movement.
- Navigate the course rows, full-path disclosure, Utilities menu, Continue learning, settings, backup link, and deletion fields using only the keyboard.
- Disable JavaScript for one pass and verify course selection, settings forms, backup navigation, and deletion confirmation still use ordinary links and forms.
- If a generated follow-up is available, verify one click shows a pending state, duplicate submission is disabled, provider failure leaves retry available, and completion keeps the source-course preview visible until confirmation.

## Recovery probes

Use only the probes assigned in the roster and never sacrifice real learner data.

- Rejected key: enter an obvious non-secret placeholder, verify setup stays incomplete, then recover with the tester's own valid credential or switch providers.
- Unreachable provider: stop the local endpoint or use an unused loopback port, verify the error offers retry, switch, or defer, then recover.
- Optional runtime: open the code tool without Docker or Podman available, verify actionable setup guidance appears, then continue the text-only lesson.
- Placement: verify rapid confidence answers advance one topic at a time, review is editable, skip creates a baseline route, and leaving then resuming preserves progress without an editor.

## Evidence template

Create one sanitized record per tester outside the learner home.

```text
Tester ID: A-E
Consent to record this sanitized result: yes/no
Fresh to this candidate: yes/no
OS: macOS/Windows/Linux and version
Python: 3.11/3.12/3.13
Artifact: wheel/sdist filename
Artifact SHA-256 matched: yes/no
Provider route: hosted provider/local endpoint/deferred
First teaching interaction time, excluding external setup: minutes
Core journey: pass/fail
Supplemental journey: pass/fail
Recovery probe: pass/fail/not assigned
Maintainer intervention required: yes/no
Finding IDs: none or identifiers only
Tester summary: one sanitized sentence with no learner content
```

Do not add raw logs or private content to this record.

## Triage and release decision

Classify every finding before release:

- Release-blocking: credential exposure or false validation, learner-data loss, unusable installation or onboarding, failed course creation or resume, inaccessible core navigation, or bypass of loopback browser controls.
- Follow-up: a bounded issue with a clear workaround that does not compromise learning, privacy, security, or data ownership.
- Documentation-only: observed behavior is correct but the public instructions were unclear or incomplete.

Fix every release blocker, build a new candidate from a new commit, and repeat affected journeys.
Do not reuse an artifact hash after any code, asset, metadata, or documentation change.
Do not select or push the public tag until all five records pass, no tester required maintainer intervention, all blockers are closed, and the final candidate hashes match the release record.

## Gate record

The maintainer completes this section only after reviewing sanitized tester records.

```text
Release commit:
Intended version:
Candidate SHA256SUMS:
macOS coverage: pass/pending
Windows coverage: pass/pending
Linux coverage: pass/pending
Five fresh testers: pass/pending
Custom course: pass/pending
Quick Learn: pass/pending
Placement skip: pass/pending
Placement completion: pass/pending
Rejected or unreachable provider recovery: pass/pending
Unavailable optional tool recovery: pass/pending
Open release blockers: count
Final decision: GO/NO-GO
Reviewer and date:
```
