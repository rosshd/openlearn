"""Local interview-prep profile and coding-placement lifecycle."""

from __future__ import annotations

import ast
import copy
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

PROFILE_SCHEMA_VERSION = 1
PLACEMENT_RUBRIC_VERSION = "coding-placement-v1"
STALE_AFTER_DAYS = 90
PLACEMENT_STAGES = (
    "calibration",
    "clarification",
    "plan",
    "implementation",
    "tests",
    "complexity",
    "follow_up",
)
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
}

PLACEMENT_ASSUMPTION_CARD = (
    "Interviewer assumptions:\n"
    "- `text` is a Python string and `width` is an integer.\n"
    "- Return the zero-based start index of the first qualifying window.\n"
    "- Return -1 when no qualifying window exists, including nonpositive widths "
    "and widths larger than the text.\n"
    "- Character comparison uses Python string characters exactly as provided."
)

Clock = Callable[[], datetime]
EventAppender = Callable[[str, dict[str, object]], None]


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
        raise ValueError(
            "interview-prep placement does not match the current profile revision"
        )
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


def _empty_placement() -> dict[str, object]:
    return {
        "status": "not_started",
        "rubric_version": PLACEMENT_RUBRIC_VERSION,
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
    if set(placement) != set(_empty_placement()):
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
    if placement.get("rubric_version") != PLACEMENT_RUBRIC_VERSION:
        raise ValueError("interview-prep placement rubric is unsupported")
    activity_id = placement.get("activity_id")
    if activity_id is not None and (
        not isinstance(activity_id, str)
        or re.fullmatch(r"act_[a-f0-9]{32}", activity_id) is None
    ):
        raise ValueError("interview-prep placement activity reference is invalid")
    attempt_id = placement.get("attempt_id")
    if attempt_id is not None and (
        not isinstance(attempt_id, str)
        or re.fullmatch(r"interview_attempt_[a-f0-9]{32}", attempt_id) is None
    ):
        raise ValueError("interview-prep placement attempt reference is invalid")
    for key in ("started_at", "updated_at", "completed_at"):
        _validated_timestamp(
            placement.get(key), f"placement {key}", optional=True
        )
    placement_revision = placement.get("profile_revision")
    if placement_revision is not None and (
        not isinstance(placement_revision, int)
        or isinstance(placement_revision, bool)
        or placement_revision < 1
    ):
        raise ValueError("interview-prep placement profile revision is invalid")
    problem_id = placement.get("problem_id")
    if problem_id is not None and problem_id != PLACEMENT_PROBLEM["problem_id"]:
        raise ValueError("interview-prep placement problem reference is invalid")
    next_stage = placement.get("next_stage")
    if next_stage is not None and next_stage not in PLACEMENT_STAGES:
        raise ValueError("interview-prep placement next stage is invalid")
    refs = placement.get("evidence_refs")
    if not isinstance(refs, list) or len(refs) > len(PLACEMENT_STAGES) + 1:
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
    observations = placement.get("observations")
    if not isinstance(observations, dict) or not set(observations) <= {
        *PLACEMENT_STAGES,
        "baseline",
    }:
        raise ValueError("interview-prep placement observations are invalid")
    for stage, observation in observations.items():
        if (
            not isinstance(observation, dict)
            or set(observation)
            != {"status", "substantive", "skipped", "non_attempt", "signals"}
            or observation.get("status")
            not in {"observed", "not_observed", "uncertain"}
            or not isinstance(observation.get("signals"), list)
            or not all(isinstance(item, str) for item in observation["signals"])
            or not isinstance(observation.get("substantive"), bool)
            or not isinstance(observation.get("skipped"), bool)
            or not isinstance(observation.get("non_attempt"), bool)
        ):
            raise ValueError(
                f"interview-prep placement observation for {stage} is malformed"
            )
    result = placement.get("result")
    if result is not None:
        _validate_result(result)
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
        activity_id is None
        or attempt_id is None
        or next_stage is None
        or result is not None
        or placement.get("completed_at") is not None
    ):
        raise ValueError("interview-prep active placement state is inconsistent")
    if status in {"provisional", "stale"} and (
        activity_id is None
        or attempt_id is None
        or next_stage is not None
        or result is None
        or not isinstance(placement.get("completed_at"), str)
    ):
        raise ValueError("interview-prep completed placement state is inconsistent")


