"""Shared teaching-first course initialization contract."""

from __future__ import annotations

import re
from collections.abc import Mapping

from openlearn.constants import FIRST_LESSON_WORD_LIMIT
from openlearn.text import has_source_audit_metadata


COURSE_INITIALIZATION_PROMPT = "Start my first lesson."
FIRST_LESSON_PROMPT_PREFIX = "Start teaching unit 1 from this accepted course plan."
FIRST_LESSON_RETRY_MESSAGE = (
    "The tutor could not prepare a useful first lesson. Your course and input are saved. "
    "Retry the first lesson to continue."
)
SOURCE_TEACHING_INSTRUCTIONS = (
    "Use imported sources to determine the lesson's scope. An exercise prompt identifies "
    "a concept to teach; a missing instructor answer key is not a reason to withhold "
    "a general explanation or a new illustrative example. Never invent official answers, "
    "marking criteria, or missing document contents. Explain supported concepts and "
    "distinguish your own examples from claims about the source. If an exact source fact "
    "is unavailable, say so briefly without refusing ordinary concept teaching. "
    "Keep source filenames, source IDs, checksums, and extraction line ranges out of "
    "learner-facing prose. Do not append source audit or extraction-availability reports."
)

_INTERNAL_FIRST_LESSON_FRAMING = re.compile(
    r"\b(?:let['’]s|we\s+(?:will|can))\s+continue\b"
    r"|\b(?:next useful concept|previous lesson|as we (?:discussed|learned))\b"
    r"|\b(?:can(?:not|['’]t)|unable to)\s+(?:provide|supply|give)\b"
    r"[^.!?\n]{0,100}\b(?:graded (?:response|answer)|answer key|instructor rules)\b"
    r"|^\s*(?:#+\s*)?(?:\*\*)?Source (?:excerpts provided|audit|provenance):",
    flags=re.IGNORECASE | re.MULTILINE,
)


class FirstLessonUnavailable(ValueError):
    """No validated teaching or reviewed exact-concept fallback is available."""


def first_lesson_repair_prompt(prompt: str) -> str:
    """Request one fresh lesson without trusting the rejected response's markers."""
    return (
        f"{initialization_generation_prompt(prompt)}\n\n"
        "The previous response could not be used as a first lesson. "
        "Give a concrete explanation and example of the selected concept, not generic "
        "advice to build a mental model or identify input and output. "
        "Use only the course context. If current concept labels exist, use one exact "
        "label for coverage; otherwise omit coverage markers."
    )


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
        "Begin with the first concept on unit 1's Concepts: line when a plan exists. "
        "Do not frame this as continuing a previous lesson; nothing has been taught yet. "
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
    """Accept teaching, use reviewed content, or stop before recording coverage."""
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
    valid_concept_keys = {concept.casefold()} if valid_concepts else set()
    if first_lesson_response_is_valid(answer) and (
        not valid_concepts
        or (len(declared) == 1 and declared[0].casefold() in valid_concept_keys)
    ):
        return answer
    unsafe_metadata = bool(
        re.search(r"<!--\s*openlearn-action\b", answer, flags=re.IGNORECASE)
        or declared
        and (len(declared) != 1 or declared[0].casefold() not in valid_concept_keys)
    )
    normalized = None if unsafe_metadata else normalize_first_lesson_response(answer)
    if normalized is not None:
        marker = f"\n\n<!-- covered: {concept} -->" if valid_concepts else ""
        candidate = f"{normalized}{marker}"
        if first_lesson_response_is_valid(candidate):
            return candidate
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
        raise FirstLessonUnavailable(FIRST_LESSON_RETRY_MESSAGE)
    return f"**Lesson:**\n{lesson}\n\n<!-- covered: {concept} -->"


def normalize_first_lesson_response(answer: str) -> str | None:
    """Salvage grounded lesson prose when the model adds forbidden framing."""
    visible = re.sub(r"<!--.*?-->", "", answer, flags=re.DOTALL).strip()
    if _INTERNAL_FIRST_LESSON_FRAMING.search(visible) or has_source_audit_metadata(visible):
        return None
    visible = re.split(
        r"(?im)^\s*(?:#+\s*)?(?:\*\*)?(?:Check|Question|Next|Action):(?:\*\*)?",
        visible,
        maxsplit=1,
    )[0]
    visible = re.sub(
        r"(?im)^\s*(?:#+\s*)?(?:\*\*)?(?:Lesson|Explanation):(?:\*\*)?\s*",
        "",
        visible,
    )
    visible = re.sub(
        r"(?im)^\s*(?:#+\s*)?(?:\*\*)?Example:(?:\*\*)?\s*",
        "For example, ",
        visible,
    )
    match = re.search(r"(?i)\bfor example,\s*", visible)
    if match is None:
        return None

    def usable_sentences(value: str, limit: int) -> list[str]:
        sentences = re.split(r"(?<=[.!?])\s+", " ".join(value.split()))
        return [sentence.strip(" -*#") for sentence in sentences if sentence.strip()
                and "?" not in sentence][:limit]

    explanation = usable_sentences(visible[: match.start()], 2)
    example = usable_sentences(visible[match.end() :], 2)
    if not explanation or not example:
        return None
    normalized = (
        f"**Lesson:**\n{' '.join(explanation)}\n\n"
        f"For example, {' '.join(example)}"
    )
    return normalized if first_lesson_response_is_valid(normalized) else None


def first_lesson_response_is_valid(answer: str) -> bool:
    if re.search(r"<!--\s*openlearn-action\b", answer, flags=re.IGNORECASE):
        return False
    visible = re.sub(r"<!--.*?-->", "", answer, flags=re.DOTALL).strip()
    if _INTERNAL_FIRST_LESSON_FRAMING.search(visible) or has_source_audit_metadata(visible):
        return False
    if (
        "by building a clear mental model of" in visible.casefold()
        and "write down the input, required output" in visible.casefold()
    ):
        return False
    labels = re.findall(
        r"(?im)^\s*(?:\*\*)?(Lesson|Feedback|Example|Check|Hint|Next|Action):(?:\*\*)?",
        visible,
    )
    if labels != ["Lesson"] or "?" in visible:
        return False
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", visible) if part.strip()]
    if len(paragraphs) != 2 or not paragraphs[1].casefold().startswith("for example,"):
        return False
    if not re.search(r"[\w\d]", paragraphs[1][len("For example,"):]):
        return False
    sentence_count = len(re.findall(r"[.!](?=\s|$)", visible))
    return 2 <= sentence_count <= 4 and len(visible.split()) <= FIRST_LESSON_WORD_LIMIT
