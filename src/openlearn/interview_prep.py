"""Local interview-prep profile and coding-placement lifecycle."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

PROFILE_SCHEMA_VERSION = 1
PLACEMENT_V1 = "coding-placement-v1"
PLACEMENT_V2 = "coding-placement-v2"
PLACEMENT_V3 = "reasoning-placement-v3"
PLACEMENT_V4 = "confidence-placement-v4"
PLACEMENT_RUBRIC_VERSION = PLACEMENT_V3
STALE_AFTER_DAYS = 90
PLACEMENT_V1_STAGES = (
    "calibration",
    "clarification",
    "plan",
    "implementation",
    "tests",
    "complexity",
    "follow_up",
)
PLACEMENT_V2_STAGES = ("conversation", "implementation", "debrief")
PLACEMENT_V3_STAGES = ("clarification", "reasoning")
PLACEMENT_V4_STAGES = ("confidence", "outline")
# Kept as the historical stage list for callers that construct v1 fixtures.
PLACEMENT_STAGES = PLACEMENT_V1_STAGES
PLACEMENT_LIFECYCLES = {
    PLACEMENT_V1: {
        "stages": PLACEMENT_V1_STAGES,
        "optional_stages": (),
    },
    PLACEMENT_V2: {
        "stages": PLACEMENT_V2_STAGES,
        "optional_stages": ("debrief",),
    },
    PLACEMENT_V3: {
        "stages": PLACEMENT_V3_STAGES,
        "optional_stages": PLACEMENT_V3_STAGES,
    },
    PLACEMENT_V4: {
        "stages": PLACEMENT_V4_STAGES,
        "optional_stages": (),
    },
}
PLACEMENT_RUBRICS = {
    PLACEMENT_V1: {
        "axes": {
            "prerequisites": ("plan",),
            "coding_fluency": ("implementation", "tests"),
            "reasoning": ("plan", "complexity", "follow_up"),
            "interview_process": ("clarification", "tests", "follow_up"),
        },
        "evidence": {
            "prerequisites": ("plan", "implementation"),
            "coding_fluency": ("implementation", "tests"),
            "reasoning": ("plan", "complexity", "follow_up"),
            "interview_process": ("clarification", "tests", "follow_up"),
        },
    },
    PLACEMENT_V2: {
        "axes": {
            "prerequisites": ("conversation",),
            "coding_fluency": ("implementation",),
            "reasoning": ("conversation", "debrief"),
            "interview_process": ("conversation", "debrief"),
        },
        "evidence": {
            "prerequisites": ("conversation",),
            "coding_fluency": ("implementation",),
            "reasoning": ("conversation", "debrief"),
            "interview_process": ("conversation", "debrief"),
        },
    },
    PLACEMENT_V3: {
        "axes": {
            "prerequisites": ("reasoning",),
            "coding_fluency": (),
            "reasoning": ("reasoning",),
            "interview_process": ("clarification", "reasoning"),
        },
        "evidence": {
            "prerequisites": ("reasoning",),
            "coding_fluency": (),
            "reasoning": ("reasoning",),
            "interview_process": ("clarification", "reasoning"),
        },
    },
    PLACEMENT_V4: {
        "axes": {
            "prerequisites": (),
            "coding_fluency": (),
            "reasoning": (),
            "interview_process": (),
        },
        "evidence": {
            "prerequisites": (),
            "coding_fluency": (),
            "reasoning": (),
            "interview_process": (),
        },
    },
}
DRAFT_MAX_LINES = 50
DRAFT_MAX_LINE_LENGTH = 4_000
DRAFT_MAX_LENGTH = 40_000
PLACEMENT_V3_SIGNAL_LABELS = {
    "named_data_structure_or_strategy": "Named an approach and data structure",
    "covered_edges_and_tests": "Covered edge cases and expected tests",
    "stated_time_complexity": "Explained time complexity",
    "stated_space_complexity": "Explained space complexity",
}
CONFIDENCE_PATTERNS = (
    ("arrays_hashing", "Arrays & hashing"),
    ("two_pointers", "Two pointers"),
    ("sliding_window", "Sliding window"),
    ("stack", "Stacks"),
    ("binary_search", "Binary search"),
    ("linked_lists", "Linked lists"),
    ("trees", "Trees"),
    ("graphs", "Graphs"),
    ("heaps", "Heaps / priority queues"),
    ("backtracking", "Backtracking"),
    ("dynamic_programming", "Dynamic programming"),
    ("intervals_greedy", "Intervals & greedy"),
)
CONFIDENCE_PATTERN_IDS = frozenset(pattern_id for pattern_id, _label in CONFIDENCE_PATTERNS)
SYSTEM_DESIGN_TOPICS = (
    ("requirements_scope", "Requirements and scope"),
    ("capacity_estimation", "Capacity estimation"),
    ("api_design", "API and interface design"),
    ("data_modeling", "Data modeling"),
    ("databases_partitioning", "Databases and partitioning"),
    ("caching_delivery", "Caching and content delivery"),
    ("messaging_async", "Messaging and asynchronous work"),
    ("reliability_observability", "Reliability and observability"),
    ("tradeoff_communication", "Explaining system tradeoffs"),
)
SYSTEM_DESIGN_TOPIC_IDS = frozenset(
    topic_id for topic_id, _label in SYSTEM_DESIGN_TOPICS
)
CONFIDENCE_TOPIC_LABELS = dict((*CONFIDENCE_PATTERNS, *SYSTEM_DESIGN_TOPICS))
CONFIDENCE_SCALE = (
    (1, "New"),
    (2, "Shaky"),
    (3, "Some practice"),
    (4, "Confident"),
    (5, "Could explain"),
)
CONFIDENCE_ROLES = (
    ("general SWE", "General SWE"),
    ("backend", "Backend"),
    ("frontend", "Frontend"),
    ("mobile", "Mobile"),
    ("data / ML", "Data / ML"),
)
CONFIDENCE_LEVELS = (
    ("intern", "Intern"),
    ("entry", "Entry"),
    ("mid", "Mid-level"),
    ("senior", "Senior+"),
)
CONFIDENCE_FOCUSES = (
    ("coding", "Coding interviews"),
    ("balanced", "Coding + system design"),
    ("system_design", "System-design heavy"),
)
CONFIDENCE_SURVEY_ID = "leetcode_pattern_confidence_v1"


def confidence_topics_for_focus(focus: str) -> tuple[tuple[str, str], ...]:
    if focus == "coding":
        return CONFIDENCE_PATTERNS
    if focus == "balanced":
        return (*CONFIDENCE_PATTERNS, *SYSTEM_DESIGN_TOPICS)
    if focus == "system_design":
        return SYSTEM_DESIGN_TOPICS
    raise ValueError("interview-prep confidence survey focus is invalid")
PROFILE_FIELDS = (
    "role_family",
    "target_level",
    "interview_date",
    "coding_language",
    "weekly_minutes",
    "session_minutes",
    "data_structures_experience",
    "algorithms_experience",
    "interview_experience",
    "target_notes",
    "accessibility_preferences",
)
PLACEMENT_PROBLEM = {
    "problem_id": "first_unique_window_v1",
    "title": "First unique window",
    "prompt": (
        "Implement first_unique_window(text, width), returning the first index at which "
        "width consecutive characters are all distinct, or -1 when no such window exists."
    ),
    "source": "openLearn original problem bank",
    "license": "AGPL-3.0-or-later",
    "function_name": "first_unique_window",
    "function_stub": (
        "def first_unique_window(text, width):\n"
        '    """Return the first all-distinct window index, or -1."""\n'
        "    raise NotImplementedError\n"
    ),
    "examples": [
        {
            "inputs": {"text": "aabcde", "width": 3},
            "expected": 1,
        },
        {
            "inputs": {"text": "aaaa", "width": 2},
            "expected": -1,
        },
    ],
    "test_cases": [
        {
            "name": "normal_match",
            "inputs": {"text": "aabcde", "width": 3},
            "expected": 1,
            "hidden": False,
        },
        {
            "name": "no_match",
            "inputs": {"text": "aaaa", "width": 2},
            "expected": -1,
            "hidden": False,
        },
        {
            "name": "repeated_characters",
            "inputs": {"text": "abccdef", "width": 4},
            "expected": 3,
            "hidden": True,
        },
        {
            "name": "width_one",
            "inputs": {"text": "zz", "width": 1},
            "expected": 0,
            "hidden": True,
        },
        {
            "name": "nonpositive_width",
            "inputs": {"text": "abc", "width": 0},
            "expected": -1,
            "hidden": True,
        },
        {
            "name": "width_greater_than_text",
            "inputs": {"text": "abc", "width": 4},
            "expected": -1,
            "hidden": True,
        },
        {
            "name": "empty_text",
            "inputs": {"text": "", "width": 1},
            "expected": -1,
            "hidden": True,
        },
    ],
}

PLACEMENT_CONTRACT = (
    "text is a Python string and width is an integer.",
    "Return the zero-based start index of the first qualifying window.",
    "Return -1 when no qualifying window exists, including nonpositive widths and "
    "widths larger than the text.",
    "Character comparison uses Python string characters exactly as provided.",
)
PLACEMENT_ASSUMPTION_CARD = "Interviewer assumptions:\n" + "\n".join(
    f"- {item}" for item in PLACEMENT_CONTRACT
)
PLACEMENT_EXECUTION_EVIDENCE_KIND = "openlearn-placement-execution-v1"

Clock = Callable[[], datetime]
EventAppender = Callable[[str, dict[str, object]], None]


def _placement_versions(
    placement: Mapping[str, object],
) -> tuple[str, str]:
    """Resolve recorded versions, inferring v1 only for legacy records."""
    rubric = placement.get("rubric_version")
    lifecycle = placement.get("lifecycle_version")
    if "lifecycle_version" not in placement and rubric == PLACEMENT_V1:
        lifecycle = PLACEMENT_V1
    if not isinstance(lifecycle, str) or lifecycle not in PLACEMENT_LIFECYCLES:
        raise ValueError("interview-prep placement lifecycle is unsupported")
    if not isinstance(rubric, str) or rubric not in PLACEMENT_RUBRICS:
        raise ValueError("interview-prep placement rubric is unsupported")
    if lifecycle != rubric:
        raise ValueError("interview-prep placement lifecycle and rubric do not match")
    return str(lifecycle), str(rubric)


def placement_stages(placement: Mapping[str, object]) -> tuple[str, ...]:
    lifecycle, _rubric = _placement_versions(placement)
    definition = PLACEMENT_LIFECYCLES[lifecycle]
    return tuple(str(stage) for stage in definition["stages"])


def placement_optional_stages(placement: Mapping[str, object]) -> tuple[str, ...]:
    lifecycle, _rubric = _placement_versions(placement)
    definition = PLACEMENT_LIFECYCLES[lifecycle]
    return tuple(str(stage) for stage in definition["optional_stages"])


def placement_clarification_response(question: str) -> str:
    """Answer placement clarifications deterministically from the problem contract."""
    normalized = question.casefold()
    asks_contract = bool(
        re.search(r"\b(?:input|output|return|format|signature|parameter)\w*\b", normalized)
    )
    asks_examples = bool(re.search(r"\b(?:example|sample)\w*\b", normalized))
    asks_constraints = bool(
        re.search(
            r"\b(?:constraint|edge|boundar|width|character|unicode|case-sensitive)\w*\b",
            normalized,
        )
    )
    sections: list[str] = []
    if asks_contract:
        sections.append(
            "Contract:\n"
            "- Call `first_unique_window(text, width)` with a Python string and an integer.\n"
            "- Return the zero-based start index of the first width-character window "
            "whose characters are all distinct.\n"
            "- Return -1 when no qualifying window exists."
        )
    if asks_constraints:
        sections.append(
            "Constraints and edges:\n"
            "- Return -1 when width is nonpositive or greater than len(text).\n"
            "- Character comparison uses Python string characters exactly as provided "
            "(including case and Unicode)."
        )
    if asks_examples:
        sections.append(_placement_examples_text())
    if not sections:
        sections.extend((PLACEMENT_ASSUMPTION_CARD, _placement_examples_text()))
    return "\n\n".join(sections)


def _placement_examples_text() -> str:
    examples = PLACEMENT_PROBLEM["examples"]
    assert isinstance(examples, list)
    lines = ["Examples:"]
    for example in examples:
        assert isinstance(example, dict)
        inputs = example["inputs"]
        assert isinstance(inputs, dict)
        lines.append(
            f"- first_unique_window({inputs['text']!r}, {inputs['width']}) -> {example['expected']}"
        )
    return "\n".join(lines)


def placement_execution_evidence(
    source: str,
    *,
    outcome: str,
    tests_passed: bool,
    return_code: int | None,
) -> str:
    """Create the exact versioned envelope accepted by placement scoring."""
    if not isinstance(source, str) or not source.strip():
        raise ValueError("placement execution source must be non-empty text")
    if not isinstance(outcome, str) or not outcome.strip():
        raise ValueError("placement execution outcome must be non-empty text")
    if not isinstance(tests_passed, bool):
        raise ValueError("placement execution tests_passed must be boolean")
    if return_code is not None and (
        isinstance(return_code, bool) or not isinstance(return_code, int)
    ):
        raise ValueError("placement execution return_code must be an integer or null")
    return json.dumps(
        {
            "kind": PLACEMENT_EXECUTION_EVIDENCE_KIND,
            "source": source,
            "outcome": outcome.strip(),
            "tests_passed": tests_passed,
            "return_code": return_code,
        },
        sort_keys=True,
    )


def _parse_placement_execution_evidence(response: str) -> dict[str, object] | None:
    try:
        value = json.loads(response)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or set(value) != {
        "kind",
        "source",
        "outcome",
        "tests_passed",
        "return_code",
    }:
        return None
    return_code = value.get("return_code")
    if (
        value.get("kind") != PLACEMENT_EXECUTION_EVIDENCE_KIND
        or not isinstance(value.get("source"), str)
        or not str(value["source"]).strip()
        or not isinstance(value.get("outcome"), str)
        or not str(value["outcome"]).strip()
        or not isinstance(value.get("tests_passed"), bool)
        or (
            return_code is not None
            and (isinstance(return_code, bool) or not isinstance(return_code, int))
        )
    ):
        return None
    return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(now: Clock) -> str:
    return now().astimezone(timezone.utc).isoformat()


def _write_checkpoint(_stage: str, _path: Path) -> None:
    """Test seam for deterministic interruption around profile publication."""


def _write(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _write_checkpoint("before_replace", path)
        os.replace(temporary, path)
        _write_checkpoint("after_replace", path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def load_profile(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("interview-prep profile does not exist") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("interview-prep profile is unreadable") from exc
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema_version",
            "profile_revision",
            "created_at",
            "updated_at",
            "profile",
            "placement",
            "recommendations",
        }
        or value.get("schema_version") != PROFILE_SCHEMA_VERSION
    ):
        raise ValueError("interview-prep profile has an unsupported format")
    profile = value.get("profile")
    placement = value.get("placement")
    revision = value.get("profile_revision")
    if (
        not isinstance(profile, dict)
        or not isinstance(placement, dict)
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
    ):
        raise ValueError("interview-prep profile is malformed")
    if _normalized_profile(profile) != profile:
        raise ValueError("interview-prep profile is not canonical")
    _validated_timestamp(value.get("created_at"), "profile created_at")
    _validated_timestamp(value.get("updated_at"), "profile updated_at")
    _validate_placement(placement)
    if placement.get("status") in {"in_progress", "provisional"} and (
        placement.get("profile_revision") != revision
    ):
        raise ValueError("interview-prep placement does not match the current profile revision")
    _validate_recommendations(value.get("recommendations"), expected_revision=revision)
    return value


def _normalized_profile(values: Mapping[str, object]) -> dict[str, object]:
    unexpected = set(values) - set(PROFILE_FIELDS)
    if unexpected:
        raise ValueError(f"unknown interview profile field: {sorted(unexpected)[0]}")
    normalized: dict[str, object] = {}
    for field in PROFILE_FIELDS:
        value = values.get(field, "")
        if field in {"weekly_minutes", "session_minutes"}:
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise ValueError(f"{field} must be a positive integer")
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field} must be a positive integer") from exc
            if number < 1 or number > 10_080:
                raise ValueError(f"{field} must be between 1 and 10080")
            normalized[field] = number
            continue
        if not isinstance(value, str):
            raise ValueError(f"{field} must be text")
        text = value.strip()
        if len(text) > 2_000:
            raise ValueError(f"{field} is too long")
        normalized[field] = text
    session_minutes = normalized["session_minutes"]
    weekly_minutes = normalized["weekly_minutes"]
    assert isinstance(session_minutes, int) and isinstance(weekly_minutes, int)
    if session_minutes > weekly_minutes:
        raise ValueError("session_minutes cannot exceed weekly_minutes")
    return normalized


def _empty_placement(
    version: str = PLACEMENT_V3, *, include_lifecycle: bool = True
) -> dict[str, object]:
    if (
        not isinstance(version, str)
        or version not in PLACEMENT_LIFECYCLES
        or version not in PLACEMENT_RUBRICS
    ):
        raise ValueError("interview-prep placement version is unsupported")
    placement = {
        "status": "not_started",
        **({"lifecycle_version": version} if include_lifecycle else {}),
        "rubric_version": version,
        "attempt_id": None,
        "activity_id": None,
        "started_at": None,
        "updated_at": None,
        "completed_at": None,
        "profile_revision": None,
        "problem_id": None,
        "next_stage": None,
        "evidence_refs": [],
        "observations": {},
        "result": None,
    }
    if version == PLACEMENT_V3:
        placement["draft"] = None
    if version == PLACEMENT_V4:
        placement["survey"] = None
    return placement


def _empty_placement_for_reset(placement: Mapping[str, object]) -> dict[str, object]:
    lifecycle_version, _rubric_version = _placement_versions(placement)
    return _empty_placement(
        lifecycle_version,
        include_lifecycle="lifecycle_version" in placement,
    )


def _validated_timestamp(value: object, label: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"interview-prep {label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"interview-prep {label} is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"interview-prep {label} must include a timezone")


def _validate_placement(placement: Mapping[str, object]) -> None:
    legacy_keys = set(_empty_placement(PLACEMENT_V1, include_lifecycle=False))
    versioned_legacy_keys = set(_empty_placement(PLACEMENT_V1))
    v3_keys = set(_empty_placement(PLACEMENT_V3))
    v4_keys = set(_empty_placement(PLACEMENT_V4))
    if frozenset(placement) not in {
        frozenset(legacy_keys),
        frozenset(versioned_legacy_keys),
        frozenset(v3_keys),
        frozenset(v4_keys),
    }:
        raise ValueError("interview-prep placement state is malformed")
    status = placement.get("status")
    if status not in {
        "not_started",
        "deferred",
        "in_progress",
        "provisional",
        "stale",
    }:
        raise ValueError("interview-prep placement status is invalid")
    lifecycle_version, rubric_version = _placement_versions(placement)
    if lifecycle_version == PLACEMENT_V3 and set(placement) != v3_keys:
        raise ValueError("interview-prep placement v3 draft state is malformed")
    if lifecycle_version == PLACEMENT_V4 and set(placement) != v4_keys:
        raise ValueError("interview-prep placement v4 survey state is malformed")
    if lifecycle_version != PLACEMENT_V3 and "draft" in placement:
        raise ValueError("interview-prep legacy placement draft state is malformed")
    if lifecycle_version != PLACEMENT_V4 and "survey" in placement:
        raise ValueError("interview-prep legacy placement survey state is malformed")
    stages = placement_stages(placement)
    activity_id = placement.get("activity_id")
    if activity_id is not None and (
        not isinstance(activity_id, str) or re.fullmatch(r"act_[a-f0-9]{32}", activity_id) is None
    ):
        raise ValueError("interview-prep placement activity reference is invalid")
    attempt_id = placement.get("attempt_id")
    if attempt_id is not None and (
        not isinstance(attempt_id, str)
        or re.fullmatch(r"interview_attempt_[a-f0-9]{32}", attempt_id) is None
    ):
        raise ValueError("interview-prep placement attempt reference is invalid")
    for key in ("started_at", "updated_at", "completed_at"):
        _validated_timestamp(placement.get(key), f"placement {key}", optional=True)
    placement_revision = placement.get("profile_revision")
    if placement_revision is not None and (
        not isinstance(placement_revision, int)
        or isinstance(placement_revision, bool)
        or placement_revision < 1
    ):
        raise ValueError("interview-prep placement profile revision is invalid")
    problem_id = placement.get("problem_id")
    allowed_problem_id = (
        CONFIDENCE_SURVEY_ID
        if lifecycle_version == PLACEMENT_V4
        else PLACEMENT_PROBLEM["problem_id"]
    )
    if problem_id is not None and problem_id != allowed_problem_id:
        raise ValueError("interview-prep placement problem reference is invalid")
    next_stage = placement.get("next_stage")
    if next_stage is not None and next_stage not in stages:
        raise ValueError("interview-prep placement next stage is invalid")
    refs = placement.get("evidence_refs")
    if not isinstance(refs, list) or len(refs) > len(stages) + 1:
        raise ValueError("interview-prep placement evidence references are invalid")
    for ref in refs:
        if (
            not isinstance(ref, dict)
            or set(ref) != {"evidence_id", "event_type"}
            or ref.get("event_type") != "activity_evidence_recorded"
            or not isinstance(ref.get("evidence_id"), str)
            or re.fullmatch(r"evidence_[a-f0-9]{32}", str(ref["evidence_id"])) is None
        ):
            raise ValueError("interview-prep placement evidence reference is invalid")
    if len({str(ref["evidence_id"]) for ref in refs}) != len(refs):
        raise ValueError("interview-prep placement evidence references are duplicated")
    observations = placement.get("observations")
    if not isinstance(observations, dict) or not set(observations) <= {
        *stages,
        "baseline",
    }:
        raise ValueError("interview-prep placement observations are invalid")
    for stage, observation in observations.items():
        if (
            not isinstance(observation, dict)
            or set(observation) != {"status", "substantive", "skipped", "non_attempt", "signals"}
            or observation.get("status") not in {"observed", "not_observed", "uncertain"}
            or not isinstance(observation.get("signals"), list)
            or not all(isinstance(item, str) for item in observation["signals"])
            or not isinstance(observation.get("substantive"), bool)
            or not isinstance(observation.get("skipped"), bool)
            or not isinstance(observation.get("non_attempt"), bool)
        ):
            raise ValueError(f"interview-prep placement observation for {stage} is malformed")
    draft = placement.get("draft")
    if lifecycle_version == PLACEMENT_V3 and draft is not None:
        draft_stage = draft.get("stage") if isinstance(draft, dict) else None
        matching_published_draft = (
            isinstance(draft_stage, str)
            and draft_stage in stages
            and draft_stage in observations
            and any(
                ref.get("evidence_id") == placement_evidence_id(placement, draft_stage)
                for ref in refs
            )
        )
        if (
            not isinstance(draft, dict)
            or set(draft) != {"stage", "lines", "updated_at"}
            or (draft_stage != next_stage and not matching_published_draft)
            or not isinstance(draft.get("lines"), list)
            or not draft["lines"]
            or len(draft["lines"]) > DRAFT_MAX_LINES
            or not all(
                isinstance(line, str)
                and bool(line.strip())
                and line == line.strip()
                and len(line) <= DRAFT_MAX_LINE_LENGTH
                for line in draft["lines"]
            )
            or len("\n".join(draft["lines"])) > DRAFT_MAX_LENGTH
        ):
            raise ValueError("interview-prep placement draft is malformed")
        _validated_timestamp(draft.get("updated_at"), "placement draft updated_at")
    survey = placement.get("survey")
    if lifecycle_version == PLACEMENT_V4 and survey is not None:
        _validate_confidence_survey(survey)
    result = placement.get("result")
    if result is not None:
        _validate_result(result, rubric_version=rubric_version)
    if status in {"not_started", "deferred"} and any(
        placement.get(key) is not None
        for key in (
            "attempt_id",
            "activity_id",
            "started_at",
            "completed_at",
            "profile_revision",
            "problem_id",
            "next_stage",
            "result",
        )
    ):
        raise ValueError("interview-prep empty placement state is inconsistent")
    if status == "in_progress" and (
        (activity_id is None and lifecycle_version != PLACEMENT_V4)
        or attempt_id is None
        or next_stage is None
        or result is not None
        or placement.get("completed_at") is not None
    ):
        raise ValueError("interview-prep active placement state is inconsistent")
    if status in {"provisional", "stale"} and (
        (activity_id is None and lifecycle_version != PLACEMENT_V4)
        or attempt_id is None
        or next_stage is not None
        or result is None
        or not isinstance(placement.get("completed_at"), str)
    ):
        raise ValueError("interview-prep completed placement state is inconsistent")


def _validate_confidence_survey(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "role_family",
        "target_level",
        "interview_focus",
        "ratings",
        "outline",
    }:
        raise ValueError("interview-prep confidence survey is malformed")
    allowed_roles = {item for item, _label in CONFIDENCE_ROLES}
    allowed_levels = {item for item, _label in CONFIDENCE_LEVELS}
    allowed_focuses = {item for item, _label in CONFIDENCE_FOCUSES}
    if value.get("role_family") not in allowed_roles:
        raise ValueError("interview-prep confidence survey role is invalid")
    if value.get("target_level") not in allowed_levels:
        raise ValueError("interview-prep confidence survey level is invalid")
    focus = value.get("interview_focus")
    if focus not in allowed_focuses:
        raise ValueError("interview-prep confidence survey focus is invalid")
    ratings = value.get("ratings")
    expected_ids = frozenset(
        topic_id for topic_id, _label in confidence_topics_for_focus(str(focus))
    )
    accepted_id_sets = {expected_ids, CONFIDENCE_PATTERN_IDS}
    if (
        not isinstance(ratings, dict)
        or frozenset(ratings) not in accepted_id_sets
        or any(
            isinstance(rating, bool) or not isinstance(rating, int) or rating not in range(1, 6)
            for rating in ratings.values()
        )
    ):
        raise ValueError("interview-prep confidence ratings are invalid")
    outline = value.get("outline")
    if outline is not None and (
        not isinstance(outline, str)
        or not outline.strip()
        or len(outline) > 12_000
    ):
        raise ValueError("interview-prep confidence outline is invalid")


def _validate_result(result: object, *, rubric_version: str = PLACEMENT_V1) -> None:
    expected = {
        "provisional",
        "starting_level",
        "mastery_update_applied",
        "patterns_marked_known",
        "gaps",
        "uncertainty",
    }
    if rubric_version in {PLACEMENT_V3, PLACEMENT_V4}:
        expected.add("passport")
    if not isinstance(result, dict) or set(result) != expected:
        raise ValueError("interview-prep placement result is malformed")
    if (
        result.get("provisional") is not True
        or result.get("mastery_update_applied") is not False
        or result.get("patterns_marked_known") != []
        or not isinstance(result.get("starting_level"), str)
        or not isinstance(result.get("uncertainty"), list)
    ):
        raise ValueError("interview-prep placement result is invalid")
    gaps = result.get("gaps")
    if not isinstance(gaps, dict) or set(gaps) != {
        "prerequisites",
        "coding_fluency",
        "reasoning",
        "interview_process",
    }:
        raise ValueError("interview-prep placement gap result is invalid")
    for detail in gaps.values():
        if (
            not isinstance(detail, dict)
            or set(detail) != {"status", "evidence", "note"}
            or detail.get("status") not in {"observed", "not_observed", "uncertain"}
            or not isinstance(detail.get("evidence"), list)
            or not isinstance(detail.get("note"), str)
        ):
            raise ValueError("interview-prep placement gap detail is invalid")
    if rubric_version in {PLACEMENT_V3, PLACEMENT_V4}:
        passport = result.get("passport")
        if (
            not isinstance(passport, dict)
            or set(passport)
            != {
                "starting_route",
                "first_activity",
                "reasoning_signals",
                "practice_priority",
                "uncertainty_to_verify",
            }
            or not all(
                isinstance(passport.get(key), str) and bool(str(passport[key]).strip())
                for key in (
                    "starting_route",
                    "first_activity",
                    "practice_priority",
                    "uncertainty_to_verify",
                )
            )
            or not isinstance(passport.get("reasoning_signals"), list)
            or not passport["reasoning_signals"]
            or len(passport["reasoning_signals"]) > 8
            or not all(isinstance(item, str) and item for item in passport["reasoning_signals"])
        ):
            raise ValueError("interview-prep placement passport is invalid")


def _validate_recommendations(recommendations: object, *, expected_revision: int) -> None:
    if recommendations is None:
        return
    if not isinstance(recommendations, dict):
        raise ValueError("interview-prep recommendations are malformed")
    required = {
        "profile_revision",
        "weekly_minutes",
        "session_minutes",
        "sessions_per_week",
        "target",
        "horizon",
        "priorities",
    }
    if set(recommendations) != required:
        raise ValueError("interview-prep recommendations are malformed")
    if (
        not all(
            isinstance(recommendations[key], int)
            and not isinstance(recommendations[key], bool)
            and int(recommendations[key]) > 0
            for key in (
                "profile_revision",
                "weekly_minutes",
                "session_minutes",
                "sessions_per_week",
            )
        )
        or recommendations["profile_revision"] != expected_revision
        or not isinstance(recommendations["target"], str)
        or not isinstance(recommendations["horizon"], str)
        or not isinstance(recommendations["priorities"], list)
        or not all(isinstance(item, str) for item in recommendations["priorities"])
    ):
        raise ValueError("interview-prep recommendations are invalid")


def create_profile(
    path: Path, values: Mapping[str, object], *, now: Clock = _utcnow
) -> dict[str, object]:
    if path.exists():
        raise ValueError("interview-prep profile already exists")
    timestamp = _timestamp(now)
    value: dict[str, object] = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_revision": 1,
        "created_at": timestamp,
        "updated_at": timestamp,
        "profile": _normalized_profile(values),
        "placement": _empty_placement(),
        "recommendations": None,
    }
    _write(path, value)
    return value


def edit_profile(
    path: Path,
    changes: Mapping[str, object],
    append_event: EventAppender,
    *,
    now: Clock = _utcnow,
) -> dict[str, object]:
    value = load_profile(path)
    current = value["profile"]
    assert isinstance(current, dict)
    normalized = normalize_profile_update(current, changes)
    if normalized == current:
        return value
    current_revision = value["profile_revision"]
    assert isinstance(current_revision, int)
    revision = current_revision + 1
    value["profile"] = normalized
    value["profile_revision"] = revision
    value["updated_at"] = _timestamp(now)
    value["recommendations"] = None
    placement = value["placement"]
    assert isinstance(placement, dict)
    if placement.get("status") in {"provisional", "stale"}:
        placement["status"] = "stale"
        placement["updated_at"] = _timestamp(now)
    elif placement.get("status") == "in_progress":
        value["placement"] = _empty_placement_for_reset(placement)
    _write(path, value)
    append_event(
        "interview_profile_edited",
        {
            "profile_revision": revision,
            "changed_fields": sorted(changes),
            "recommendations_invalidated": True,
            "attempt_evidence_deleted": False,
        },
    )
    return value


def normalize_profile_update(
    current: Mapping[str, object], changes: Mapping[str, object]
) -> dict[str, object]:
    """Validate a proposed edit without mutating placement or storage."""
    return _normalized_profile({**current, **changes})


def clear_profile(path: Path, append_event: EventAppender, *, now: Clock = _utcnow) -> None:
    value = load_profile(path)
    placement = value["placement"]
    assert isinstance(placement, dict)
    append_event(
        "interview_profile_cleared",
        {
            "profile_revision": value["profile_revision"],
            "placement_attempt_id": placement.get("attempt_id"),
            "attempt_evidence_deleted": False,
            "cleared_at": _timestamp(now),
        },
    )
    path.unlink()


def defer_placement(
    path: Path, append_event: EventAppender, *, now: Clock = _utcnow
) -> dict[str, object]:
    value = load_profile(path)
    placement = value["placement"]
    assert isinstance(placement, dict)
    if placement.get("status") == "in_progress":
        raise ValueError("discard or complete the in-progress placement before deferring")
    lifecycle_version, rubric_version = _placement_versions(placement)
    value["placement"] = _empty_placement_for_reset(placement)
    placement = value["placement"]
    assert isinstance(placement, dict)
    placement["status"] = "deferred"
    placement["updated_at"] = _timestamp(now)
    _write(path, value)
    append_event(
        "interview_placement_deferred",
        {
            "lifecycle_version": lifecycle_version,
            "rubric_version": rubric_version,
        },
    )
    return value


def start_placement(
    path: Path,
    *,
    activity_id: str,
    lifecycle_version: str = PLACEMENT_V3,
    rubric_version: str | None = None,
    now: Clock = _utcnow,
) -> dict[str, object]:
    if rubric_version is None:
        rubric_version = lifecycle_version
    candidate = {
        "lifecycle_version": lifecycle_version,
        "rubric_version": rubric_version,
    }
    _placement_versions(candidate)
    value = refresh_staleness(path, now=now)
    placement = value["placement"]
    assert isinstance(placement, dict)
    if placement.get("status") == "in_progress":
        return value
    timestamp = _timestamp(now)
    value["placement"] = {
        **_empty_placement(lifecycle_version),
        "rubric_version": rubric_version,
        "status": "in_progress",
        "attempt_id": f"interview_attempt_{uuid4().hex}",
        "activity_id": activity_id,
        "started_at": timestamp,
        "updated_at": timestamp,
        "profile_revision": value["profile_revision"],
        "problem_id": PLACEMENT_PROBLEM["problem_id"],
        "next_stage": placement_stages(candidate)[0],
    }
    value["recommendations"] = None
    _write(path, value)
    return value


_CONFIDENCE_UNIT_DEFINITIONS = (
    (
        "Arrays and Hashing",
        ("arrays_hashing",),
        "Use lookup, counting, sets, maps, and prefix techniques.",
        ("Hash maps", "Sets", "Frequency counting", "Prefix sums", "Narrating map tradeoffs"),
        "State why a map or set fits before writing code.",
    ),
    (
        "Two Pointers and Sliding Window",
        ("two_pointers", "sliding_window"),
        "Recognize boundary movement, invariants, and window state.",
        ("Two pointers", "Sliding window", "Window invariants", "Stating invariants aloud"),
        "Name the invariant and explain every pointer movement.",
    ),
    (
        "Stacks and Binary Search",
        ("stack", "binary_search"),
        "Apply stack structure and monotonic search-space reduction.",
        ("Stacks", "Monotonic stacks", "Binary search", "Boundary conditions", "Clarifying search boundaries"),
        "Confirm boundaries and narrate why the search space shrinks.",
    ),
    (
        "Linked Lists",
        ("linked_lists",),
        "Manipulate pointers safely and reason about cycles.",
        ("Pointer updates", "Reversal", "Fast and slow pointers", "Walking through pointer changes"),
        "Walk through pointer changes on a tiny example before coding.",
    ),
    (
        "Trees, Graphs, and Heaps",
        ("trees", "graphs", "heaps"),
        "Choose traversals and priority structures for connected problems.",
        ("DFS", "BFS", "Tree recursion", "Graph traversal", "Priority queues", "Choosing traversal aloud"),
        "Explain the traversal choice and what each queue or stack entry means.",
    ),
    (
        "Backtracking",
        ("backtracking",),
        "Model choices, constraints, pruning, and state restoration.",
        ("Decision trees", "Pruning", "State restoration", "Explaining pruning choices"),
        "Describe the choice, constraint, and undo step before implementation.",
    ),
    (
        "Dynamic Programming, Intervals, and Greedy",
        ("dynamic_programming", "intervals_greedy"),
        "Identify state transitions and justify locally optimal choices.",
        ("Dynamic programming", "Memoization", "Intervals", "Greedy reasoning", "Deriving recurrences aloud"),
        "Derive the state and transition aloud instead of jumping to a formula.",
    ),
)

_SYSTEM_DESIGN_UNIT_DEFINITIONS = (
    (
        "Requirements, Scale, and Interfaces",
        ("requirements_scope", "capacity_estimation", "api_design"),
        "Turn an open-ended prompt into explicit requirements, scale assumptions, and interfaces.",
        ("Functional requirements", "Quality attributes", "Capacity estimation", "API contracts", "Scoping aloud"),
        "State assumptions and ask which tradeoffs matter before drawing components.",
    ),
    (
        "Data Models and Storage",
        ("data_modeling", "databases_partitioning"),
        "Choose data models, storage systems, indexes, and partitioning strategies from access patterns.",
        ("Access patterns", "Data modeling", "Indexes", "Replication", "Partitioning", "Defending storage choices"),
        "Tie every storage choice to an access pattern and name its downside.",
    ),
    (
        "Caching and Content Delivery",
        ("caching_delivery",),
        "Place caches and delivery layers while reasoning about invalidation, freshness, and hotspots.",
        ("Cache placement", "Invalidation", "Freshness", "CDNs", "Explaining cache tradeoffs"),
        "Say what is cached, where it lives, and how it becomes stale.",
    ),
    (
        "Messaging and Asynchronous Workflows",
        ("messaging_async",),
        "Use queues, streams, and workers while handling ordering, retries, and idempotency.",
        ("Queues", "Streams", "Delivery semantics", "Idempotency", "Failure walkthroughs"),
        "Walk one request through retries and partial failure instead of describing only the happy path.",
    ),
    (
        "Reliability, Observability, and Tradeoffs",
        ("reliability_observability", "tradeoff_communication"),
        "Design degradation, recovery, and observability while communicating explicit tradeoffs.",
        ("Failure modes", "Redundancy", "Backpressure", "Observability", "Tradeoff summaries"),
        "Close each design section with the tradeoff chosen and the signal that would validate it.",
    ),
)

_ROLE_SPECIFIC_UNITS = {
    "backend": (
        "Backend Interview Foundations",
        "Practice API, database, concurrency, and service-boundary tradeoffs.",
        ("API design", "Data modeling", "Concurrency", "Service tradeoffs"),
    ),
    "frontend": (
        "Frontend Interview Foundations",
        "Practice browser, state, rendering, accessibility, and UI architecture tradeoffs.",
        ("Browser runtime", "State management", "Rendering", "Accessibility"),
    ),
    "mobile": (
        "Mobile Interview Foundations",
        "Practice lifecycle, offline data, platform, and client architecture tradeoffs.",
        ("App lifecycle", "Offline data", "Platform constraints", "Client architecture"),
    ),
    "data / ML": (
        "Data and ML Interview Foundations",
        "Practice SQL, data pipelines, model evaluation, and ML system tradeoffs.",
        ("SQL", "Data pipelines", "Model evaluation", "ML systems"),
    ),
}


def _confidence_emphasis(ratings: Mapping[str, int], patterns: tuple[str, ...]) -> str:
    rating = min((ratings.get(pattern, 1) for pattern in patterns), default=1)
    if rating <= 2:
        return "Learn"
    if rating == 3:
        return "Practice"
    if rating == 4:
        return "Review"
    return "Verify"


def confidence_outline_items(survey: Mapping[str, object] | None) -> list[dict[str, object]]:
    ratings_value = survey.get("ratings") if isinstance(survey, Mapping) else None
    ratings = (
        {str(key): int(value) for key, value in ratings_value.items()}
        if isinstance(ratings_value, Mapping)
        else {pattern_id: 1 for pattern_id in CONFIDENCE_PATTERN_IDS}
    )
    items: list[dict[str, object]] = [
        {
            "title": "Interview Communication and Problem Framing",
            "emphasis": "Practice",
            "slides": 3,
            "difficulty": 3,
            "outcome": "Clarify requirements, think aloud, surface edge cases, and frame concise behavioral examples.",
            "concepts": ("Clarifying requirements", "Think-aloud communication", "Edge cases", "Behavioral framing"),
            "interview_habit": "Use a clear opening routine in every interview round.",
        }
    ]
    focus = str(survey.get("interview_focus") or "coding") if survey else "coding"
    level = str(survey.get("target_level") or "entry") if survey else "entry"

    if focus == "system_design":
        items.append(
            {
                "title": "Coding Pattern Maintenance",
                "emphasis": "Review",
                "slides": 2,
                "difficulty": 5,
                "outcome": "Keep core pattern recognition, complexity, and testing ready for mixed interview loops.",
                "concepts": ("Pattern recognition", "Complexity analysis", "Testing strategy", "Concise solution narration"),
                "interview_habit": "Give a compact approach, complexity, and test plan before implementation.",
            }
        )
    else:
        for title, patterns, outcome, concepts, interview_habit in _CONFIDENCE_UNIT_DEFINITIONS:
            emphasis = _confidence_emphasis(ratings, patterns)
            slides = {"Learn": 5, "Practice": 4, "Review": 2, "Verify": 1}[emphasis]
            difficulty = max(3, min(9, 3 + len(patterns) + (1 if emphasis == "Learn" else 0)))
            items.append(
                {
                    "title": title,
                    "emphasis": emphasis,
                    "slides": slides,
                    "difficulty": difficulty,
                    "outcome": outcome,
                    "concepts": concepts,
                    "interview_habit": interview_habit,
                }
            )

    if focus in {"balanced", "system_design"}:
        for title, topics, outcome, concepts, interview_habit in _SYSTEM_DESIGN_UNIT_DEFINITIONS:
            emphasis = _confidence_emphasis(ratings, topics)
            slides = {"Learn": 5, "Practice": 4, "Review": 2, "Verify": 1}[emphasis]
            items.append(
                {
                    "title": title,
                    "emphasis": emphasis,
                    "slides": slides,
                    "difficulty": 7 if level in {"mid", "senior"} else 5,
                    "outcome": outcome,
                    "concepts": concepts,
                    "interview_habit": interview_habit,
                }
            )
    role = str(survey.get("role_family") or "general SWE") if survey else "general SWE"
    role_unit = _ROLE_SPECIFIC_UNITS.get(role)
    if role_unit is not None:
        title, outcome, concepts = role_unit
        items.append(
            {
                "title": title,
                "emphasis": "Practice",
                "slides": 3,
                "difficulty": 6 if level in {"mid", "senior"} else 4,
                "outcome": outcome,
                "concepts": (*concepts, "Role-specific behavioral examples"),
                "interview_habit": "Connect technical tradeoffs to role expectations and one concise experience story.",
            }
        )
    items.append(
        {
            "title": "Integrated Mock Interview Rounds",
            "emphasis": "Practice",
            "slides": 4,
            "difficulty": 7,
            "outcome": "Apply the technical and communication habits practiced throughout the course under realistic timing.",
            "concepts": ("Timed practice", "Testing under pressure", "Communication under pressure", "Self-review"),
            "interview_habit": "Use the same clarify, plan, implement, test, and summarize loop under time pressure.",
        }
    )
    return items


def confidence_outline(survey: Mapping[str, object] | None) -> str:
    role = str(survey.get("role_family") or "general SWE") if survey else "general SWE"
    level = str(survey.get("target_level") or "entry") if survey else "entry"
    focus = str(survey.get("interview_focus") or "coding") if survey else "coding"
    lines = [
        f"Scope: Technical interview preparation for a {level} {role} target.",
        "Excludes: Production coding fluency claims based only on self-report.",
        (
            "Assumptions: Confidence ratings tune lesson depth but do not mark mastery. "
            f"Interview focus is {focus.replace('_', ' ')}."
        ),
        "Units:",
    ]
    for index, item in enumerate(confidence_outline_items(survey), start=1):
        lines.append(
            f"{index}. {item['title']} ({item['slides']} slides, difficulty "
            f"{item['difficulty']}/10) - {item['outcome']} Emphasis: {item['emphasis']}."
        )
        lines.append("Concepts: " + "; ".join(str(value) for value in item["concepts"]))
        lines.append(f"Interview habit: {item['interview_habit']}")
    return "\n".join(lines)


def start_confidence_placement(
    path: Path, *, restart: bool = False, now: Clock = _utcnow
) -> dict[str, object]:
    value = refresh_staleness(path, now=now)
    placement = value["placement"]
    assert isinstance(placement, dict)
    if (
        placement.get("lifecycle_version") == PLACEMENT_V4
        and placement.get("status") == "in_progress"
        and not restart
    ):
        return value
    timestamp = _timestamp(now)
    value["placement"] = {
        **_empty_placement(PLACEMENT_V4),
        "status": "in_progress",
        "attempt_id": f"interview_attempt_{uuid4().hex}",
        "started_at": timestamp,
        "updated_at": timestamp,
        "profile_revision": value["profile_revision"],
        "problem_id": CONFIDENCE_SURVEY_ID,
        "next_stage": "confidence",
    }
    value["recommendations"] = None
    _write(path, value)
    return value


def save_confidence_survey(
    path: Path,
    *,
    role_family: str,
    target_level: str,
    interview_focus: str,
    ratings: Mapping[str, int],
    now: Clock = _utcnow,
) -> dict[str, object]:
    value = load_profile(path)
    placement = value["placement"]
    assert isinstance(placement, dict)
    lifecycle, _rubric = _placement_versions(placement)
    if (
        lifecycle != PLACEMENT_V4
        or placement.get("status") != "in_progress"
        or placement.get("next_stage") not in {"confidence", "outline"}
    ):
        raise ValueError("confidence placement is not ready for answers")
    survey: dict[str, object] = {
        "role_family": role_family,
        "target_level": target_level,
        "interview_focus": interview_focus,
        "ratings": dict(ratings),
        "outline": None,
    }
    _validate_confidence_survey(survey)
    survey["outline"] = confidence_outline(survey)
    profile = value["profile"]
    assert isinstance(profile, dict)
    profile_changed = (
        profile.get("role_family") != role_family
        or profile.get("target_level") != target_level
    )
    profile["role_family"] = role_family
    profile["target_level"] = target_level
    if profile_changed:
        revision = int(value["profile_revision"]) + 1
        value["profile_revision"] = revision
        placement["profile_revision"] = revision
    timestamp = _timestamp(now)
    value["updated_at"] = timestamp
    placement["survey"] = survey
    placement["next_stage"] = "outline"
    placement["updated_at"] = timestamp
    _write(path, value)
    return value


def _confidence_result(survey: Mapping[str, object] | None, *, skipped: bool) -> dict[str, object]:
    ratings_value = survey.get("ratings") if isinstance(survey, Mapping) else None
    ratings = ratings_value if isinstance(ratings_value, Mapping) else {}
    focus = str(survey.get("interview_focus") or "coding") if survey else "coding"
    topics = confidence_topics_for_focus(focus)
    _lowest_index, (_lowest_id, lowest_label) = min(
        enumerate(topics),
        key=lambda indexed: (int(ratings.get(indexed[1][0], 1)), indexed[0]),
    )
    uncertainty = [
        "Confidence ratings guide curriculum emphasis and do not establish mastery.",
        "Understanding will be verified through retrieval and implementation during the course.",
    ]
    if skipped:
        uncertainty.insert(0, "Placement was skipped, so the baseline outline is intentionally broad.")
    uncertain_gap = {
        "status": "uncertain",
        "evidence": [],
        "note": "Self-report is planning context only and will be verified during practice.",
    }
    return {
        "provisional": True,
        "starting_level": "learner-selected-baseline" if skipped else "confidence-guided",
        "mastery_update_applied": False,
        "patterns_marked_known": [],
        "gaps": {
            "prerequisites": dict(uncertain_gap),
            "coding_fluency": dict(uncertain_gap),
            "reasoning": dict(uncertain_gap),
            "interview_process": dict(uncertain_gap),
        },
        "uncertainty": uncertainty,
        "passport": {
            "starting_route": "Broad baseline" if skipped else "Confidence-guided pattern practice",
            "first_activity": "Clarifying requirements",
            "reasoning_signals": ["Learner confidence profile recorded" if not skipped else "Placement skipped"],
            "practice_priority": f"Verify {lowest_label} through guided practice.",
            "uncertainty_to_verify": "Pattern fluency must be demonstrated in later coding practice.",
        },
    }


def confirm_confidence_placement(
    path: Path,
    *,
    outline: str,
    now: Clock = _utcnow,
) -> dict[str, object]:
    value = load_profile(path)
    placement = value["placement"]
    assert isinstance(placement, dict)
    lifecycle, _rubric = _placement_versions(placement)
    survey = placement.get("survey")
    if (
        lifecycle != PLACEMENT_V4
        or placement.get("status") != "in_progress"
        or placement.get("next_stage") != "outline"
        or not isinstance(survey, dict)
    ):
        raise ValueError("confidence placement outline is not ready")
    normalized_outline = outline.strip()
    survey["outline"] = normalized_outline
    _validate_confidence_survey(survey)
    timestamp = _timestamp(now)
    placement["status"] = "provisional"
    placement["next_stage"] = None
    placement["updated_at"] = timestamp
    placement["completed_at"] = timestamp
    placement["result"] = _confidence_result(survey, skipped=False)
    value["recommendations"] = _recommendations(value, current_date=now().date())
    _write(path, value)
    return value


def skip_confidence_placement(path: Path, *, now: Clock = _utcnow) -> dict[str, object]:
    value = load_profile(path)
    timestamp = _timestamp(now)
    placement = {
        **_empty_placement(PLACEMENT_V4),
        "status": "provisional",
        "attempt_id": f"interview_attempt_{uuid4().hex}",
        "started_at": timestamp,
        "updated_at": timestamp,
        "completed_at": timestamp,
        "profile_revision": value["profile_revision"],
        "problem_id": CONFIDENCE_SURVEY_ID,
        "result": _confidence_result(None, skipped=True),
    }
    value["placement"] = placement
    value["recommendations"] = _recommendations(value, current_date=now().date())
    _write(path, value)
    return value


def discard_placement(
    path: Path, append_event: EventAppender, *, now: Clock = _utcnow
) -> dict[str, object]:
    value = load_profile(path)
    placement = value["placement"]
    assert isinstance(placement, dict)
    if placement.get("status") != "in_progress":
        raise ValueError("there is no in-progress placement to discard")
    attempt_id = placement.get("attempt_id")
    refs = list(placement.get("evidence_refs", []))
    value["placement"] = _empty_placement_for_reset(placement)
    value["recommendations"] = None
    _write(path, value)
    append_event(
        "interview_placement_state_discarded",
        {
            "attempt_id": attempt_id,
            "activity_id": placement.get("activity_id"),
            "evidence_refs": refs,
            "attempt_evidence_deleted": False,
            "discarded_at": _timestamp(now),
        },
    )
    return value


def _load_v3_placement(path: Path) -> tuple[dict[str, object], dict[str, object]]:
    value = load_profile(path)
    placement = value["placement"]
    assert isinstance(placement, dict)
    lifecycle_version, _rubric_version = _placement_versions(placement)
    if lifecycle_version != PLACEMENT_V3:
        raise ValueError("placement drafts are available only for v3 placement")
    return value, placement


def placement_draft(path: Path) -> dict[str, object] | None:
    """Load a copy of the active v3 draft, if one exists."""
    _value, placement = _load_v3_placement(path)
    draft = placement.get("draft")
    return draft if isinstance(draft, dict) else None


def append_placement_draft_line(
    path: Path,
    stage: str,
    line: str,
    *,
    now: Clock = _utcnow,
) -> dict[str, object]:
    """Durably append one bounded line without publishing or advancing placement."""
    value, placement = _load_v3_placement(path)
    if placement.get("status") != "in_progress":
        raise ValueError("placement is not in progress")
    expected = placement.get("next_stage")
    if stage != expected:
        raise ValueError(f"expected {expected} draft, received {stage}")
    if not isinstance(line, str) or not line.strip():
        raise ValueError("placement draft line must be non-empty")
    normalized = line.strip()
    if len(normalized) > DRAFT_MAX_LINE_LENGTH:
        raise ValueError("placement draft line is too large")
    draft = placement.get("draft")
    if draft is None:
        lines: list[str] = []
    else:
        assert isinstance(draft, dict)
        if draft.get("stage") != stage:
            raise ValueError("placement draft does not match the active stage")
        stored_lines = draft.get("lines")
        assert isinstance(stored_lines, list)
        lines = stored_lines
    if len(lines) >= DRAFT_MAX_LINES or len("\n".join([*lines, normalized])) > DRAFT_MAX_LENGTH:
        raise ValueError("placement draft is too large")
    timestamp = _timestamp(now)
    placement["draft"] = {
        "stage": stage,
        "lines": [*lines, normalized],
        "updated_at": timestamp,
    }
    placement["updated_at"] = timestamp
    _write(path, value)
    return value


def replace_placement_draft_lines(
    path: Path,
    stage: str,
    lines: list[str],
    *,
    now: Clock = _utcnow,
) -> dict[str, object]:
    """Replace the active v3 draft with bounded lines in one durable write."""
    value, placement = _load_v3_placement(path)
    if placement.get("status") != "in_progress" or placement.get("next_stage") != stage:
        raise ValueError(f"expected {placement.get('next_stage')} draft, received {stage}")
    normalized = [line.strip() for line in lines if isinstance(line, str) and line.strip()]
    if not normalized:
        raise ValueError("placement draft must contain at least one line")
    if (
        len(normalized) > DRAFT_MAX_LINES
        or any(len(line) > DRAFT_MAX_LINE_LENGTH for line in normalized)
        or len("\n".join(normalized)) > DRAFT_MAX_LENGTH
    ):
        raise ValueError("placement draft is too large")
    timestamp = _timestamp(now)
    placement["draft"] = {
        "stage": stage,
        "lines": normalized,
        "updated_at": timestamp,
    }
    placement["updated_at"] = timestamp
    _write(path, value)
    return value


def undo_placement_draft_line(
    path: Path,
    stage: str,
    *,
    now: Clock = _utcnow,
) -> dict[str, object]:
    """Remove only the most recent line from the matching active v3 draft."""
    value, placement = _load_v3_placement(path)
    if placement.get("status") != "in_progress" or placement.get("next_stage") != stage:
        raise ValueError(f"expected {placement.get('next_stage')} draft, received {stage}")
    draft = placement.get("draft")
    if not isinstance(draft, dict) or draft.get("stage") != stage:
        return value
    lines = draft.get("lines")
    assert isinstance(lines, list)
    timestamp = _timestamp(now)
    placement["draft"] = (
        {"stage": stage, "lines": lines[:-1], "updated_at": timestamp} if len(lines) > 1 else None
    )
    placement["updated_at"] = timestamp
    _write(path, value)
    return value


def clear_placement_draft(
    path: Path,
    stage: str,
    *,
    now: Clock = _utcnow,
) -> dict[str, object]:
    """Clear only a draft that matches the active v3 stage."""
    value, placement = _load_v3_placement(path)
    if placement.get("status") != "in_progress" or placement.get("next_stage") != stage:
        raise ValueError(f"expected {placement.get('next_stage')} draft, received {stage}")
    draft = placement.get("draft")
    if not isinstance(draft, dict):
        return value
    if draft.get("stage") != stage:
        raise ValueError("placement draft does not match the active stage")
    placement["draft"] = None
    placement["updated_at"] = _timestamp(now)
    _write(path, value)
    return value


def placement_evidence_id(placement: Mapping[str, object], stage: str) -> str:
    """Return the stable event identity for one v3 attempt-stage publication."""
    lifecycle_version, _rubric_version = _placement_versions(placement)
    if lifecycle_version != PLACEMENT_V3:
        raise ValueError("deterministic placement evidence IDs are v3-only")
    attempt_id = placement.get("attempt_id")
    if (
        not isinstance(attempt_id, str)
        or re.fullmatch(r"interview_attempt_[a-f0-9]{32}", attempt_id) is None
    ):
        raise ValueError("placement attempt reference is invalid")
    if stage not in placement_stages(placement):
        raise ValueError("placement stage is invalid")
    digest = hashlib.sha256(
        f"openlearn-placement-evidence-v3\0{attempt_id}\0{stage}".encode()
    ).hexdigest()[:32]
    return f"evidence_{digest}"


def record_placement_evidence(
    path: Path,
    stage: str,
    response: str,
    *,
    evidence_id: str,
    now: Clock = _utcnow,
) -> dict[str, object]:
    value = load_profile(path)
    placement = value["placement"]
    assert isinstance(placement, dict)
    lifecycle_version, _rubric_version = _placement_versions(placement)
    refs = placement.get("evidence_refs")
    assert isinstance(refs, list)
    if lifecycle_version == PLACEMENT_V3:
        expected_id = placement_evidence_id(placement, stage)
        if evidence_id != expected_id:
            raise ValueError("v3 placement evidence ID must be deterministic")
        if any(ref.get("evidence_id") == evidence_id for ref in refs):
            draft = placement.get("draft")
            if isinstance(draft, dict) and draft.get("stage") == stage:
                placement["draft"] = None
                placement["updated_at"] = _timestamp(now)
                _write(path, value)
            return value
    if placement.get("status") != "in_progress":
        raise ValueError("placement is not in progress")
    expected = placement.get("next_stage")
    if stage != expected:
        raise ValueError(f"expected {expected} evidence, received {stage}")
    if not isinstance(response, str) or not response.strip():
        raise ValueError("placement evidence must be non-empty")
    response = response.strip()
    if len(response) > 40_000:
        raise ValueError("placement evidence is too large")
    refs.append(
        {
            "evidence_id": evidence_id,
            "event_type": "activity_evidence_recorded",
        }
    )
    observations = placement.get("observations")
    assert isinstance(observations, dict)
    profile = value["profile"]
    assert isinstance(profile, dict)
    observations[stage] = _evidence_observation(
        stage,
        response,
        coding_language=str(profile.get("coding_language") or ""),
        rubric_version=str(placement["rubric_version"]),
    )
    if lifecycle_version == PLACEMENT_V3:
        draft = placement.get("draft")
        if isinstance(draft, dict) and draft.get("stage") != stage:
            raise ValueError("placement draft does not match published stage")
        placement["draft"] = None
    placement["updated_at"] = _timestamp(now)
    stages = placement_stages(placement)
    index = stages.index(stage)
    if index + 1 < len(stages):
        placement["next_stage"] = stages[index + 1]
    else:
        _complete_placement(value, now=now)
    _write(path, value)
    return value


def skip_optional_placement_stage(
    path: Path,
    stage: str,
    *,
    now: Clock = _utcnow,
) -> dict[str, object]:
    """Complete an optional stage without inventing evidence or observations."""
    value = load_profile(path)
    placement = value["placement"]
    assert isinstance(placement, dict)
    if placement.get("status") == "provisional":
        observations = placement.get("observations")
        if (
            stage in placement_optional_stages(placement)
            and isinstance(observations, dict)
            and stage not in observations
        ):
            return value
    if placement.get("status") != "in_progress":
        raise ValueError("placement is not in progress")
    if placement.get("next_stage") != stage:
        raise ValueError(f"expected {placement.get('next_stage')} evidence, received {stage}")
    if stage not in placement_optional_stages(placement):
        raise ValueError("placement stage is not optional")
    stages = placement_stages(placement)
    index = stages.index(stage)
    if placement.get("lifecycle_version") == PLACEMENT_V3:
        placement["draft"] = None
    if index + 1 < len(stages):
        placement["next_stage"] = stages[index + 1]
        placement["updated_at"] = _timestamp(now)
    else:
        _complete_placement(value, now=now)
    _write(path, value)
    return value


def _complete_placement(value: dict[str, object], *, now: Clock) -> None:
    placement = value["placement"]
    assert isinstance(placement, dict)
    observations = placement["observations"]
    assert isinstance(observations, dict)
    timestamp = _timestamp(now)
    placement["next_stage"] = None
    placement["status"] = "provisional"
    placement["updated_at"] = timestamp
    placement["completed_at"] = timestamp
    placement["result"] = _provisional_result(
        observations, rubric_version=str(placement["rubric_version"])
    )
    value["recommendations"] = _recommendations(value, current_date=now().date())


def complete_with_baseline(
    path: Path,
    *,
    evidence_id: str,
    reason: str,
    now: Clock = _utcnow,
) -> dict[str, object]:
    value = load_profile(path)
    placement = value["placement"]
    assert isinstance(placement, dict)
    if placement.get("status") != "in_progress":
        raise ValueError("placement is not in progress")
    lifecycle_version, _rubric_version = _placement_versions(placement)
    if lifecycle_version == PLACEMENT_V3:
        raise ValueError("baseline completion is available only for legacy placement")
    refs = placement["evidence_refs"]
    observations = placement["observations"]
    assert isinstance(refs, list) and isinstance(observations, dict)
    refs.append(
        {
            "evidence_id": evidence_id,
            "event_type": "activity_evidence_recorded",
        }
    )
    observations["baseline"] = {
        "status": "uncertain",
        "substantive": bool(reason.strip()),
        "skipped": False,
        "non_attempt": False,
        "signals": ["learner_selected_baseline"],
    }
    timestamp = _timestamp(now)
    placement["status"] = "provisional"
    placement["next_stage"] = None
    placement["updated_at"] = timestamp
    placement["completed_at"] = timestamp
    result = _provisional_result(observations, rubric_version=str(placement["rubric_version"]))
    result["starting_level"] = "learner-selected-baseline"
    uncertainty = result["uncertainty"]
    assert isinstance(uncertainty, list)
    uncertainty.insert(
        0,
        "The learner selected a reduced-demand baseline before full placement evidence.",
    )
    placement["result"] = result
    value["recommendations"] = _recommendations(value, current_date=now().date())
    _write(path, value)
    return value


def _evidence_observation(
    stage: str,
    response: str,
    *,
    coding_language: str,
    rubric_version: str = PLACEMENT_V1,
) -> dict[str, object]:
    if rubric_version not in PLACEMENT_RUBRICS:
        raise ValueError("interview-prep placement rubric is unsupported")
    execution = _parse_placement_execution_evidence(response)
    normalized = response.lower()
    skipped = execution is None and (
        "skipped" in normalized or "less demanding baseline" in normalized
    )
    non_attempt = execution is None and bool(
        re.search(
            r"\b(?:i\s+(?:do not|don't|cannot|can't|could not|couldn't)\s+"
            r"(?:know|do|answer|implement|explain)|no idea|unable to|not sure how)\b",
            normalized,
        )
    )
    signals: list[str] = []
    word_count = len(re.findall(r"\b[\w'-]+\b", normalized))
    clarification_topic = re.search(
        r"\b(?:input|output|text|width|index|constraint|edge|empty|zero|negative|case|"
        r"unicode|duplicate|return)\b",
        normalized,
    )
    if (
        not skipped
        and not non_attempt
        and stage == "clarification"
        and "?" in response
        and word_count >= 3
        and clarification_topic
    ):
        signals.append("asked_clarifying_question")
    if (
        not skipped
        and not non_attempt
        and stage == "plan"
        and re.search(
            r"\b(?:set|dict|dictionary|map|hashmap|window|index)\b", normalized
        )
    ):
        signals.append("named_data_structure_or_strategy")
    if execution is not None:
        signals.append("execution_evidence_received")
        signals.append("execution_passed" if execution["tests_passed"] else "execution_failed")
    unsupported_implementation_language = (
        stage == "implementation" and coding_language.strip().lower() != "python"
    )
    if (
        not skipped
        and not non_attempt
        and stage == "implementation"
        and not unsupported_implementation_language
    ):
        implementation_source = str(execution["source"]) if execution is not None else response
        try:
            tree = ast.parse(implementation_source)
        except SyntaxError:
            tree = None
        functions = (
            [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
            if tree is not None
            else []
        )
        if functions:
            signals.append("produced_function")
        if any(
            isinstance(node, ast.Return) and node.value is not None
            for function in functions
            for node in ast.walk(function)
        ):
            signals.append("produced_return_path")
    if unsupported_implementation_language:
        signals.append("unsupported_language_for_rubric")
    if (
        not skipped
        and not non_attempt
        and stage == "complexity"
        and re.search(r"\bo\s*\([^)]{1,30}\)", normalized)
    ):
        signals.append("stated_complexity")
    if not skipped and not non_attempt and stage == "follow_up" and len(response.split()) >= 5:
        signals.append("engaged_with_follow_up")
    if not skipped and not non_attempt and stage == "conversation" and len(response.split()) >= 3:
        signals.append("engaged_in_conversation")
    if not skipped and not non_attempt and stage == "debrief" and len(response.split()) >= 5:
        signals.append("engaged_in_debrief")
    if not skipped and not non_attempt and stage == "reasoning":
        strategy = re.search(r"\b(?:sliding window|two pointers?)\b", normalized)
        structured_storage = re.search(
            r"\b(?:use|using|track|store|maintain)\b.{0,40}"
            r"\b(?:set|dict|dictionary|map|hashmap)\b",
            normalized,
        )
        if word_count >= 5 and (strategy or structured_storage):
            signals.append("named_data_structure_or_strategy")
        if re.search(
            r"\b(?:edges?|edge cases?|empty|invalid|duplicate|nonpositive|no[- ]match|"
            r"width (?:of )?(?:zero|one)|width (?:larger|greater))\b",
            normalized,
        ):
            signals.append("covered_edges_and_tests")
        if re.search(
            r"(?:\bo\s*\([^)]{1,30}\)\s+time\b|\btime(?: complexity)?\s*(?:is|of|:)"
            r"?\s*o\s*\([^)]{1,30}\)|\b(?:constant|linear|logarithmic|quadratic)"
            r"\s+time\b)",
            normalized,
        ):
            signals.append("stated_time_complexity")
        if re.search(
            r"(?:\bo\s*\([^)]{1,30}\)\s+(?:extra\s+)?space\b|\bspace(?: complexity)?"
            r"\s*(?:is|of|:)?\s*o\s*\([^)]{1,30}\)|\b(?:constant|linear|logarithmic|"
            r"quadratic)\s+(?:extra\s+)?space\b)",
            normalized,
        ):
            signals.append("stated_space_complexity")
    required_by_stage = {
        "clarification": {"asked_clarifying_question"},
        "plan": {"named_data_structure_or_strategy"},
        "complexity": {"stated_complexity"},
        "follow_up": {"engaged_with_follow_up"},
        "conversation": {"engaged_in_conversation"},
        "debrief": {"engaged_in_debrief"},
        "reasoning": {
            "named_data_structure_or_strategy",
            "covered_edges_and_tests",
            "stated_time_complexity",
            "stated_space_complexity",
        },
    }
    required = required_by_stage.get(stage, set())
    if skipped or unsupported_implementation_language:
        status = "uncertain"
    elif non_attempt:
        status = "not_observed"
    elif stage == "implementation":
        status = (
            "observed"
            if execution is not None
            and execution["tests_passed"] is True
            and {"produced_function", "produced_return_path"} <= set(signals)
            else "not_observed"
        )
    elif stage == "tests":
        status = (
            "observed"
            if execution is not None and execution["tests_passed"] is True
            else "not_observed"
        )
    else:
        status = "not_observed" if required and not required <= set(signals) else "observed"
    return {
        "status": status,
        "substantive": not skipped and not non_attempt and len(response.split()) >= 3,
        "skipped": skipped,
        "non_attempt": non_attempt,
        "signals": signals,
    }


def _observation_status(observations: Mapping[str, object], stage: str) -> str:
    value = observations.get(stage)
    if isinstance(value, dict) and value.get("status") in {
        "observed",
        "not_observed",
        "uncertain",
    }:
        return str(value["status"])
    return "uncertain"


def placement_feedback(placement: Mapping[str, object]) -> dict[str, object] | None:
    """Return concise learner-facing coaching from durable v3 reasoning signals."""
    lifecycle_version, _rubric_version = _placement_versions(placement)
    if lifecycle_version != PLACEMENT_V3 or placement.get("status") not in {
        "in_progress",
        "provisional",
    }:
        return None
    observations = placement.get("observations")
    if not isinstance(observations, Mapping):
        return None
    clarification = observations.get("clarification")
    clarification_signals = (
        set(str(signal) for signal in clarification.get("signals", []))
        if isinstance(clarification, Mapping)
        else set()
    )
    asked_clarification = "asked_clarifying_question" in clarification_signals
    if placement.get("status") == "in_progress":
        if placement.get("next_stage") != "reasoning" or clarification is None:
            return None
        return {
            "title": (
                "Good clarification habit"
                if asked_clarification
                else "Sharpen your clarification"
            ),
            "strengths": (
                ["You asked a direct clarifying question before choosing an approach."]
                if asked_clarification
                else []
            ),
            "improvement": (
                "Next, name the data structure, the window invariant, important edge cases, "
                "and the expected time and space cost."
                if asked_clarification
                else "Ask one direct question about inputs, indexing, constraints, or edge "
                "cases before committing to an approach."
            ),
            "next_step": "Explain your approach",
        }

    result = placement.get("result")
    passport = result.get("passport") if isinstance(result, Mapping) else None
    reasoning = observations.get("reasoning")
    reasoning_signals = (
        set(str(signal) for signal in reasoning.get("signals", []))
        if isinstance(reasoning, Mapping)
        else set()
    )
    strengths = [
        label
        for signal, label in PLACEMENT_V3_SIGNAL_LABELS.items()
        if signal in reasoning_signals
    ]
    if asked_clarification:
        strengths.insert(0, "Asked a direct clarifying question")
    improvement_by_signal = (
        (
            "named_data_structure_or_strategy",
            "Name the data structure and the invariant that keeps the window valid.",
        ),
        (
            "covered_edges_and_tests",
            "Call out invalid widths, duplicate characters, and at least one no-match test.",
        ),
        (
            "stated_time_complexity",
            "State the time complexity and explain why the window processes each character "
            "only a bounded number of times.",
        ),
        (
            "stated_space_complexity",
            "State the space complexity in terms of the tracked window or character set.",
        ),
    )
    improvement = next(
        (message for signal, message in improvement_by_signal if signal not in reasoning_signals),
        str(passport.get("practice_priority"))
        if isinstance(passport, Mapping)
        else "Turn the approach into a complete tested implementation during practice.",
    )
    next_step = (
        str(passport.get("first_activity"))
        if isinstance(passport, Mapping)
        else "Start the baseline lesson"
    )
    return {
        "title": "Your reasoning snapshot",
        "strengths": strengths,
        "improvement": improvement,
        "next_step": next_step,
    }


def _axis_status(observations: Mapping[str, object], stages: tuple[str, ...]) -> str:
    statuses = {_observation_status(observations, stage) for stage in stages}
    if statuses == {"observed"}:
        return "observed"
    if "not_observed" in statuses:
        return "not_observed"
    return "uncertain"


def _provisional_result(
    observations: Mapping[str, object], *, rubric_version: str = PLACEMENT_V1
) -> dict[str, object]:
    rubric = PLACEMENT_RUBRICS.get(rubric_version)
    if rubric is None:
        raise ValueError("interview-prep placement rubric is unsupported")
    axes = rubric["axes"]
    assert isinstance(axes, dict)
    axis_statuses = {
        axis: _axis_status(observations, tuple(stages)) for axis, stages in axes.items()
    }
    evidence = rubric["evidence"]
    assert isinstance(evidence, dict)
    evidence_by_axis = {axis: list(stages) for axis, stages in evidence.items()}
    not_observed_count = sum(status == "not_observed" for status in axis_statuses.values())
    uncertain_count = sum(status == "uncertain" for status in axis_statuses.values())
    coding_note = (
        "Coding fluency was not observed in this reasoning-only placement and must "
        "be verified during later course practice."
        if rubric_version == PLACEMENT_V3
        else "Placement observes production fluency but does not establish mastery."
    )
    gaps: dict[str, dict[str, object]] = {
        "prerequisites": {
            "status": axis_statuses["prerequisites"],
            "evidence": evidence_by_axis["prerequisites"],
            "note": "Data-structure prerequisites need confirmation through later retrieval.",
        },
        "coding_fluency": {
            "status": axis_statuses["coding_fluency"],
            "evidence": evidence_by_axis["coding_fluency"],
            "note": coding_note,
        },
        "reasoning": {
            "status": axis_statuses["reasoning"],
            "evidence": evidence_by_axis["reasoning"],
            "note": "Reasoning remains provisional until repeated on a novel problem.",
        },
        "interview_process": {
            "status": axis_statuses["interview_process"],
            "evidence": evidence_by_axis["interview_process"],
            "note": "Interview-format familiarity is separate from prerequisite knowledge.",
        },
    }
    result: dict[str, object] = {
        "provisional": True,
        "starting_level": (
            "foundational"
            if not_observed_count >= 3
            else "developing"
            if not_observed_count
            else "uncertain-baseline"
            if uncertain_count
            else "observed-interview-baseline"
        ),
        "mastery_update_applied": False,
        "patterns_marked_known": [],
        "gaps": gaps,
        "uncertainty": [
            "One bounded problem cannot establish durable mastery.",
            "Self-report is context only; recommendations rely on observed evidence.",
        ],
    }
    if rubric_version == PLACEMENT_V3:
        reasoning = observations.get("reasoning")
        observed_signals = list(reasoning.get("signals", [])) if isinstance(reasoning, dict) else []
        reasoning_signals = [
            PLACEMENT_V3_SIGNAL_LABELS[signal]
            for signal in observed_signals
            if signal in PLACEMENT_V3_SIGNAL_LABELS
        ]
        if not reasoning_signals:
            reasoning_signals = ["Reasoning needs confirmation during guided practice"]
        strong_reasoning = len(reasoning_signals) == len(PLACEMENT_V3_SIGNAL_LABELS)
        result["starting_level"] = (
            "developing-reasoning" if strong_reasoning else "foundational-reasoning"
        )
        uncertainty = result["uncertainty"]
        assert isinstance(uncertainty, list)
        uncertainty.append(
            "Coding fluency remains unobserved until a later unaided implementation."
        )
        result["passport"] = {
            "starting_route": ("Pattern practice" if strong_reasoning else "Interview foundations"),
            "first_activity": "Sliding Window Foundations",
            "reasoning_signals": reasoning_signals,
            "practice_priority": (
                "Turn the proposed approach into a complete tested implementation."
                if strong_reasoning
                else "Practice structuring an approach with edge cases and complexity."
            ),
            "uncertainty_to_verify": (
                "Implement and test a complete solution without autocomplete."
            ),
        }
    return result


def practice_schedule(profile: Mapping[str, object]) -> tuple[int, int, int]:
    weekly = profile["weekly_minutes"]
    requested_session = profile["session_minutes"]
    assert isinstance(weekly, int) and isinstance(requested_session, int)
    sessions = max(1, (weekly + requested_session - 1) // requested_session)
    session = (weekly + sessions - 1) // sessions
    return weekly, session, sessions


def _recommendations(value: Mapping[str, object], *, current_date: date) -> dict[str, object]:
    profile = value["profile"]
    assert isinstance(profile, dict)
    scheduled, session, sessions = practice_schedule(profile)
    role = str(profile.get("role_family") or "general SWE")
    level = str(profile.get("target_level") or "target")
    interview_date = str(profile.get("interview_date") or "")
    horizon_days: int | None = None
    if interview_date:
        try:
            horizon_days = (date.fromisoformat(interview_date) - current_date).days
        except ValueError:
            horizon_days = None
    horizon = (
        "open-ended"
        if horizon_days is None
        else "urgent"
        if horizon_days <= 28
        else "near-term"
        if horizon_days <= 84
        else "long-range"
    )
    placement = value["placement"]
    assert isinstance(placement, dict)
    result = placement.get("result")
    gaps = result.get("gaps") if isinstance(result, dict) else {}
    axis_labels = {
        "prerequisites": "Rebuild the prerequisite data structures needed for the target bar.",
        "coding_fluency": "Practice producing and testing complete code in the preferred language.",
        "reasoning": "Make plans, invariants, and complexity tradeoffs explicit on novel problems.",
        "interview_process": "Rehearse clarification and follow-up discussion in interview format.",
    }
    priorities = [
        text
        for axis, text in axis_labels.items()
        if not isinstance(gaps, dict)
        or not isinstance(gaps.get(axis), dict)
        or gaps[axis].get("status") != "observed"
    ]
    if horizon == "urgent":
        process = axis_labels["interview_process"]
        priorities = [process, *[item for item in priorities if item != process]]
    if not priorities:
        priorities = [f"Raise transfer difficulty toward the {role} {level} interview bar."]
    return {
        "profile_revision": value["profile_revision"],
        "weekly_minutes": scheduled,
        "session_minutes": session,
        "sessions_per_week": sessions,
        "target": f"{role} at {level} bar",
        "horizon": horizon,
        "priorities": priorities[:3],
    }


def project_staleness(value: Mapping[str, object], *, now: Clock = _utcnow) -> dict[str, object]:
    """Return the current stale projection without mutating caller state or storage."""
    projected = copy.deepcopy(dict(value))
    placement = projected["placement"]
    assert isinstance(placement, dict)
    completed = placement.get("completed_at")
    stale = placement.get("profile_revision") != projected.get("profile_revision")
    if isinstance(completed, str):
        try:
            completed_at = datetime.fromisoformat(completed.replace("Z", "+00:00"))
        except ValueError:
            stale = True
        else:
            stale = stale or (now().astimezone(timezone.utc) - completed_at).days > STALE_AFTER_DAYS
    if stale and placement.get("status") == "provisional":
        placement["status"] = "stale"
        projected["recommendations"] = None
    return projected


def refresh_staleness(path: Path, *, now: Clock = _utcnow) -> dict[str, object]:
    value = load_profile(path)
    projected = project_staleness(value, now=now)
    if projected != value:
        _write(path, projected)
    return projected