def _validate_result(result: object) -> None:
    if not isinstance(result, dict) or set(result) != {
        "provisional",
        "starting_level",
        "mastery_update_applied",
        "patterns_marked_known",
        "gaps",
        "uncertainty",
    }:
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


def _validate_recommendations(
    recommendations: object, *, expected_revision: int
) -> None:
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
        value["placement"] = _empty_placement()
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


def clear_profile(
    path: Path, append_event: EventAppender, *, now: Clock = _utcnow
) -> None:
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
    placement.update(_empty_placement())
    placement["status"] = "deferred"
    placement["updated_at"] = _timestamp(now)
    _write(path, value)
    append_event(
        "interview_placement_deferred",
        {"rubric_version": PLACEMENT_RUBRIC_VERSION},
    )
    return value


def start_placement(
    path: Path,
    *,
    activity_id: str,
    now: Clock = _utcnow,
) -> dict[str, object]:
    value = refresh_staleness(path, now=now)
    placement = value["placement"]
    assert isinstance(placement, dict)
    if placement.get("status") == "in_progress":
        return value
    timestamp = _timestamp(now)
    placement.update(
        {
            **_empty_placement(),
            "status": "in_progress",
            "attempt_id": f"interview_attempt_{uuid4().hex}",
            "activity_id": activity_id,
            "started_at": timestamp,
            "updated_at": timestamp,
            "profile_revision": value["profile_revision"],
            "problem_id": PLACEMENT_PROBLEM["problem_id"],
            "next_stage": PLACEMENT_STAGES[0],
        }
    )
    value["recommendations"] = None
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
    value["placement"] = _empty_placement()
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
    refs = placement.get("evidence_refs")
    assert isinstance(refs, list)
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
    )
    placement["updated_at"] = _timestamp(now)
    index = PLACEMENT_STAGES.index(stage)
    if index + 1 < len(PLACEMENT_STAGES):
        placement["next_stage"] = PLACEMENT_STAGES[index + 1]
    else:
        placement["next_stage"] = None
        placement["status"] = "provisional"
        placement["completed_at"] = _timestamp(now)
        placement["result"] = _provisional_result(observations)
        value["recommendations"] = _recommendations(value, current_date=now().date())
    _write(path, value)
    return value


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
    result = _provisional_result(observations)
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
    stage: str, response: str, *, coding_language: str
) -> dict[str, object]:
    normalized = response.lower()
    skipped = "skipped" in normalized or "less demanding baseline" in normalized
    non_attempt = bool(
        re.search(
            r"\b(?:i\s+(?:do not|don't|cannot|can't|could not|couldn't)\s+"
            r"(?:know|do|answer|implement|explain)|no idea|unable to|not sure how)\b",
            normalized,
        )
    )
    signals: list[str] = []
    if not skipped and not non_attempt and stage == "clarification" and "?" in response:
        signals.append("asked_clarifying_question")
    if not skipped and not non_attempt and stage == "plan" and any(
        term in normalized for term in ("set", "dict", "map", "window", "index")
    ):
        signals.append("named_data_structure_or_strategy")
    unsupported_implementation_language = (
        stage == "implementation" and coding_language.strip().lower() != "python"
    )
    if (
        not skipped
        and not non_attempt
        and stage == "implementation"
        and not unsupported_implementation_language
    ):
        try:
            tree = ast.parse(response)
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
    if not skipped and not non_attempt and stage == "tests" and any(
        term in normalized for term in ("empty", "edge", "duplicate", "no match", "assert")
    ):
        signals.append("named_edge_case")
    if (
        not skipped
        and not non_attempt
        and stage == "complexity"
        and re.search(r"\bo\s*\([^)]{1,30}\)", normalized)
    ):
        signals.append("stated_complexity")
    if (
        not skipped
        and not non_attempt
        and stage == "follow_up"
        and len(response.split()) >= 5
    ):
        signals.append("engaged_with_follow_up")
    required_by_stage = {
        "clarification": {"asked_clarifying_question"},
        "plan": {"named_data_structure_or_strategy"},
        "implementation": {"produced_function", "produced_return_path"},
        "tests": {"named_edge_case"},
        "complexity": {"stated_complexity"},
        "follow_up": {"engaged_with_follow_up"},
    }
    required = required_by_stage.get(stage, set())
    status = (
        "uncertain"
        if skipped or unsupported_implementation_language
        else "not_observed"
        if non_attempt or (required and not required <= set(signals))
        else "observed"
    )
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


