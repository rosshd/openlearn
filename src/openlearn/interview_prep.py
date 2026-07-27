"""Local interview-prep profile and coding-placement lifecycle."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
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

Clock = Callable[[], datetime]
EventAppender = Callable[[str, dict[str, object]], None]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(now: Clock) -> str:
    return now().astimezone(timezone.utc).isoformat()


def _write(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
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
    if not isinstance(value, dict) or value.get("schema_version") != PROFILE_SCHEMA_VERSION:
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
    merged = {**current, **changes}
    normalized = _normalized_profile(merged)
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
        placement["status"] = "stale"
        placement["updated_at"] = _timestamp(now)
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
    path: Path, append_event: EventAppender, *, now: Clock = _utcnow
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
            "activity_id": f"act_{uuid4().hex}",
            "started_at": timestamp,
            "updated_at": timestamp,
            "profile_revision": value["profile_revision"],
            "problem_id": PLACEMENT_PROBLEM["problem_id"],
            "next_stage": PLACEMENT_STAGES[0],
        }
    )
    value["recommendations"] = None
    _write(path, value)
    append_event(
        "interview_placement_started",
        {
            "attempt_id": placement["attempt_id"],
            "activity_id": placement["activity_id"],
            "rubric_version": PLACEMENT_RUBRIC_VERSION,
            "profile_revision": value["profile_revision"],
            "problem": dict(PLACEMENT_PROBLEM),
            "purpose": "placement",
            "mastery_update_applied": False,
        },
    )
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
        "interview_placement_discarded",
        {
            "attempt_id": attempt_id,
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
    append_event: EventAppender,
    *,
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
    evidence_id = f"evidence_{uuid4().hex}"
    append_event(
        "interview_placement_evidence",
        {
            "evidence_id": evidence_id,
            "attempt_id": placement["attempt_id"],
            "activity_id": placement["activity_id"],
            "rubric_version": PLACEMENT_RUBRIC_VERSION,
            "evidence_kind": stage,
            "domain_evidence": {"coding": {"response": response}},
            "mastery_update_applied": False,
        },
    )
    refs = placement.get("evidence_refs")
    assert isinstance(refs, list)
    refs.append(
        {
            "evidence_id": evidence_id,
            "event_type": "interview_placement_evidence",
            "evidence_kind": stage,
        }
    )
    observations = placement.get("observations")
    assert isinstance(observations, dict)
    observations[stage] = _evidence_observation(stage, response)
    placement["updated_at"] = _timestamp(now)
    index = PLACEMENT_STAGES.index(stage)
    if index + 1 < len(PLACEMENT_STAGES):
        placement["next_stage"] = PLACEMENT_STAGES[index + 1]
    else:
        placement["next_stage"] = None
        placement["status"] = "provisional"
        placement["completed_at"] = _timestamp(now)
        placement["result"] = _provisional_result(observations)
        value["recommendations"] = _recommendations(value)
        append_event(
            "interview_placement_completed",
            {
                "attempt_id": placement["attempt_id"],
                "activity_id": placement["activity_id"],
                "rubric_version": PLACEMENT_RUBRIC_VERSION,
                "evidence_refs": list(refs),
                "result": placement["result"],
                "mastery_update_applied": False,
            },
        )
    _write(path, value)
    return value


def _evidence_observation(stage: str, response: str) -> dict[str, object]:
    normalized = response.lower()
    skipped = "skipped" in normalized or "less demanding baseline" in normalized
    signals: list[str] = []
    if stage == "clarification" and "?" in response:
        signals.append("asked_clarifying_question")
    if stage == "plan" and any(
        term in normalized for term in ("set", "dict", "map", "window", "index")
    ):
        signals.append("named_data_structure_or_strategy")
    if stage == "implementation":
        if any(term in normalized for term in ("def ", "function ", "fn ")):
            signals.append("produced_function")
        if "return" in normalized:
            signals.append("produced_return_path")
    if stage == "tests" and any(
        term in normalized for term in ("empty", "edge", "duplicate", "no match", "assert")
    ):
        signals.append("named_edge_case")
    if stage == "complexity" and ("o(" in normalized or "complexity" in normalized):
        signals.append("stated_complexity")
    if stage == "follow_up" and len(response.split()) >= 5:
        signals.append("engaged_with_follow_up")
    return {
        "substantive": not skipped and len(response.split()) >= 3,
        "skipped": skipped,
        "signals": signals,
    }


def _observation_has(
    observations: Mapping[str, object], stage: str, signal: str
) -> bool:
    value = observations.get(stage)
    return (
        isinstance(value, dict)
        and value.get("skipped") is not True
        and isinstance(value.get("signals"), list)
        and signal in value["signals"]
    )


def _provisional_result(observations: Mapping[str, object]) -> dict[str, object]:
    prerequisite_observed = _observation_has(
        observations, "plan", "named_data_structure_or_strategy"
    )
    implementation_observed = _observation_has(
        observations, "implementation", "produced_function"
    ) and _observation_has(observations, "implementation", "produced_return_path")
    tests_observed = _observation_has(observations, "tests", "named_edge_case")
    reasoning_observed = prerequisite_observed and _observation_has(
        observations, "complexity", "stated_complexity"
    )
    process_observed = _observation_has(
        observations, "clarification", "asked_clarifying_question"
    ) and _observation_has(observations, "follow_up", "engaged_with_follow_up")
    likely_gap_count = sum(
        not signal
        for signal in (
            prerequisite_observed,
            implementation_observed and tests_observed,
            reasoning_observed,
            process_observed,
        )
    )
    gaps: dict[str, dict[str, object]] = {
        "prerequisites": {
            "status": "observed" if prerequisite_observed else "likely_gap",
            "evidence": ["plan", "implementation"],
            "note": "Data-structure prerequisites need confirmation through later retrieval.",
        },
        "coding_fluency": {
            "status": (
                "observed" if implementation_observed and tests_observed else "likely_gap"
            ),
            "evidence": ["implementation", "tests"],
            "note": "Placement observes production fluency but does not establish mastery.",
        },
        "reasoning": {
            "status": "observed" if reasoning_observed else "likely_gap",
            "evidence": ["plan", "complexity", "follow_up"],
            "note": "Reasoning remains provisional until repeated on a novel problem.",
        },
        "interview_process": {
            "status": "observed" if process_observed else "likely_gap",
            "evidence": ["clarification", "tests", "follow_up"],
            "note": "Interview-format familiarity is separate from prerequisite knowledge.",
        },
    }
    return {
        "provisional": True,
        "starting_level": (
            "foundational"
            if likely_gap_count >= 3
            else "developing"
            if likely_gap_count
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


def _recommendations(value: Mapping[str, object]) -> dict[str, object]:
    profile = value["profile"]
    assert isinstance(profile, dict)
    weekly = profile["weekly_minutes"]
    requested_session = profile["session_minutes"]
    assert isinstance(weekly, int) and isinstance(requested_session, int)
    session = min(requested_session, weekly)
    sessions = max(1, weekly // session)
    scheduled = min(weekly, sessions * session)
    role = str(profile.get("role_family") or "general SWE")
    level = str(profile.get("target_level") or "target")
    return {
        "profile_revision": value["profile_revision"],
        "weekly_minutes": scheduled,
        "session_minutes": session,
        "sessions_per_week": sessions,
        "target": f"{role} at {level} bar",
        "priorities": [
            "Validate prerequisite gaps with short retrieval checks.",
            "Practice one original coding problem with explicit tests and complexity analysis.",
            "Rehearse clarification and follow-up discussion without a timer.",
        ],
    }


def refresh_staleness(path: Path, *, now: Clock = _utcnow) -> dict[str, object]:
    value = load_profile(path)
    placement = value["placement"]
    assert isinstance(placement, dict)
    completed = placement.get("completed_at")
    stale = placement.get("profile_revision") != value.get("profile_revision")
    if isinstance(completed, str):
        try:
            completed_at = datetime.fromisoformat(completed.replace("Z", "+00:00"))
        except ValueError:
            stale = True
        else:
            stale = stale or (now().astimezone(timezone.utc) - completed_at).days > STALE_AFTER_DAYS
    if stale and placement.get("status") == "provisional":
        placement["status"] = "stale"
        value["recommendations"] = None
        _write(path, value)
    return value
