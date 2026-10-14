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
    "administrative": "The plan contained study instructions instead of subject concepts.",
}


def safe_plan_diagnostics(raw: object) -> dict[str, object]:
    """Retain structural facts only, never arbitrary provider values or keys."""
    if not isinstance(raw, dict):
        return {}
    safe: dict[str, object] = {}
    enums = {
        "plan_reason": set(PLAN_FAILURE_REASONS),
        "plan_top_type": {"object", "array", "string", "number", "boolean", "null"},
        "plan_field": {"label", "objective", "source_ids"},
        "plan_source_issue": {"container", "count", "type", "malformed", "unselected"},
        "plan_source_prefix": {"file", "folder", "github", "url", "other"},
        "plan_source_form": {"identity", "checksum", "filename", "index", "other"},
    }
    for key, allowed in enums.items():
        if isinstance(raw.get(key), str) and raw[key] in allowed:
            safe[key] = raw[key]
    for key in (
        "plan_concept_index", "plan_source_index", "plan_concept_count",
        "plan_source_count", "plan_known_source_count", "plan_source_length",
        "plan_checksum_match_count", "plan_unknown_field_count", "plan_json_line", "plan_json_column",
    ):
        value = raw.get(key)
        if type(value) is int and 0 <= value <= 1000000:
            safe[key] = value
    fields = raw.get("plan_top_fields")
    allowed_fields = {"concepts", "schema", "plan", "units", "lessons", "sequence", "source_ids", "error", "message"}
    if isinstance(fields, list):
        safe["plan_top_fields"] = sorted({field for field in fields if isinstance(field, str) and field in allowed_fields})
    return safe


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    return {dict: "object", list: "array", str: "string", int: "number", float: "number"}.get(type(value), "null")


class SourcePlanError(cli.OpenLearnError):
    """The source planner did not return a usable grounded sequence."""

    def __init__(self, message: str, *, reason: str = "", diagnostics: dict[str, object] | None = None):
        super().__init__(message)
        self.safe_reason = PLAN_FAILURE_REASONS.get(reason, "")
        self.diagnostics = safe_plan_diagnostics({**(diagnostics or {}), "plan_reason": reason})


class PlanValidationError(ValueError):
    """A fixed rejection reason, never model output or arbitrary exception text."""

    def __init__(self, reason: str, **diagnostics: object):
        super().__init__("Invalid source concept plan.")
        self.reason = reason
        self.diagnostics = safe_plan_diagnostics({**diagnostics, "plan_reason": reason})


class SourceLessonError(cli.OpenLearnError):
    """The source tutor did not return a validated lesson."""


PLAN_INSTRUCTIONS = """Build a short learning sequence from the selected source excerpts.
Source exercises describe skills to teach, not tasks for you to complete.
Identify concepts and the prerequisites needed to understand them, ordered from
foundations to application. Give each concept a concrete learning objective.
Teach the subject matter in the source body. Exam dates, scope/exclusions,
tables of contents, study workflows, diagnostics, source citations and application
source limitations are constraints or navigation, not lessons to teach.
Do not turn instructions about using this guide or tutor into curriculum topics.
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
Copy the complete source ID, including its file:, folder:, github: or url: prefix.
Do not substitute a filename or an original checksum for the source ID.
"""


_STUDY_HEADING = re.compile(
    r"(?i)^(?:exam (?:scope|timing|format|details)|contents|table of contents|"
    r"using this|how to use|study (?:plan|sequence|strategy)|suggested (?:study|sessions)|"
    r"practice (?:midterm|exam)|worked practice answers|answer key|formula sheet|"
    r"sources and|course source map|source limitations|suggested sessions|a useful opening request)\b"
)
_ADMIN_CONCEPT = re.compile(
    r"(?i)^(?:exam (?:scope|timing|format)|excluded topics|"
    r"diagnostic assessment of (?:confirmed|review) topics|targeted weak topic study|"
    r"study (?:workflow|strategy|plan)|review workflow|source limitations|"
    r"practice (?:midterm|exam)|table of contents|using (?:this|the) guide)\b"
)