def _axis_status(observations: Mapping[str, object], stages: tuple[str, ...]) -> str:
    statuses = {_observation_status(observations, stage) for stage in stages}
    if statuses == {"observed"}:
        return "observed"
    if "not_observed" in statuses:
        return "not_observed"
    return "uncertain"


def _provisional_result(observations: Mapping[str, object]) -> dict[str, object]:
    axis_statuses = {
        "prerequisites": _axis_status(observations, ("plan",)),
        "coding_fluency": _axis_status(observations, ("implementation", "tests")),
        "reasoning": _axis_status(observations, ("plan", "complexity", "follow_up")),
        "interview_process": _axis_status(
            observations, ("clarification", "tests", "follow_up")
        ),
    }
    not_observed_count = sum(
        status == "not_observed" for status in axis_statuses.values()
    )
    uncertain_count = sum(status == "uncertain" for status in axis_statuses.values())
    gaps: dict[str, dict[str, object]] = {
        "prerequisites": {
            "status": axis_statuses["prerequisites"],
            "evidence": ["plan", "implementation"],
            "note": "Data-structure prerequisites need confirmation through later retrieval.",
        },
        "coding_fluency": {
            "status": axis_statuses["coding_fluency"],
            "evidence": ["implementation", "tests"],
            "note": "Placement observes production fluency but does not establish mastery.",
        },
        "reasoning": {
            "status": axis_statuses["reasoning"],
            "evidence": ["plan", "complexity", "follow_up"],
            "note": "Reasoning remains provisional until repeated on a novel problem.",
        },
        "interview_process": {
            "status": axis_statuses["interview_process"],
            "evidence": ["clarification", "tests", "follow_up"],
            "note": "Interview-format familiarity is separate from prerequisite knowledge.",
        },
    }
    return {
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


def practice_schedule(profile: Mapping[str, object]) -> tuple[int, int, int]:
    weekly = profile["weekly_minutes"]
    requested_session = profile["session_minutes"]
    assert isinstance(weekly, int) and isinstance(requested_session, int)
    session = min(requested_session, weekly)
    sessions = max(1, weekly // session)
    scheduled = min(weekly, sessions * session)
    return scheduled, session, sessions


def _recommendations(
    value: Mapping[str, object], *, current_date: date
) -> dict[str, object]:
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
        priorities = [
            f"Raise transfer difficulty toward the {role} {level} interview bar."
        ]
    return {
        "profile_revision": value["profile_revision"],
        "weekly_minutes": scheduled,
        "session_minutes": session,
        "sessions_per_week": sessions,
        "target": f"{role} at {level} bar",
        "horizon": horizon,
        "priorities": priorities[:3],
    }


def project_staleness(
    value: Mapping[str, object], *, now: Clock = _utcnow
) -> dict[str, object]:
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
