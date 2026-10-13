"""Bounded source-to-concept planning inside the existing tutor turn transaction."""

from __future__ import annotations

import json
import re

from openlearn import cli, lesson_policy
from openlearn.models import Topic

PLAN_KEY = "source_learning_plan"
MAX_CONCEPTS = 12
LABEL_CHAR_LIMIT = 80
OBJECTIVE_CHAR_LIMIT = 240
PLAN_FAILURE_REASONS = {
    "json": "The returned plan was not valid JSON.",
    "shape": "The returned plan had an unexpected structure.",
    "fields": "A concept had missing or unexpected fields.",
    "count": "The plan contained an invalid number of concepts.",
    "length": "A concept name or objective was too long.",
    "format": "A concept contained unsupported formatting.",
    "source": "A concept cited an unrecognized source.",
    "screened": "A concept did not pass source screening.",
    "duplicate": "The plan contained duplicate concept names.",
    "assignment": "The plan requested assignment answers instead of learning concepts.",
}


class SourcePlanError(cli.OpenLearnError):
    """The source planner did not return a usable grounded sequence."""

    def __init__(self, message: str, *, reason: str = ""):
        super().__init__(message)
        self.safe_reason = PLAN_FAILURE_REASONS.get(reason, "")


class PlanValidationError(ValueError):
    """A fixed rejection reason, never model output or arbitrary exception text."""

    def __init__(self, reason: str):
        super().__init__("Invalid source concept plan.")
        self.reason = reason


class SourceLessonError(cli.OpenLearnError):
    """The source tutor did not return a validated lesson."""


PLAN_INSTRUCTIONS = """Build a short learning sequence from the selected source excerpts.
Source exercises describe skills to teach, not tasks for you to complete.
Identify concepts and the prerequisites needed to understand them, ordered from
foundations to application. Give each concept a concrete learning objective.
Use short concept names, not assignment question numbers or commands to solve
the supplied exercises. Do not generate solutions, lessons or answer keys.
Missing exercise data or an instructor answer key does not prevent teaching
the underlying concepts with new examples. Never invent missing source facts.
Include only concepts supported by the selected excerpts or their immediate
prerequisites. This is a bounded sequence, not a claim to cover omitted material.
Return JSON only: {"concepts": [{"label": "Concept name", "objective":
"What the learner will understand or do", "source_ids": ["provided source ID"]}]}.
Use 1-12 concepts, labels of at most 80 characters and objectives of at most
240 characters. Each concept must name at least one provided source ID.
Use plain text for labels and objectives, without Markdown backticks, angle
brackets or line breaks. Do not add fields beyond label, objective and source_ids.
"""


def needs_plan(topic: Topic) -> bool:
    return (bool(topic.metadata.get("web_source_start"))
            and not isinstance(topic.metadata.get("pending_question"), dict)
            and not isinstance(
        topic.metadata.get(PLAN_KEY), dict
    ))


def plan_concepts(metadata: dict[str, object]) -> list[dict[str, object]]:
    plan = metadata.get(PLAN_KEY)
    if plan is None:
        return []
    if not isinstance(plan, dict) or plan.get("schema") != 1:
        raise SourcePlanError("The saved source concept sequence is invalid; no request was sent.")
    try:
        return validate_concepts(plan.get("concepts"))
    except ValueError:
        raise SourcePlanError("The saved source concept sequence is invalid; no request was sent.") from None