def subject_sections(lines: list[str]) -> tuple[list[tuple[int, int]], set[int]]:
    """Find shallow subject headings and exclude study wrappers when a body exists."""
    headings = []
    fence = None
    for index, line in enumerate(lines):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            char = marker[1][0]
            fence = None if fence == char else char if fence is None else fence
        if fence or marker:
            continue
        match = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            label = re.sub(r"^\d+[.)]?\s+", "", match[2]).strip()
            headings.append((index, len(match[1]), label))
    ignored: set[int] = set()
    eligible = []
    skip_level = None
    for number, (index, level, label) in enumerate(headings):
        if skip_level is not None and level <= skip_level:
            skip_level = None
        # A document title may contain all its subject headings. Never exclude
        # its entire subtree merely because the title says 'study guide'.
        if index == 0:
            continue
        if skip_level is None and _STUDY_HEADING.search(label):
            skip_level = level
        if skip_level is not None:
            end = headings[number + 1][0] if number + 1 < len(headings) else len(lines)
            ignored.update(range(index, end))
        else:
            eligible.append((index, level))
    if not eligible:
        # A source about study procedure, or plain exercises, remains usable.
        return [], set()
    level = min(level for _index, level in eligible)
    anchors = [index for index, heading_level in eligible if heading_level == level]
    ignored.update(range(anchors[0]))
    spans = []
    heading_levels = {index: level for index, level, _label in headings}
    for index in anchors:
        stop = index + 1
        for following in range(index + 1, len(lines)):
            if following in ignored or heading_levels.get(following, level + 1) <= level:
                break
            stop = following + 1
            if lines[following].strip() and not re.match(r"^\s*#", lines[following]):
                break
        spans.append((index, stop))
    return spans, ignored


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
        raise PlanValidationError("count", plan_concept_count=len(concepts) if isinstance(concepts, list) else 0)
    labels: set[str] = set()
    for number, concept in enumerate(concepts, 1):
        location = {"plan_concept_index": number}
        if not isinstance(concept, dict) or set(concept) != {"label", "objective", "source_ids"}:
            raise PlanValidationError("fields", **location)
        for key, limit in (("label", LABEL_CHAR_LIMIT), ("objective", OBJECTIVE_CHAR_LIMIT)):
            text = concept[key]
            if not isinstance(text, str) or not text.strip():
                raise PlanValidationError("fields", **location, plan_field=key)
            if len(text) > limit:
                raise PlanValidationError("length", **location, plan_field=key)
            if "\n" in text or "<" in text or "`" in text:
                raise PlanValidationError("format", **location, plan_field=key)
            if sources.screened(text, limit) != text:
                raise PlanValidationError("screened", **location, plan_field=key)
        label = concept["label"].casefold()
        if label in labels:
            raise PlanValidationError("duplicate", **location, plan_field="label")
        if re.match(r"(?i)^(?:question\s*\d|solve\b|answer\b|compute\b)", label):
            raise PlanValidationError("assignment", **location, plan_field="label")
        labels.add(label)
        ids = concept["source_ids"]
        location.update(plan_field="source_ids", plan_known_source_count=len(known_ids or ()))
        if not isinstance(ids, list):
            raise PlanValidationError("source", **location, plan_source_issue="container")
        if not 1 <= len(ids) <= sources.EXCERPT_LIMIT:
            raise PlanValidationError("source", **location, plan_source_issue="count", plan_source_count=len(ids))
        for index, identity in enumerate(ids, 1):
            detail = {**location, "plan_source_index": index, "plan_source_count": len(ids)}
            if not isinstance(identity, str):
                raise PlanValidationError("source", **detail, plan_source_issue="type")
            valid = bool(re.fullmatch(r"(?:file|folder|github|url):[a-f0-9]{16,64}", identity))
            prefix, _, checksum = identity.partition(":")
            bare_checksum = bool(re.fullmatch(r"[a-f0-9]{16,64}", identity))
            if bare_checksum:
                checksum = identity
            detail.update(
                plan_source_length=len(identity),
                plan_source_prefix=prefix if prefix in {"file", "folder", "github", "url"} else "other",
                plan_source_form="identity" if valid else "checksum" if bare_checksum else "filename" if re.search(r"\.(?:txt|md|py|pdf|docx)$", identity, re.I) else "index" if identity.isdecimal() else "other",
                plan_checksum_match_count=sum(value.partition(":")[2] == checksum for value in known_ids or ()) if checksum else 0,
            )
            if not valid:
                raise PlanValidationError("source", **detail, plan_source_issue="malformed")
            if known_ids is not None and identity not in known_ids:
                raise PlanValidationError("source", **detail, plan_source_issue="unselected")
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
    from openlearn import source_context as sources

    return PLAN_INSTRUCTIONS + "\nSelected untrusted source data:\n" + context.prompt + "\n" + sources.SOURCE_POLICY


