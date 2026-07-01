---
name: openlearn-tutor-policy
description: >
  Use when changing tutor prompts, answer judging, feedback policy, mastery,
  anti-gaming, quizzes, SRS, progress, difficulty, or learning-science behavior.
---

# openlearn tutor policy

## Core Thesis

openlearn's quality comes from the closed loop:
persistent learner state, calibrated answer judging, explicit move selection, and delayed retrieval measurement.
The model is replaceable; the loop is the product.

## Turn Loop

1. Ingest learner message, latency, pending check, and recent tutor text.
2. Classify the message as answer, question, request, confusion, or other.
3. Judge understanding with structured output.
4. Detect shallow or copied answers with deterministic signals where possible.
5. Update mastery, misconceptions, rolling pass rate, difficulty, quiz state, and SRS.
6. Select the tutor move from learner state.
7. Generate terminal-friendly tutor output.
8. Advance only when the mastery gate is met.

Explicit requests to skip, continue, move on, or go to the next slide are navigation decisions.
Do not grade them as answers.
Clear stale learning gates, advance, and preserve durable learner preferences when the wording says the learner does not need or want the material.

## Judge Contract

- Score actual understanding from `0` to `1`.
- Preserve and obey stored answer keys for multiple choice.
- Support free-response pending questions and multiple-choice pending questions without stored keys.
- Distinguish recognition, recall, explanation, transfer, and production.
- Capture specific misconception or prerequisite gap when possible.
- Treat fast, high-overlap answers as suspect instead of mastery evidence.

## Move Policy

- Struggling: reduce load, isolate one sub-concept, prompt an attempt, then give a worked example if needed.
- On track: keep difficulty near the 80-85 percent success band with production and transfer checks.
- Mastering: avoid fluency illusion with edge cases, prediction, novel transfer, and harder checks.
- Suspected gaming: verify with an immediate transfer question and do not advance.
- Explicit navigation: advance immediately and remember durable skip preferences.
- Advancement: require passed production or transfer evidence, not one correct or confident answer.

## Prompt Rules

- Prefer eliciting before telling.
- Withhold full answers until a genuine attempt when the learner can try.
- Ask checks only when they test important understanding.
- Avoid lookup questions whose answers appear verbatim in recent tutor text.
- Keep tutor output terminal-friendly and learner-facing only.
- Never invent keybindings or tool defaults from generic knowledge when learner context is silent.
- First lessons should teach exactly one concept with one Lesson section, one Example section, and at most one Check section.

## Measurement

- Optimize delayed retrieval performance, not in-session satisfaction.
- Slow AI-judge evals belong outside the default gate.
- Build and preserve fixtures for judge calibration and move quality before heavy prompt tuning.

## References

- `docs/TUTOR_INTERACTION.md` contains the longer design narrative.
- `docs/LEARNING_SCIENCE.md` contains the research notes.