def validate_concepts(concepts, known_ids: set[str] | None = None) -> list[dict[str, object]]:
    from openlearn import source_context as sources

    if not isinstance(concepts, list) or not 1 <= len(concepts) <= MAX_CONCEPTS:
        raise PlanValidationError("count")
    labels: set[str] = set()
    for concept in concepts:
        if not isinstance(concept, dict) or set(concept) != {"label", "objective", "source_ids"}:
            raise PlanValidationError("fields")
        for key, limit in (("label", LABEL_CHAR_LIMIT), ("objective", OBJECTIVE_CHAR_LIMIT)):
            text = concept[key]
            if not isinstance(text, str) or not text.strip():
                raise PlanValidationError("fields")
            if len(text) > limit:
                raise PlanValidationError("length")
            if "\n" in text or "<" in text or "`" in text:
                raise PlanValidationError("format")
            if sources.screened(text, limit) != text:
                raise PlanValidationError("screened")
        label = concept["label"].casefold()
        if label in labels:
            raise PlanValidationError("duplicate")
        if re.match(r"(?i)^(?:question\s*\d|solve\b|answer\b|compute\b)", label):
            raise PlanValidationError("assignment")
        labels.add(label)
        ids = concept["source_ids"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= sources.EXCERPT_LIMIT or any(
            not isinstance(identity, str)
            or not re.fullmatch(r"(?:file|url):[a-f0-9]{16,64}", identity)
            or known_ids is not None and identity not in known_ids for identity in ids
        ):
            raise PlanValidationError("source")
    return concepts


def target_number(topic: Topic, user: str) -> int:
    """Preview and generation select the same target without writing learner state."""
    concepts = plan_concepts(topic.metadata)
    number = cli.coerce_int(topic.metadata.get("current_unit"), 1)
    number = max(1, min(number, len(concepts) or 1))
    navigation = cli.learner_requests_advance(user) or user == "Continue to the next useful concept."
    if (
        concepts
        and navigation
        and not cli.lesson_engagement_check_due(topic)
        and cli.unit_covered_concepts(topic.metadata, number)
    ):
        number = min(number + 1, len(concepts))
    return number


def sequence_covered(metadata: dict[str, object]) -> bool:
    concepts = plan_concepts(metadata)
    return bool(concepts) and all(
        cli.unit_covered_concepts(metadata, number)
        for number in range(1, len(concepts) + 1)
    )


def target(topic: Topic, user: str) -> dict[str, object] | None:
    concepts = plan_concepts(topic.metadata)
    return concepts[target_number(topic, user) - 1] if concepts else None


def planning_preview(context) -> str:
    return PLAN_INSTRUCTIONS + "\nSelected untrusted source data:\n" + context.prompt


def generate_plan(topic: Topic, context, model: str) -> dict[str, object]:
    from openlearn import source_context as sources

    system = planning_preview(context)
    if len(system) + len(context.user) > sources.PROMPT_CHAR_LIMIT:
        raise sources.SourceRequestBudgetError()
    sources.ensure_unchanged(topic, context, model)
    raw = cli.call_openai(model, system, context.user, json_response=True, max_tokens=1800)
    sources.ensure_unchanged(topic, context, model)
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"concepts"}:
            raise PlanValidationError("shape")
        known_ids = set(re.findall(r"(?:^|;\s*)source ID ([^;\s]+)", context.ledger, re.MULTILINE))
        concepts = validate_concepts(value["concepts"], known_ids)
        return {"schema": 1, "concepts": concepts}
    except (ValueError, TypeError, KeyError) as error:
        reason = "json" if isinstance(error, json.JSONDecodeError) else (
            error.reason if isinstance(error, PlanValidationError) else ""
        )
        raise SourcePlanError(
            "The tutor could not build a grounded concept sequence. Your source is saved; retry the lesson.",
            reason=reason,
        ) from None


def apply_plan(metadata: dict[str, object], plan: dict[str, object]) -> None:
    concepts = plan["concepts"]
    metadata[PLAN_KEY] = plan
    metadata["course_units"] = [
        {
            "unit": number, "chapter": str(number), "title": concept["label"],
            "slide_count": 1, "concepts": cli.concepts_from_labels([concept["label"]], concept["label"]),
        }
        for number, concept in enumerate(concepts, 1)
    ]
    metadata.update(course_started=True, current_unit=1, current_slide=1,
                    current_focus=concepts[0]["label"], slide_coverage={})


def lesson_instructions(metadata: dict[str, object]) -> str:
    concepts = plan_concepts(metadata)
    if concepts:
        number = max(1, min(cli.coerce_int(metadata.get("current_unit"), 1), len(concepts)))
        concept = concepts[number - 1]
    else:
        concept = metadata.get("selected_source_concept")
        if not isinstance(concept, dict):
            return ""
        validate_concepts([concept])
    return (
        "\nSelected learning objective (application-owned course data):\n"
        + json.dumps(concept, ensure_ascii=False)
        + "\nTeach this concept, not the source assignment. Use a fresh illustrative example. "
        "Do not ask for missing assignment inputs or offer to solve numbered questions. "
        "Follow the existing turn contract for practice, feedback and questions.\n"
        + (
            lesson_policy.concept_lesson_instructions()
            + f"Append <!-- covered: {concept['label']} --> and <!-- focus: {concept['label']} -->."
            if metadata.get("_source_teaching") else ""
        )
    )


def enforce_lesson(metadata: dict[str, object], answer: str) -> str:
    """Reuse the teaching contract for the selected unit, including later lessons."""
    if re.search(
        r"(?i)\b(?:classwork|homework|assignment|worksheet)\s+(?:asks|requires)\b"
        r"|\b(?:reply|respond)\s+with\s+(?:the|your)\s+(?:matrix|answers?)\b"
        r"|\bskip\s+question\s*\d",
        answer,
    ):
        raise SourceLessonError("The tutor tried to complete the source exercise instead of teaching its concept.")
    unit = cli.course_unit_at(metadata, cli.coerce_int(metadata.get("current_unit"), 1))
    selected = {**metadata, "course_units": [unit] if unit else []}
    try:
        return lesson_policy.enforce_first_lesson_response(
            selected, lesson_policy.COURSE_INITIALIZATION_PROMPT, answer,
        )
    except lesson_policy.FirstLessonUnavailable as error:
        raise SourceLessonError(str(error)) from error