def _resolve_plan_source_ids(concepts, known_ids: set[str]):
    """Resolve checksum shorthand only when one selected identity matches exactly."""
    from openlearn import source_context as sources

    if not isinstance(concepts, list) or not 1 <= len(concepts) <= MAX_CONCEPTS:
        return concepts
    resolved = []
    for concept in concepts:
        if (not isinstance(concept, dict) or not isinstance(concept.get("source_ids"), list)
                or not 1 <= len(concept["source_ids"]) <= sources.EXCERPT_LIMIT):
            resolved.append(concept)
            continue
        identities = []
        for identity in concept["source_ids"]:
            matches = [value for value in known_ids if value.partition(":")[2] == identity] if (
                isinstance(identity, str) and re.fullmatch(r"[a-f0-9]{16,64}", identity)
            ) else []
            identities.append(matches[0] if len(matches) == 1 else identity)
        resolved.append({**concept, "source_ids": identities})
    return resolved


def generate_plan(topic: Topic, context, model: str) -> dict[str, object]:
    from openlearn import source_context as sources

    system = planning_preview(context)
    if len(system) + len(context.user) > sources.PROMPT_CHAR_LIMIT:
        raise sources.SourceRequestBudgetError()
    sources.ensure_unchanged(topic, context, model)
    raw = cli.call_openai(model, system, context.user, json_response=True, max_tokens=1800)
    sources.ensure_unchanged(topic, context, model)
    diagnostics: dict[str, object] = {}
    try:
        value = json.loads(raw)
        diagnostics["plan_top_type"] = _json_type(value)
        if isinstance(value, dict):
            diagnostics["plan_top_fields"] = list(value)
            diagnostics["plan_unknown_field_count"] = len(value) - len(safe_plan_diagnostics(diagnostics)["plan_top_fields"])
        if not isinstance(value, dict) or set(value) != {"concepts"}:
            raise PlanValidationError("shape")
        known_ids = set(re.findall(r"(?:^|;\s*)source ID ([^;\s]+)", context.ledger, re.MULTILINE))
        concepts = validate_concepts(_resolve_plan_source_ids(value["concepts"], known_ids), known_ids)
        if context.subject_sections:
            concepts = [concept for concept in concepts if not _ADMIN_CONCEPT.search(
                re.sub(r"[-_]+", " ", concept["label"])
            )]
            if not concepts:
                raise PlanValidationError("administrative")
        return {"schema": 1, "concepts": concepts}
    except (ValueError, TypeError, KeyError) as error:
        reason = "json" if isinstance(error, json.JSONDecodeError) else (
            error.reason if isinstance(error, PlanValidationError) else ""
        )
        if isinstance(error, json.JSONDecodeError):
            diagnostics.update(plan_json_line=error.lineno, plan_json_column=error.colno)
        if isinstance(error, PlanValidationError):
            diagnostics.update(error.diagnostics)
        raise SourcePlanError(
            "The tutor could not build a grounded concept sequence. Your source is saved; retry the lesson.",
            reason=reason,
            diagnostics=diagnostics,
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
