"""Shared teaching-first course initialization contract."""

from __future__ import annotations

import re
from collections.abc import Mapping

from openlearn.constants import FIRST_LESSON_WORD_LIMIT


COURSE_INITIALIZATION_PROMPT = "Start my first lesson."
FIRST_LESSON_PROMPT_PREFIX = "Start teaching unit 1 from this accepted course plan."


def is_first_lesson_prompt(value: object) -> bool:
    """Recognize accepted-plan prompts, including saved prompts."""
    return isinstance(value, str) and value.startswith(FIRST_LESSON_PROMPT_PREFIX)


def is_course_initialization_prompt(value: object) -> bool:
    return value == COURSE_INITIALIZATION_PROMPT or is_first_lesson_prompt(value)


def first_lesson_prompt(outline: str, *, first_activity: str | None = None) -> str:
    required_activity = (
        f"The required first activity is {first_activity}. Teach that activity now. "
        if first_activity
        else ""
    )
    return (
        f"{FIRST_LESSON_PROMPT_PREFIX} "
        f"{required_activity}"
        "Do not repeat the whole plan. "
        f"{first_lesson_instructions()}"
        "Append <!-- covered: Exact concept label --> using one exact label from "
        "the current unit's Concepts: line. This marker is hidden from the learner "
        "and is required for coverage tracking.\n\n"
        f"Accepted course plan:\n{outline}"
    )


def first_lesson_instructions() -> str:
    """The teaching contract shared by planned and unplanned startup."""
    return (
        "Teach exactly one concept. "
        "Use exactly one **Lesson:** section and no other primary label. "
        "Use two short paragraphs: explain the concept first, then start the "
        "second paragraph with 'For example,' and make it concrete. Keep the "
        "example accessible without relying on an algorithm, data structure, or "
        "system component that has not been introduced. Use 2-4 sentences total. Do not append a "
        "check, question, continuation cue, or learner action. "
        f"Hard limit: {FIRST_LESSON_WORD_LIMIT} words.\n"
    )


def initialization_generation_prompt(prompt: str) -> str:
    """Expand the saved sentinel without claiming that an outline was accepted."""
    if prompt != COURSE_INITIALIZATION_PROMPT:
        return prompt
    return (
        f"{COURSE_INITIALIZATION_PROMPT} Teach from the course goal and available context. "
        f"{first_lesson_instructions()}"
        "If the current unit has a Concepts: line, append <!-- covered: Exact concept label --> "
        "using one label from it. Otherwise do not invent a coverage label."
    )


def enforce_first_lesson_response(metadata: Mapping[str, object], prompt: str, answer: str) -> str:
    """Guarantee that course initialization teaches instead of emitting navigation."""
    if not is_course_initialization_prompt(prompt):
        return answer
    focus = str(metadata.get("current_focus") or "the first course concept")
    concept = focus
    valid_concepts: list[str] = []
    units = metadata.get("course_units")
    unit_titles: set[str] = set()
    if isinstance(units, list):
        unit_titles = {
            str(unit.get("title") or "")
            for unit in units
            if isinstance(unit, dict)
        }
    if isinstance(units, list) and units and isinstance(units[0], dict):
        concepts = units[0].get("concepts")
        valid_concepts = [
            str(item["label"]).strip()
            for item in (concepts if isinstance(concepts, list) else [])
            if isinstance(item, dict)
            and isinstance(item.get("label"), str)
            and item["label"].strip()
        ]
        if isinstance(concepts, list) and concepts and isinstance(concepts[0], dict):
            concept = str(concepts[0].get("label") or focus)
    declared = re.findall(r"<!--\s*covered:\s*(.*?)\s*-->", answer, flags=re.IGNORECASE)
    valid_concept_keys = {label.casefold() for label in valid_concepts}
    if first_lesson_response_is_valid(answer) and (
        not valid_concepts
        or any(marker.casefold() in valid_concept_keys for marker in declared)
    ):
        return answer
    system_design_heavy = "Coding Pattern Maintenance" in unit_titles
    if concept.casefold() == "clarifying requirements" and system_design_heavy:
        lesson = (
            "Before proposing components, turn the prompt into explicit functional requirements "
            "and quality attributes. Ask about scale, latency, consistency, and availability only "
            "when the prompt leaves them open, then state important assumptions aloud.\n\n"
            "For example, for a link-sharing service, clarify expected traffic, whether reads or "
            "writes dominate, and whether availability or strict freshness matters most before "
            "choosing any components."
        )
    elif concept.casefold() == "clarifying requirements":
        lesson = (
            "Before writing code, restate the required output and ask only about ambiguities "
            "that could change the solution. This prevents solving the wrong problem and makes "
            "your tradeoffs easier to explain.\n\n"
            "For example, for 'return the first repeated value in a list,' clarify whether to "
            "return the value or its index and what to return when no repeat exists."
        )
    else:
        lesson = (
            f"Begin {focus} by building a clear mental model of {concept}. Identify what "
            "information controls the result before working through details.\n\n"
            "For example, write down the input, required output, and one reason your chosen "
            "method fits before you commit to the implementation."
        )
    return f"**Lesson:**\n{lesson}\n\n<!-- covered: {concept} -->"


def first_lesson_response_is_valid(answer: str) -> bool:
    if re.search(r"<!--\s*openlearn-action\b", answer, flags=re.IGNORECASE):
        return False
    visible = re.sub(r"<!--.*?-->", "", answer, flags=re.DOTALL).strip()
    labels = re.findall(
        r"(?im)^\s*(?:\*\*)?(Lesson|Feedback|Example|Check|Hint|Next|Action):(?:\*\*)?",
        visible,
    )
    if labels != ["Lesson"] or "?" in visible:
        return False
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", visible) if part.strip()]
    if len(paragraphs) != 2 or not paragraphs[1].casefold().startswith("for example,"):
        return False
    sentence_count = len(re.findall(r"[.!](?=\s|$)", visible))
    return 2 <= sentence_count <= 4 and len(visible.split()) <= FIRST_LESSON_WORD_LIMIT
