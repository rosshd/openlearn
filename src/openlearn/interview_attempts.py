"""Durable local interview-problem attempts and resumable workspaces."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import windows_attempt_io

ATTEMPT_SCHEMA_VERSION = 1
ATTEMPT_ID_PATTERN = re.compile(r"^attempt_[a-f0-9]{32}$")
TOPIC_GENERATION_PATTERN = re.compile(r"^topic_[a-f0-9]{32}$")
PURPOSES = {"practice", "placement", "mastery_check", "review", "mock_interview"}
STATUSES = {"active", "completed", "abandoned", "cancelled", "invalid"}
DISPOSITIONS = {
    "solved_independently",
    "solved_with_help",
    "partial",
    "abandoned",
    "cancelled",
    "invalid",
    "runner_failure",
}
LEARNER_OUTCOMES = {
    "passed",
    "artifact_saved",
    "test_failure",
    "compile_error",
    "runtime_error",
    "timeout",
    "resource_limit",
    "output_limit",
}
INFRASTRUCTURE_OUTCOMES = {"runner_error", "runner_unavailable", "interrupted", "cancelled"}
MAX_RECORD_BYTES = 512_000
MAX_EVENTS_BYTES = 2_000_000
MAX_SNAPSHOT_BYTES = 64_000
MAX_TEXT = 16_000
MAX_TEST_RUNS = 256
MAX_EVIDENCE_REFS = 256
EVENT_SCHEMA_VERSION = 1
JOURNAL_SCHEMA_VERSION = 1
MAX_EVENT_LINE_BYTES = 64_000
FEEDBACK_STEPS = {
    "session_appended",
    "assistance_recorded",
    "activity_completed",
    "attempt_completed",
    "reflection_registered",
}
RUN_ID_PATTERN = re.compile(r"^run_[a-f0-9]{32}$")
SNAPSHOT_ID_PATTERN = re.compile(r"^snapshot_[a-f0-9]{32}$")
EVENT_ID_PATTERN = re.compile(r"^(?:event_[a-f0-9]{32}|attempt_[a-f0-9]{32}:[^\s]{1,200})$")


class AttemptError(ValueError):
    """A durable attempt is malformed or cannot be mutated safely."""


LockFactory = Callable[[Path], AbstractContextManager[None]]
AtomicWriter = Callable[[Path, str], None]
GenerationReader = Callable[[str], str | None]
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(now: Clock) -> str:
    return now().astimezone(timezone.utc).isoformat()


def _validate_timestamp(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        raise AttemptError(f"{field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AttemptError(f"{field} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AttemptError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _bounded_text(value: object, field: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise AttemptError(f"{field} must be text")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise AttemptError(f"{field} must not be empty")
    if len(normalized) > MAX_TEXT:
        raise AttemptError(f"{field} is too large")
    return normalized


def _json_size(value: object) -> int:
    try:
        return len(
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
        )
    except (TypeError, ValueError) as exc:
        raise AttemptError("attempt data must be finite JSON") from exc


def _read_bounded_text(path: Path, limit: int, field: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise AttemptError(f"{field} is unsafe")
    try:
        before = path.stat()
        if before.st_size > limit:
            raise AttemptError(f"{field} is too large")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            chunks: list[bytes] = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise AttemptError(f"{field} could not be read") from exc
    if len(data) > limit:
        raise AttemptError(f"{field} is too large")
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise AttemptError(f"{field} changed while being read")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AttemptError(f"{field} is not UTF-8") from exc


def _safe_component(value: str, field: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", value):
        raise AttemptError(f"invalid {field}")
    return value


def _validate_problem_reference(value: Mapping[str, object]) -> dict[str, object]:
    required = {
        "catalog_id",
        "catalog_revision",
        "problem_id",
        "problem_revision",
        "problem_checksum",
    }
    if set(value) != required:
        raise AttemptError("attempt problem reference fields are invalid")
    catalog_id = _bounded_text(value["catalog_id"], "catalog_id", allow_empty=False)
    problem_id = _bounded_text(value["problem_id"], "problem_id", allow_empty=False)
    catalog_revision = value["catalog_revision"]
    problem_revision = value["problem_revision"]
    checksum = value["problem_checksum"]
    if (
        isinstance(catalog_revision, bool)
        or not isinstance(catalog_revision, int)
        or catalog_revision < 1
        or isinstance(problem_revision, bool)
        or not isinstance(problem_revision, int)
        or problem_revision < 1
        or not isinstance(checksum, str)
        or not re.fullmatch(r"[a-f0-9]{64}", checksum)
    ):
        raise AttemptError("attempt problem reference is invalid")
    return {
        "catalog_id": catalog_id,
        "catalog_revision": catalog_revision,
        "problem_id": problem_id,
        "problem_revision": problem_revision,
        "problem_checksum": checksum,
    }


def validate_problem_reference(value: Mapping[str, object]) -> dict[str, object]:
    """Return a canonical exact catalog/problem reference."""
    return _validate_problem_reference(value)


def _validate_snapshot(value: object) -> dict[str, object]:
    required = {"snapshot_id", "captured_at", "sha256", "size_bytes", "content"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise AttemptError("attempt snapshot fields are invalid")
    identifier = value["snapshot_id"]
    checksum = value["sha256"]
    size = value["size_bytes"]
    content = value["content"]
    if not isinstance(identifier, str) or not SNAPSHOT_ID_PATTERN.fullmatch(identifier):
        raise AttemptError("attempt snapshot id is invalid")
    if not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise AttemptError("attempt snapshot checksum is invalid")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_SNAPSHOT_BYTES * 4:
        raise AttemptError("attempt snapshot size is invalid")
    if content is not None:
        if not isinstance(content, str) or len(content.encode("utf-8")) != size:
            raise AttemptError("attempt snapshot content size is invalid")
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != checksum:
            raise AttemptError("attempt snapshot content checksum is invalid")
        if size > MAX_SNAPSHOT_BYTES:
            raise AttemptError("attempt snapshot content exceeds the inline limit")
    _validate_timestamp(value["captured_at"], "snapshot captured_at")
    return copy.deepcopy(dict(value))


def _validate_test_run(value: object) -> dict[str, object]:
    required = {
        "run_id",
        "visibility",
        "started_at",
        "finished_at",
        "outcome",
        "learner_failure",
        "passed",
        "timeout",
        "failure_class",
        "limits",
        "output",
        "evidence_id",
        "feedback_id",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AttemptError("attempt test run fields are invalid")
    run_id = value["run_id"]
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise AttemptError("attempt test run id is invalid")
    if value["visibility"] not in {"public", "hidden"}:
        raise AttemptError("attempt test visibility is invalid")
    _validate_timestamp(value["started_at"], "test started_at")
    _validate_timestamp(value["finished_at"], "test finished_at", optional=True)
    outcome = value["outcome"]
    if outcome not in LEARNER_OUTCOMES | INFRASTRUCTURE_OUTCOMES | {"pending"}:
        raise AttemptError("attempt test outcome is invalid")
    expected_failure = outcome in LEARNER_OUTCOMES - {"passed", "artifact_saved"}
    if (
        type(value["learner_failure"]) is not bool
        or value["learner_failure"] is not expected_failure
        or type(value["passed"]) is not bool
        or value["passed"] is not (outcome == "passed")
        or type(value["timeout"]) is not bool
        or value["timeout"] is not (outcome == "timeout")
    ):
        raise AttemptError("attempt runner classification is inconsistent")
    failure_class = value["failure_class"]
    expected_class = (
        outcome
        if expected_failure
        else "infrastructure"
        if outcome in INFRASTRUCTURE_OUTCOMES
        else None
    )
    if failure_class != expected_class:
        raise AttemptError("attempt failure class is inconsistent")
    if outcome == "pending" and value["finished_at"] is not None:
        raise AttemptError("pending test run cannot be finished")
    if outcome != "pending" and value["finished_at"] is None:
        raise AttemptError("finished test run needs finished_at")
    limits = value["limits"]
    if not isinstance(limits, Mapping) or _json_size(limits) > 8_192:
        raise AttemptError("attempt test limits are invalid")
    _bounded_text(value["output"], "test output")
    for field in ("evidence_id", "feedback_id"):
        item = value[field]
        if item is not None and (not isinstance(item, str) or not item.strip()):
            raise AttemptError(f"attempt test {field} is invalid")
    return copy.deepcopy(dict(value))


def _validate_timed_entry(value: object, field: str) -> dict[str, object]:
    required = {"entry_id", "at", "content", "run_id", "evidence_id"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise AttemptError(f"attempt {field} entry fields are invalid")
    if not isinstance(value["entry_id"], str) or not re.fullmatch(
        r"entry_[a-f0-9]{32}", str(value["entry_id"])
    ):
        raise AttemptError(f"attempt {field} entry id is invalid")
    _validate_timestamp(value["at"], f"{field} at")
    _bounded_text(value["content"], field, allow_empty=False)
    for reference in ("run_id", "evidence_id"):
        item = value[reference]
        if item is not None and (not isinstance(item, str) or not item.strip()):
            raise AttemptError(f"attempt {field} {reference} is invalid")
    return copy.deepcopy(dict(value))


def _validate_assistance(value: object) -> dict[str, object]:
    required = {
        "hints",
        "scaffolding",
        "tutor_interventions",
        "editorial_exposures",
        "full_solution_exposures",
        "independent_transfer_cleared_at",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AttemptError("attempt assistance fields are invalid")
    result: dict[str, object] = {}
    for field in (
        "hints",
        "scaffolding",
        "tutor_interventions",
        "editorial_exposures",
        "full_solution_exposures",
    ):
        entries = value[field]
        if not isinstance(entries, list) or len(entries) > MAX_EVIDENCE_REFS:
            raise AttemptError(f"attempt {field} are invalid")
        result[field] = [_validate_timed_entry(entry, field) for entry in entries]
    result["independent_transfer_cleared_at"] = _validate_timestamp(
        value["independent_transfer_cleared_at"],
        "independent transfer cleared_at",
        optional=True,
    )
    return result


def _validate_reasoning(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {"entries"}:
        raise AttemptError("attempt reasoning fields are invalid")
    entries = value["entries"]
    if not isinstance(entries, list) or len(entries) > MAX_EVIDENCE_REFS:
        raise AttemptError("attempt reasoning entries are invalid")
    result = []
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {
            "entry_id",
            "at",
            "content",
            "run_id",
            "evidence_id",
            "kind",
        }:
            raise AttemptError("attempt reasoning entry fields are invalid")
        if entry["kind"] not in {"complexity", "edge_cases", "reflection", "evaluation"}:
            raise AttemptError("attempt reasoning kind is invalid")
        normalized = _validate_timed_entry(
            {key: entry[key] for key in ("entry_id", "at", "content", "run_id", "evidence_id")},
            "reasoning",
        )
        normalized["kind"] = entry["kind"]
        result.append(normalized)
    return {"entries": result}


def _validate_execution(value: object, activity_id: str) -> dict[str, object]:
    required = {
        "activity_revision",
        "activity_checksum",
        "activity_bundle",
        "function_name",
        "test_cases",
        "test_bundle_checksum",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AttemptError("attempt execution fields are invalid")
    revision = value["activity_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise AttemptError("attempt activity revision is invalid")
    bundle = value["activity_bundle"]
    if not isinstance(bundle, Mapping) or _json_size(bundle) > 64_000:
        raise AttemptError("attempt activity bundle is invalid")
    if bundle.get("activity_id") != activity_id or bundle.get("revision") != revision:
        raise AttemptError("attempt activity bundle identity is invalid")
    activity_checksum = value["activity_checksum"]
    canonical_bundle = json.dumps(
        bundle, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if (
        not isinstance(activity_checksum, str)
        or activity_checksum != hashlib.sha256(canonical_bundle).hexdigest()
    ):
        raise AttemptError("attempt activity checksum is invalid")
    function_name = value["function_name"]
    if not isinstance(function_name, str) or (
        function_name and not function_name.isidentifier()
    ):
        raise AttemptError("attempt function name is invalid")
    test_cases = value["test_cases"]
    if not isinstance(test_cases, list) or len(test_cases) > 256:
        raise AttemptError("attempt test bundle is invalid")
    test_checksum = value["test_bundle_checksum"]
    canonical_tests = json.dumps(
        test_cases, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if (
        not isinstance(test_checksum, str)
        or test_checksum != hashlib.sha256(canonical_tests).hexdigest()
    ):
        raise AttemptError("attempt test bundle checksum is invalid")
    return copy.deepcopy(dict(value))


def _validate_feedback(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {"pending", "deliveries"}:
        raise AttemptError("attempt feedback fields are invalid")
    pending = value["pending"]
    if pending is not None:
        required = {
            "feedback_id",
            "run_id",
            "evidence_id",
            "hint_index",
            "prompt",
            "created_at",
            "failures",
            "answer",
            "answer_saved_at",
            "completed_steps",
        }
        if not isinstance(pending, Mapping) or set(pending) != required:
            raise AttemptError("pending feedback fields are invalid")
        if not isinstance(pending["feedback_id"], str) or not re.fullmatch(
            r"feedback_[a-f0-9]{64}", str(pending["feedback_id"])
        ):
            raise AttemptError("pending feedback id is invalid")
        if not isinstance(pending["run_id"], str) or not RUN_ID_PATTERN.fullmatch(
            str(pending["run_id"])
        ):
            raise AttemptError("pending feedback run id is invalid")
        if not isinstance(pending["evidence_id"], str) or not pending["evidence_id"]:
            raise AttemptError("pending feedback evidence id is invalid")
        if (
            isinstance(pending["hint_index"], bool)
            or not isinstance(pending["hint_index"], int)
            or pending["hint_index"] < 0
        ):
            raise AttemptError("pending feedback hint index is invalid")
        _bounded_text(pending["prompt"], "pending feedback prompt", allow_empty=False)
        _validate_timestamp(pending["created_at"], "pending feedback created_at")
        failures = pending["failures"]
        if not isinstance(failures, list) or len(failures) > 64:
            raise AttemptError("pending feedback failures are invalid")
        for failure in failures:
            _validate_timed_entry(failure, "feedback failure")
        answer = pending["answer"]
        if answer is not None:
            _bounded_text(answer, "pending feedback answer", allow_empty=False)
        answer_saved_at = _validate_timestamp(
            pending["answer_saved_at"],
            "pending feedback answer_saved_at",
            optional=True,
        )
        if (answer is None) != (answer_saved_at is None):
            raise AttemptError("pending feedback answer fields are inconsistent")
        completed_steps = pending["completed_steps"]
        if (
            not isinstance(completed_steps, list)
            or len(completed_steps) != len(set(completed_steps))
            or any(step not in FEEDBACK_STEPS for step in completed_steps)
            or completed_steps != sorted(completed_steps)
        ):
            raise AttemptError("pending feedback completed steps are invalid")
        if completed_steps and answer is None:
            raise AttemptError("feedback steps require a persisted answer")
    deliveries = value["deliveries"]
    if not isinstance(deliveries, list) or len(deliveries) > MAX_TEST_RUNS:
        raise AttemptError("feedback deliveries are invalid")
    for delivery in deliveries:
        required = {
            "feedback_id",
            "run_id",
            "evidence_id",
            "hint_index",
            "delivered_at",
            "response_hash",
            "acknowledged_at",
        }
        if not isinstance(delivery, Mapping) or set(delivery) != required:
            raise AttemptError("feedback delivery fields are invalid")
        if not isinstance(delivery["feedback_id"], str) or not re.fullmatch(
            r"feedback_[a-f0-9]{64}", str(delivery["feedback_id"])
        ):
            raise AttemptError("feedback delivery id is invalid")
        if not isinstance(delivery["run_id"], str) or not RUN_ID_PATTERN.fullmatch(
            str(delivery["run_id"])
        ):
            raise AttemptError("feedback delivery run id is invalid")
        if not isinstance(delivery["evidence_id"], str) or not delivery["evidence_id"]:
            raise AttemptError("feedback delivery evidence id is invalid")
        if (
            isinstance(delivery["hint_index"], bool)
            or not isinstance(delivery["hint_index"], int)
            or delivery["hint_index"] < 0
        ):
            raise AttemptError("feedback delivery hint index is invalid")
        _validate_timestamp(delivery["delivered_at"], "feedback delivered_at")
        if not isinstance(delivery["response_hash"], str) or not re.fullmatch(
            r"[a-f0-9]{64}", str(delivery["response_hash"])
        ):
            raise AttemptError("feedback response hash is invalid")
        _validate_timestamp(
            delivery["acknowledged_at"],
            "feedback acknowledged_at",
            optional=True,
        )
    return copy.deepcopy(dict(value))


def _validate_event(value: object, attempt_id: str) -> dict[str, object]:
    required = {
        "schema_version",
        "event_id",
        "attempt_id",
        "attempt_revision",
        "ts",
        "event_type",
        "data",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AttemptError("attempt event fields are invalid")
    if value["schema_version"] != EVENT_SCHEMA_VERSION:
        raise AttemptError("attempt event schema is invalid")
    if value["attempt_id"] != attempt_id:
        raise AttemptError("attempt event identity is invalid")
    event_id = value["event_id"]
    if not isinstance(event_id, str) or not EVENT_ID_PATTERN.fullmatch(event_id):
        raise AttemptError("attempt event id is invalid")
    revision = value["attempt_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise AttemptError("attempt event revision is invalid")
    _validate_timestamp(value["ts"], "attempt event timestamp")
    _bounded_text(value["event_type"], "attempt event type", allow_empty=False)
    if not isinstance(value["data"], Mapping) or _json_size(value["data"]) > MAX_TEXT:
        raise AttemptError("attempt event data is invalid")
    return copy.deepcopy(dict(value))


def validate_attempt(value: Mapping[str, object]) -> dict[str, object]:
    required = {
        "schema_version",
        "attempt_id",
        "revision",
        "topic",
        "topic_generation",
        "profile_ref",
        "language",
        "activity_id",
        "execution",
        "problem",
        "purpose",
        "status",
        "disposition",
        "workspace_ref",
        "started_at",
        "last_active_at",
        "completed_at",
        "abandoned_at",
        "cancelled_at",
        "resumed_at",
        "clarification",
        "plan",
        "snapshots",
        "test_runs",
        "assistance",
        "reasoning",
        "evidence_refs",
        "follow_up",
        "feedback",
    }
    if set(value) != required:
        raise AttemptError("attempt record fields are invalid")
    if value["schema_version"] != ATTEMPT_SCHEMA_VERSION:
        raise AttemptError("attempt has an unsupported format")
    attempt_id = value["attempt_id"]
    if not isinstance(attempt_id, str) or not ATTEMPT_ID_PATTERN.fullmatch(attempt_id):
        raise AttemptError("attempt_id is invalid")
    revision = value["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise AttemptError("attempt revision is invalid")
    topic = value["topic"]
    if not isinstance(topic, str):
        raise AttemptError("attempt topic is invalid")
    _safe_component(topic, "attempt topic")
    generation = value["topic_generation"]
    if not isinstance(generation, str) or not TOPIC_GENERATION_PATTERN.fullmatch(generation):
        raise AttemptError("attempt topic generation is invalid")
    for field in ("profile_ref", "language", "activity_id"):
        _bounded_text(value[field], field)
    activity_id = str(value["activity_id"])
    _validate_execution(value["execution"], activity_id)
    problem = value["problem"]
    if not isinstance(problem, Mapping):
        raise AttemptError("attempt problem reference must be an object")
    _validate_problem_reference(problem)
    if value["purpose"] not in PURPOSES or value["status"] not in STATUSES:
        raise AttemptError("attempt lifecycle value is invalid")
    disposition = value["disposition"]
    if disposition is not None and disposition not in DISPOSITIONS:
        raise AttemptError("attempt disposition is invalid")
    workspace_ref = value["workspace_ref"]
    if not isinstance(workspace_ref, str) or not workspace_ref:
        raise AttemptError("attempt workspace reference is invalid")
    workspace_path = Path(workspace_ref)
    if workspace_path.is_absolute() or ".." in workspace_path.parts:
        raise AttemptError("attempt workspace reference must be safe and relative")
    normalized_timestamps: dict[str, str | None] = {}
    for field in (
        "started_at",
        "last_active_at",
        "completed_at",
        "abandoned_at",
        "cancelled_at",
        "resumed_at",
    ):
        normalized_timestamps[field] = _validate_timestamp(
            value[field], field, optional=field not in {"started_at", "last_active_at"}
        )
    started = datetime.fromisoformat(str(normalized_timestamps["started_at"]))
    last_active = datetime.fromisoformat(str(normalized_timestamps["last_active_at"]))
    if last_active < started:
        raise AttemptError("attempt last_active_at precedes started_at")
    for field in ("completed_at", "abandoned_at", "cancelled_at", "resumed_at"):
        timestamp = normalized_timestamps[field]
        if timestamp is not None and datetime.fromisoformat(timestamp) < started:
            raise AttemptError(f"attempt {field} precedes started_at")
    status = value["status"]
    required_lifecycle = {
        "completed": ("completed_at", {"solved_independently", "solved_with_help", "partial"}),
        "abandoned": ("abandoned_at", {"abandoned"}),
        "cancelled": ("cancelled_at", {"cancelled"}),
        "invalid": ("completed_at", {"invalid"}),
    }
    if status in required_lifecycle:
        timestamp_field, allowed_dispositions = required_lifecycle[str(status)]
        if (
            normalized_timestamps[timestamp_field] is None
            or value["disposition"] not in allowed_dispositions
        ):
            raise AttemptError("attempt terminal lifecycle fields are inconsistent")
    elif status == "active" and value["disposition"] is not None:
        raise AttemptError("active attempt cannot have a final disposition")
    for field in ("clarification", "plan"):
        _bounded_text(value[field], field)
    snapshots = value["snapshots"]
    test_runs = value["test_runs"]
    evidence_refs = value["evidence_refs"]
    if not isinstance(snapshots, list) or len(snapshots) > MAX_TEST_RUNS:
        raise AttemptError("attempt snapshots are invalid")
    if not isinstance(test_runs, list) or len(test_runs) > MAX_TEST_RUNS:
        raise AttemptError("attempt test runs are invalid")
    if not isinstance(evidence_refs, list) or len(evidence_refs) > MAX_EVIDENCE_REFS:
        raise AttemptError("attempt evidence references are invalid")
    for reference in evidence_refs:
        if not isinstance(reference, Mapping) or set(reference) != {
            "evidence_id",
            "kind",
            "added_at",
        }:
            raise AttemptError("attempt evidence reference fields are invalid")
        _bounded_text(reference["evidence_id"], "evidence id", allow_empty=False)
        _bounded_text(reference["kind"], "evidence kind", allow_empty=False)
        _validate_timestamp(reference["added_at"], "evidence added_at")
    snapshot_values = [_validate_snapshot(snapshot) for snapshot in snapshots]
    identifiers: set[str] = set()
    run_values = []
    for run in test_runs:
        normalized_run = _validate_test_run(run)
        run_id = str(normalized_run["run_id"])
        if run_id in identifiers:
            raise AttemptError("attempt contains duplicate test runs")
        identifiers.add(run_id)
        run_values.append(normalized_run)
    assistance = value["assistance"]
    reasoning = value["reasoning"]
    follow_up = value["follow_up"]
    assistance_value = _validate_assistance(assistance)
    reasoning_value = _validate_reasoning(reasoning)
    if not isinstance(follow_up, Mapping) or set(follow_up) != {
        "scheduled_at",
        "transfer_activity_id",
        "independent_transfer_evidence",
    }:
        raise AttemptError("attempt follow-up is invalid")
    _validate_timestamp(follow_up["scheduled_at"], "follow_up scheduled_at", optional=True)
    transfer_id = follow_up["transfer_activity_id"]
    if transfer_id is not None and (not isinstance(transfer_id, str) or not transfer_id):
        raise AttemptError("attempt transfer activity id is invalid")
    transfer_evidence = follow_up["independent_transfer_evidence"]
    if not isinstance(transfer_evidence, list) or len(transfer_evidence) > MAX_EVIDENCE_REFS:
        raise AttemptError("independent transfer evidence is invalid")
    for reference in transfer_evidence:
        if not isinstance(reference, Mapping) or set(reference) != {
            "evidence_id",
            "attempt_id",
            "problem",
            "verified_at",
        }:
            raise AttemptError("independent transfer evidence fields are invalid")
        _validate_problem_reference(reference["problem"])  # type: ignore[arg-type]
        _validate_timestamp(reference["verified_at"], "transfer verified_at")
    feedback_value = _validate_feedback(value["feedback"])
    evidence_ids = {
        str(reference["evidence_id"])
        for reference in evidence_refs
        if isinstance(reference, Mapping)
    }
    feedback_ids = {
        str(delivery["feedback_id"])
        for delivery in feedback_value["deliveries"]  # type: ignore[union-attr]
        if isinstance(delivery, Mapping)
    }
    pending_feedback = feedback_value["pending"]
    if isinstance(pending_feedback, Mapping):
        feedback_ids.add(str(pending_feedback["feedback_id"]))
    for run in run_values:
        if run["evidence_id"] is not None and run["evidence_id"] not in evidence_ids:
            raise AttemptError("test run references missing evidence")
        if run["feedback_id"] is not None and run["feedback_id"] not in feedback_ids:
            raise AttemptError("test run references missing feedback")
    for container in (
        assistance_value["hints"],
        assistance_value["scaffolding"],
        assistance_value["tutor_interventions"],
        assistance_value["editorial_exposures"],
        assistance_value["full_solution_exposures"],
        reasoning_value["entries"],
    ):
        assert isinstance(container, list)
        for entry in container:
            if entry["run_id"] is not None and entry["run_id"] not in identifiers:
                raise AttemptError("attempt provenance references an unknown run")
            if entry["evidence_id"] is not None and entry["evidence_id"] not in evidence_ids:
                raise AttemptError("attempt provenance references unknown evidence")
    normalized = copy.deepcopy(dict(value))
    normalized["snapshots"] = snapshot_values
    normalized["test_runs"] = run_values
    normalized["assistance"] = assistance_value
    normalized["reasoning"] = reasoning_value
    normalized["feedback"] = feedback_value
    normalized.update(normalized_timestamps)
    if _json_size(value) > MAX_RECORD_BYTES:
        raise AttemptError("attempt record is too large")
    return normalized


def effective_disposition(value: Mapping[str, object]) -> str | None:
    """Derive current independence without rewriting immutable completion history."""
    attempt = validate_attempt(value)
    if (
        attempt["disposition"] == "solved_with_help"
        and attempt["assistance"]["independent_transfer_cleared_at"] is not None  # type: ignore[index]
    ):
        return "solved_independently"
    disposition = attempt["disposition"]
    return str(disposition) if disposition is not None else None


class AttemptStore:
    """One mutation boundary for attempt state, journal, and idempotent events."""

    def __init__(
        self,
        topics_root: Path,
        lock: LockFactory,
        write_atomic: AtomicWriter,
        current_generation: GenerationReader,
        *,
        now: Clock = _utcnow,
    ) -> None:
        self.topics_root = topics_root
        self.lock = lock
        self.write_atomic = write_atomic
        self.current_generation = current_generation
        self.now = now

    @property
    def root(self) -> Path:
        return self.topics_root / "attempts"

    def topic_dir(self, topic: str) -> Path:
        directory = self.root / _safe_component(topic, "attempt topic")
        for candidate in (self.topics_root, self.root, directory):
            if candidate.exists() and candidate.is_symlink():
                raise AttemptError("attempt directory is unsafe")
        return directory

    def state_path(self, topic: str, attempt_id: str) -> Path:
        self._validate_id(attempt_id)
        return self.topic_dir(topic) / f"{attempt_id}.json"

    def events_path(self, topic: str, attempt_id: str) -> Path:
        self._validate_id(attempt_id)
        return self.topic_dir(topic) / f"{attempt_id}.events.jsonl"

    def journal_path(self, topic: str, attempt_id: str) -> Path:
        self._validate_id(attempt_id)
        return self.topic_dir(topic) / f".{attempt_id}.journal.json"

    def topic_path(self, topic: str) -> Path:
        return self.topics_root / f"{_safe_component(topic, 'attempt topic')}.md"

    @contextmanager
    def _locked_attempt(self, topic: str, state_path: Path):
        """Acquire the canonical topic-generation lock before the attempt lock."""
        with self.lock(self.topic_path(topic)):
            with self.lock(state_path):
                yield

    def create(
        self,
        *,
        topic: str,
        topic_generation: str,
        problem: Mapping[str, object],
        workspace: Path,
        language: str,
        activity_id: str,
        purpose: str,
        profile_ref: str = "",
        clarification: str = "",
        plan: str = "",
        assistance: Mapping[str, object] | None = None,
        activity_bundle: Mapping[str, object] | None = None,
        attempt_id: str | None = None,
    ) -> dict[str, object]:
        identifier = attempt_id or f"attempt_{uuid4().hex}"
        self._validate_id(identifier)
        workspace_ref = self.workspace_reference(topic, workspace)
        timestamp = _timestamp(self.now)
        bundle = copy.deepcopy(
            dict(activity_bundle or {"activity_id": activity_id, "revision": 1})
        )
        if bundle.get("activity_id") != activity_id:
            raise AttemptError("activity bundle does not match attempt activity_id")
        payload = bundle.get("domain_payload")
        coding = payload.get("coding") if isinstance(payload, Mapping) else {}
        if not isinstance(coding, Mapping):
            coding = {}
        raw_tests = coding.get("test_cases", [])
        test_cases = copy.deepcopy(raw_tests) if isinstance(raw_tests, list) else []
        canonical_bundle = json.dumps(
            bundle, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        canonical_tests = json.dumps(
            test_cases, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        assistance_value = {
            "hints": [],
            "scaffolding": [],
            "tutor_interventions": [],
            "editorial_exposures": [],
            "full_solution_exposures": [],
            "independent_transfer_cleared_at": None,
        }
        if assistance:
            if set(assistance) == set(assistance_value):
                assistance_value = _validate_assistance(assistance)
            else:
                for field in ("hints", "scaffolding", "tutor_interventions"):
                    items = assistance.get(field, [])
                    if isinstance(items, list):
                        assistance_value[field] = [
                            self._timed_entry(str(item))
                            for item in items
                            if str(item).strip()
                        ]
                if assistance.get("editorial_exposed"):
                    assistance_value["editorial_exposures"] = [
                        self._timed_entry("editorial exposed")
                    ]
                if assistance.get("full_solution_exposed"):
                    assistance_value["full_solution_exposures"] = [
                        self._timed_entry("full solution exposed")
                    ]
        record = validate_attempt(
            {
                "schema_version": ATTEMPT_SCHEMA_VERSION,
                "attempt_id": identifier,
                "revision": 1,
                "topic": topic,
                "topic_generation": topic_generation,
                "profile_ref": _bounded_text(profile_ref, "profile_ref"),
                "language": _bounded_text(language, "language", allow_empty=False),
                "activity_id": _bounded_text(activity_id, "activity_id"),
                "execution": {
                    "activity_revision": bundle.get("revision", 1),
                    "activity_checksum": hashlib.sha256(canonical_bundle).hexdigest(),
                    "activity_bundle": bundle,
                    "function_name": str(coding.get("function_name") or ""),
                    "test_cases": test_cases,
                    "test_bundle_checksum": hashlib.sha256(canonical_tests).hexdigest(),
                },
                "problem": _validate_problem_reference(problem),
                "purpose": purpose,
                "status": "active",
                "disposition": None,
                "workspace_ref": workspace_ref,
                "started_at": timestamp,
                "last_active_at": timestamp,
                "completed_at": None,
                "abandoned_at": None,
                "cancelled_at": None,
                "resumed_at": None,
                "clarification": _bounded_text(clarification, "clarification"),
                "plan": _bounded_text(plan, "plan"),
                "snapshots": [],
                "test_runs": [],
                "assistance": assistance_value,
                "reasoning": {"entries": []},
                "evidence_refs": [],
                "follow_up": {
                    "scheduled_at": None,
                    "transfer_activity_id": None,
                    "independent_transfer_evidence": [],
                },
                "feedback": {"pending": None, "deliveries": []},
            }
        )
        state_path = self.state_path(topic, identifier)
        with self._locked_attempt(topic, state_path):
            if self.current_generation(topic) != topic_generation:
                raise AttemptError("topic changed or was deleted before attempt creation")
            if state_path.exists():
                raise AttemptError("attempt already exists")
            self._commit(record, "attempt_created", {}, previous_revision=0)
        return record

    def load(self, topic: str, attempt_id: str) -> dict[str, object]:
        state_path = self.state_path(topic, attempt_id)
        with self._locked_attempt(topic, state_path):
            self._recover(topic, attempt_id)
            return self._read_state(state_path)

    def list(self, topic: str) -> list[dict[str, object]]:
        directory = self.topic_dir(topic)
        if not directory.exists():
            return []
        if directory.is_symlink() or not directory.is_dir():
            raise AttemptError("attempt directory is unsafe")
        records: list[dict[str, object]] = []
        for path in sorted(directory.glob("attempt_*.json")):
            if path.is_symlink():
                raise AttemptError("attempt record must not be a symlink")
            records.append(self.load(topic, path.stem))
        return sorted(records, key=lambda item: str(item["started_at"]), reverse=True)

    def find_for_workspace(
        self, topic: str, workspace: Path, *, unfinished_only: bool = False
    ) -> dict[str, object] | None:
        workspace_ref = self.workspace_reference(topic, workspace)
        for record in self.list(topic):
            if record["workspace_ref"] == workspace_ref and (
                not unfinished_only or record["status"] == "active"
            ):
                return record
        return None

    def mutate(
        self,
        topic: str,
        attempt_id: str,
        event_type: str,
        event_data: Mapping[str, object],
        update: Callable[[dict[str, object]], None],
        *,
        event_id: str | None = None,
        allow_completed_evidence: bool = False,
    ) -> dict[str, object]:
        state_path = self.state_path(topic, attempt_id)
        with self._locked_attempt(topic, state_path):
            self._recover(topic, attempt_id)
            current = self._read_state(state_path)
            if self.current_generation(topic) != current["topic_generation"]:
                raise AttemptError("topic changed or was deleted while the attempt was active")
            if current["status"] == "completed" and not allow_completed_evidence:
                raise AttemptError("completed attempts are immutable")
            updated = copy.deepcopy(current)
            update(updated)
            if updated == current:
                return current
            updated["revision"] = int(current["revision"]) + 1
            updated["last_active_at"] = _timestamp(self.now)
            validated = validate_attempt(updated)
            self._commit(
                validated,
                event_type,
                event_data,
                event_id=event_id,
                previous_revision=int(current["revision"]),
            )
            return validated

    def _timed_entry(
        self,
        content: str,
        *,
        run_id: str | None = None,
        evidence_id: str | None = None,
    ) -> dict[str, object]:
        return {
            "entry_id": f"entry_{uuid4().hex}",
            "at": _timestamp(self.now),
            "content": _bounded_text(content, "entry content", allow_empty=False),
            "run_id": run_id,
            "evidence_id": evidence_id,
        }

    def resume(self, topic: str, attempt_id: str) -> dict[str, object]:
        def update(value: dict[str, object]) -> None:
            if value["status"] not in {"active", "abandoned"}:
                raise AttemptError("only unfinished or abandoned attempts can be resumed")
            if value["status"] == "abandoned":
                value["status"] = "active"
                value["disposition"] = None
            value["resumed_at"] = _timestamp(self.now)

        return self.mutate(topic, attempt_id, "attempt_resumed", {}, update)

    def abandon(self, topic: str, attempt_id: str, reason: str = "") -> dict[str, object]:
        def update(value: dict[str, object]) -> None:
            if value["status"] != "active":
                raise AttemptError("only an active attempt can be abandoned")
            value["status"] = "abandoned"
            value["disposition"] = "abandoned"
            value["abandoned_at"] = _timestamp(self.now)

        return self.mutate(
            topic,
            attempt_id,
            "attempt_abandoned",
            {"reason": _bounded_text(reason, "reason")},
            update,
        )

    def cancel(self, topic: str, attempt_id: str, reason: str = "") -> dict[str, object]:
        def update(value: dict[str, object]) -> None:
            if value["status"] != "active":
                raise AttemptError("only an active attempt can be cancelled")
            value["status"] = "cancelled"
            value["disposition"] = "cancelled"
            value["cancelled_at"] = _timestamp(self.now)

        return self.mutate(
            topic,
            attempt_id,
            "attempt_cancelled",
            {"reason": _bounded_text(reason, "reason")},
            update,
        )

    def retry(self, topic: str, attempt_id: str) -> dict[str, object]:
        source = self.load(topic, attempt_id)
        workspace = self.resolve_workspace(source)
        retry_id = f"attempt_{uuid4().hex}"
        retry_workspace = workspace.with_name(
            f"{workspace.stem}-retry-{retry_id[-8:]}{workspace.suffix}"
        )
        if retry_workspace.exists():
            raise AttemptError("retry workspace already exists")
        try:
            source_text = self._read_workspace(workspace).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AttemptError("attempt workspace is not UTF-8 text") from exc
        self._write_workspace_exclusive(topic, retry_workspace, source_text.encode("utf-8"))
        return self.create(
            topic=topic,
            topic_generation=str(source["topic_generation"]),
            problem=source["problem"],  # type: ignore[arg-type]
            workspace=retry_workspace,
            language=str(source["language"]),
            activity_id=str(source["activity_id"]),
            purpose=str(source["purpose"]),
            profile_ref=str(source["profile_ref"]),
            clarification=str(source["clarification"]),
            plan=str(source["plan"]),
            assistance=source["assistance"],  # type: ignore[arg-type]
            activity_bundle=source["execution"]["activity_bundle"],  # type: ignore[index]
            attempt_id=retry_id,
        )

    def snapshot(self, topic: str, attempt_id: str) -> dict[str, object]:
        record = self.load(topic, attempt_id)
        workspace = self.resolve_workspace(record)
        data = self._read_workspace(workspace)
        snapshot = {
            "snapshot_id": f"snapshot_{uuid4().hex}",
            "captured_at": _timestamp(self.now),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "content": data.decode("utf-8") if len(data) <= MAX_SNAPSHOT_BYTES else None,
        }

        def update(value: dict[str, object]) -> None:
            snapshots = value["snapshots"]
            assert isinstance(snapshots, list)
            if snapshots and snapshots[-1].get("sha256") == snapshot["sha256"]:
                return
            snapshots.append(snapshot)

        return self.mutate(
            topic,
            attempt_id,
            "attempt_snapshot_saved",
            {key: snapshot[key] for key in ("snapshot_id", "sha256", "size_bytes")},
            update,
        )

    def start_test(
        self,
        topic: str,
        attempt_id: str,
        *,
        visibility: str = "public",
        run_id: str | None = None,
    ) -> tuple[dict[str, object], str]:
        if visibility not in {"public", "hidden"}:
            raise AttemptError("test visibility is invalid")
        identifier = run_id or f"run_{uuid4().hex}"
        if not RUN_ID_PATTERN.fullmatch(identifier):
            raise AttemptError("test run id is invalid")
        run = {
            "run_id": identifier,
            "visibility": visibility,
            "started_at": _timestamp(self.now),
            "finished_at": None,
            "outcome": "pending",
            "learner_failure": False,
            "passed": False,
            "timeout": False,
            "failure_class": None,
            "limits": {},
            "output": "",
            "evidence_id": None,
            "feedback_id": None,
        }

        def update(value: dict[str, object]) -> None:
            runs = value["test_runs"]
            assert isinstance(runs, list)
            existing = [item for item in runs if item.get("run_id") == identifier]
            if existing:
                if existing[0].get("visibility") != visibility:
                    raise AttemptError("test run id has conflicting visibility")
                return
            if len(runs) >= MAX_TEST_RUNS:
                raise AttemptError("attempt has too many test runs")
            runs.append(run)

        record = self.mutate(
            topic,
            attempt_id,
            "attempt_test_started",
            {"run_id": identifier, "visibility": visibility},
            update,
            event_id=f"{attempt_id}:{identifier}:started",
        )
        return record, identifier

    def finish_test(
        self,
        topic: str,
        attempt_id: str,
        run_id: str,
        *,
        outcome: str,
        output: str = "",
        limits: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        if outcome not in LEARNER_OUTCOMES | INFRASTRUCTURE_OUTCOMES:
            raise AttemptError("test outcome is invalid")

        def update(value: dict[str, object]) -> None:
            runs = value["test_runs"]
            assert isinstance(runs, list)
            matching = [run for run in runs if run.get("run_id") == run_id]
            if len(matching) != 1:
                raise AttemptError("test run does not belong to this attempt")
            run = matching[0]
            if run["outcome"] != "pending":
                if run["outcome"] == outcome:
                    return
                raise AttemptError("test run is already finalized")
            run.update(
                {
                    "finished_at": _timestamp(self.now),
                    "outcome": outcome,
                    "learner_failure": outcome
                    in LEARNER_OUTCOMES - {"passed", "artifact_saved"},
                    "passed": outcome == "passed",
                    "timeout": outcome == "timeout",
                    "failure_class": (
                        outcome
                        if outcome in LEARNER_OUTCOMES - {"passed", "artifact_saved"}
                        else "infrastructure"
                        if outcome in INFRASTRUCTURE_OUTCOMES
                        else None
                    ),
                    "limits": copy.deepcopy(dict(limits or {})),
                    "output": _bounded_text(output[:MAX_TEXT], "test output"),
                }
            )

        return self.mutate(
            topic,
            attempt_id,
            "attempt_test_finished",
            {"run_id": run_id, "outcome": outcome},
            update,
            event_id=f"{attempt_id}:{run_id}:finished",
        )

    def queue_feedback(
        self,
        topic: str,
        attempt_id: str,
        run_id: str,
        evidence_id: str,
        *,
        hint_index: int,
        prompt: str,
    ) -> dict[str, object]:
        feedback_id = "feedback_" + hashlib.sha256(
            f"{attempt_id}:{run_id}:{evidence_id}".encode("utf-8")
        ).hexdigest()

        def update(value: dict[str, object]) -> None:
            runs = value["test_runs"]
            assert isinstance(runs, list)
            matching = [run for run in runs if run.get("run_id") == run_id]
            if len(matching) != 1 or matching[0].get("outcome") == "pending":
                raise AttemptError("feedback requires one completed test run")
            run = matching[0]
            if run.get("feedback_id") not in {None, feedback_id}:
                raise AttemptError("test run is bound to different feedback")
            feedback = value["feedback"]
            assert isinstance(feedback, dict)
            delivered = feedback["deliveries"]
            assert isinstance(delivered, list)
            if any(item.get("feedback_id") == feedback_id for item in delivered):
                return
            pending = feedback.get("pending")
            if pending is not None:
                if pending.get("feedback_id") == feedback_id:
                    return
                raise AttemptError("another feedback delivery is pending")
            run["evidence_id"] = evidence_id
            run["feedback_id"] = feedback_id
            feedback["pending"] = {
                "feedback_id": feedback_id,
                "run_id": run_id,
                "evidence_id": evidence_id,
                "hint_index": hint_index,
                "prompt": _bounded_text(prompt, "feedback prompt", allow_empty=False),
                "created_at": _timestamp(self.now),
                "failures": [],
                "answer": None,
                "answer_saved_at": None,
                "completed_steps": [],
            }

        return self.mutate(
            topic,
            attempt_id,
            "attempt_feedback_queued",
            {"feedback_id": feedback_id, "run_id": run_id, "evidence_id": evidence_id},
            update,
            event_id=f"{attempt_id}:feedback:{feedback_id}:queued",
        )

    def record_feedback_failure(
        self, topic: str, attempt_id: str, feedback_id: str, error: str
    ) -> dict[str, object]:
        def update(value: dict[str, object]) -> None:
            feedback = value["feedback"]
            assert isinstance(feedback, dict)
            pending = feedback.get("pending")
            if not isinstance(pending, dict) or pending.get("feedback_id") != feedback_id:
                raise AttemptError("feedback is not pending")
            failures = pending["failures"]
            assert isinstance(failures, list)
            failures.append(self._timed_entry(error))

        return self.mutate(
            topic,
            attempt_id,
            "attempt_feedback_failed",
            {"feedback_id": feedback_id},
            update,
        )

    def save_feedback_answer(
        self,
        topic: str,
        attempt_id: str,
        feedback_id: str,
        response: str,
    ) -> dict[str, object]:
        answer = _bounded_text(
            response, "pending feedback answer", allow_empty=False
        )

        def update(value: dict[str, object]) -> None:
            feedback = value["feedback"]
            assert isinstance(feedback, dict)
            pending = feedback.get("pending")
            if not isinstance(pending, dict) or pending.get("feedback_id") != feedback_id:
                deliveries = feedback["deliveries"]
                assert isinstance(deliveries, list)
                if any(item.get("feedback_id") == feedback_id for item in deliveries):
                    return
                raise AttemptError("feedback is not pending")
            existing = pending.get("answer")
            if existing is not None and existing != answer:
                raise AttemptError("feedback answer conflicts with persisted content")
            if existing is None:
                pending["answer"] = answer
                pending["answer_saved_at"] = _timestamp(self.now)

        return self.mutate(
            topic,
            attempt_id,
            "attempt_feedback_answer_saved",
            {
                "feedback_id": feedback_id,
                "response_hash": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
            },
            update,
            event_id=f"{attempt_id}:feedback:{feedback_id}:answer",
        )

    def mark_feedback_step(
        self,
        topic: str,
        attempt_id: str,
        feedback_id: str,
        step: str,
    ) -> dict[str, object]:
        if step not in FEEDBACK_STEPS:
            raise AttemptError("feedback recovery step is invalid")

        def update(value: dict[str, object]) -> None:
            feedback = value["feedback"]
            assert isinstance(feedback, dict)
            pending = feedback.get("pending")
            if not isinstance(pending, dict) or pending.get("feedback_id") != feedback_id:
                deliveries = feedback["deliveries"]
                assert isinstance(deliveries, list)
                if any(item.get("feedback_id") == feedback_id for item in deliveries):
                    return
                raise AttemptError("feedback is not pending")
            if pending.get("answer") is None:
                raise AttemptError("feedback answer must be saved before recovery steps")
            steps = pending["completed_steps"]
            assert isinstance(steps, list)
            if step not in steps:
                steps.append(step)
                steps.sort()

        return self.mutate(
            topic,
            attempt_id,
            "attempt_feedback_step_completed",
            {"feedback_id": feedback_id, "step": step},
            update,
            event_id=f"{attempt_id}:feedback:{feedback_id}:step:{step}",
            allow_completed_evidence=True,
        )

    def deliver_feedback(
        self, topic: str, attempt_id: str, feedback_id: str
    ) -> dict[str, object]:
        def update(value: dict[str, object]) -> None:
            feedback = value["feedback"]
            assert isinstance(feedback, dict)
            deliveries = feedback["deliveries"]
            assert isinstance(deliveries, list)
            existing = [
                item for item in deliveries if item.get("feedback_id") == feedback_id
            ]
            if existing:
                return
            pending = feedback.get("pending")
            if not isinstance(pending, dict) or pending.get("feedback_id") != feedback_id:
                raise AttemptError("feedback is not pending")
            answer = pending.get("answer")
            if not isinstance(answer, str) or not answer:
                raise AttemptError("feedback answer was not persisted")
            if set(pending["completed_steps"]) != FEEDBACK_STEPS:
                raise AttemptError("feedback recovery steps are incomplete")
            response_hash = hashlib.sha256(answer.encode("utf-8")).hexdigest()
            deliveries.append(
                {
                    "feedback_id": feedback_id,
                    "run_id": pending["run_id"],
                    "evidence_id": pending["evidence_id"],
                    "hint_index": pending["hint_index"],
                    "delivered_at": _timestamp(self.now),
                    "response_hash": response_hash,
                    "acknowledged_at": None,
                }
            )
            feedback["pending"] = None

        return self.mutate(
            topic,
            attempt_id,
            "attempt_feedback_delivered",
            {"feedback_id": feedback_id},
            update,
            event_id=f"{attempt_id}:feedback:{feedback_id}:delivered",
            allow_completed_evidence=True,
        )

    def acknowledge_feedback(
        self, topic: str, attempt_id: str, feedback_id: str
    ) -> dict[str, object]:
        def update(value: dict[str, object]) -> None:
            feedback = value["feedback"]
            assert isinstance(feedback, dict)
            deliveries = feedback["deliveries"]
            assert isinstance(deliveries, list)
            matching = [
                item for item in deliveries if item.get("feedback_id") == feedback_id
            ]
            if len(matching) != 1:
                raise AttemptError("feedback delivery is missing")
            if matching[0].get("acknowledged_at") is None:
                matching[0]["acknowledged_at"] = _timestamp(self.now)

        return self.mutate(
            topic,
            attempt_id,
            "attempt_feedback_acknowledged",
            {"feedback_id": feedback_id},
            update,
            event_id=f"{attempt_id}:feedback:{feedback_id}:acknowledged",
            allow_completed_evidence=True,
        )

    def complete(
        self,
        topic: str,
        attempt_id: str,
        *,
        disposition: str | None = None,
    ) -> dict[str, object]:
        def update(value: dict[str, object]) -> None:
            if value["status"] == "completed":
                return
            if value["status"] != "active":
                raise AttemptError("only an active attempt can be completed")
            assistance = value["assistance"]
            assert isinstance(assistance, Mapping)
            assisted = bool(
                assistance.get("hints")
                or assistance.get("scaffolding")
                or assistance.get("editorial_exposures")
                or assistance.get("full_solution_exposures")
            )
            selected = disposition or (
                "solved_with_help" if assisted else "solved_independently"
            )
            if selected not in {"solved_independently", "solved_with_help", "partial"}:
                raise AttemptError("completed attempt disposition is invalid")
            if assistance.get("full_solution_exposures") and selected == "solved_independently":
                selected = "solved_with_help"
            value["status"] = "completed"
            value["disposition"] = selected
            value["completed_at"] = _timestamp(self.now)

        return self.mutate(
            topic,
            attempt_id,
            "attempt_completed",
            {},
            update,
            allow_completed_evidence=True,
        )

    def add_evidence(
        self,
        topic: str,
        attempt_id: str,
        evidence_id: str,
        *,
        kind: str = "review",
    ) -> dict[str, object]:
        reference = {
            "evidence_id": _bounded_text(evidence_id, "evidence_id", allow_empty=False),
            "kind": _bounded_text(kind, "evidence kind", allow_empty=False),
            "added_at": _timestamp(self.now),
        }

        def update(value: dict[str, object]) -> None:
            refs = value["evidence_refs"]
            assert isinstance(refs, list)
            if any(item.get("evidence_id") == evidence_id for item in refs):
                return
            refs.append(reference)

        return self.mutate(
            topic,
            attempt_id,
            "attempt_evidence_added",
            reference,
            update,
            event_id=f"{attempt_id}:evidence:{evidence_id}",
            allow_completed_evidence=True,
        )

    def record_assistance(
        self,
        topic: str,
        attempt_id: str,
        *,
        hint: str = "",
        scaffolding: str = "",
        intervention: str = "",
        editorial_exposed: bool = False,
        full_solution_exposed: bool = False,
        run_id: str | None = None,
        evidence_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        additions = {
            "hint": _bounded_text(hint, "hint"),
            "scaffolding": _bounded_text(scaffolding, "scaffolding"),
            "intervention": _bounded_text(intervention, "intervention"),
            "editorial_exposed": bool(editorial_exposed),
            "full_solution_exposed": bool(full_solution_exposed),
        }

        def update(value: dict[str, object]) -> None:
            assistance = value["assistance"]
            assert isinstance(assistance, dict)
            for source, target in (
                ("hint", "hints"),
                ("scaffolding", "scaffolding"),
                ("intervention", "tutor_interventions"),
            ):
                item = additions[source]
                values = assistance[target]
                assert isinstance(values, list)
                if item:
                    deterministic_id = (
                        "entry_"
                        + hashlib.sha256(
                            f"{operation_id}:{target}".encode("utf-8")
                        ).hexdigest()[:32]
                        if operation_id
                        else None
                    )
                    if deterministic_id and any(
                        entry.get("entry_id") == deterministic_id for entry in values
                    ):
                        continue
                    values.append(
                        {
                            **self._timed_entry(
                                str(item), run_id=run_id, evidence_id=evidence_id
                            ),
                            **({"entry_id": deterministic_id} if deterministic_id else {}),
                        }
                    )
            if additions["editorial_exposed"]:
                assistance["editorial_exposures"].append(
                    self._timed_entry(
                        "editorial exposed", run_id=run_id, evidence_id=evidence_id
                    )
                )
            if additions["full_solution_exposed"]:
                assistance["full_solution_exposures"].append(
                    self._timed_entry(
                        "full solution exposed",
                        run_id=run_id,
                        evidence_id=evidence_id,
                    )
                )
        return self.mutate(
            topic,
            attempt_id,
            "attempt_assistance_recorded",
            additions,
            update,
            event_id=(
                f"{attempt_id}:assistance:{operation_id}"
                if operation_id
                else None
            ),
            allow_completed_evidence=True,
        )

    def record_reasoning(
        self,
        topic: str,
        attempt_id: str,
        *,
        complexity: str = "",
        edge_cases: str = "",
        reflection: str = "",
        run_id: str | None = None,
        evidence_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        additions = {
            "complexity": _bounded_text(complexity, "complexity"),
            "edge_cases": _bounded_text(edge_cases, "edge cases"),
            "reflection": _bounded_text(reflection, "reflection"),
        }

        def update(value: dict[str, object]) -> None:
            reasoning = value["reasoning"]
            assert isinstance(reasoning, dict)
            entries = reasoning["entries"]
            assert isinstance(entries, list)
            for field, text in additions.items():
                if text:
                    deterministic_id = (
                        "entry_"
                        + hashlib.sha256(
                            f"{operation_id}:{field}".encode("utf-8")
                        ).hexdigest()[:32]
                        if operation_id
                        else None
                    )
                    if deterministic_id and any(
                        entry.get("entry_id") == deterministic_id for entry in entries
                    ):
                        continue
                    entries.append(
                        {
                            **self._timed_entry(
                                str(text), run_id=run_id, evidence_id=evidence_id
                            ),
                            "kind": field,
                            **({"entry_id": deterministic_id} if deterministic_id else {}),
                        }
                    )

        return self.mutate(
            topic,
            attempt_id,
            "attempt_reasoning_recorded",
            {key: value for key, value in additions.items() if value},
            update,
            event_id=(
                f"{attempt_id}:reasoning:{operation_id}"
                if operation_id
                else None
            ),
            allow_completed_evidence=True,
        )

    def record_independent_transfer(
        self,
        topic: str,
        attempt_id: str,
        *,
        evidence_id: str,
        transfer_attempt_id: str,
        problem: Mapping[str, object],
    ) -> dict[str, object]:
        transfer = self.load(topic, transfer_attempt_id)
        source_execution = transfer.get("execution")
        source_bundle = (
            source_execution.get("activity_bundle")
            if isinstance(source_execution, Mapping)
            else None
        )
        source_skills = (
            source_bundle.get("concept_ids")
            if isinstance(source_bundle, Mapping)
            else None
        )
        target = self.load(topic, attempt_id)
        target_execution = target.get("execution")
        target_bundle = (
            target_execution.get("activity_bundle")
            if isinstance(target_execution, Mapping)
            else None
        )
        target_skills = (
            target_bundle.get("concept_ids")
            if isinstance(target_bundle, Mapping)
            else None
        )
        evidence = next(
            (
                item
                for item in transfer.get("evidence_refs", [])  # type: ignore[union-attr]
                if isinstance(item, Mapping) and item.get("evidence_id") == evidence_id
            ),
            None,
        )
        passed_run = next(
            (
                run
                for run in transfer.get("test_runs", [])  # type: ignore[union-attr]
                if isinstance(run, Mapping)
                and run.get("evidence_id") == evidence_id
                and run.get("outcome") == "passed"
                and run.get("passed") is True
            ),
            None,
        )
        if (
            transfer.get("status") != "completed"
            or transfer.get("disposition") != "solved_independently"
            or transfer.get("problem") != dict(problem)
            or not isinstance(evidence, Mapping)
            or evidence.get("kind") != "pytest_result"
            or not isinstance(passed_run, Mapping)
            or not isinstance(source_skills, list)
            or not isinstance(target_skills, list)
            or not set(source_skills) & set(target_skills)
        ):
            raise AttemptError(
                "independent transfer requires a passed novel problem for the same skill"
            )
        reference = {
            "evidence_id": _bounded_text(evidence_id, "transfer evidence", allow_empty=False),
            "attempt_id": _bounded_text(
                transfer_attempt_id, "transfer attempt", allow_empty=False
            ),
            "problem": _validate_problem_reference(problem),
            "verified_at": _timestamp(self.now),
        }

        def update(value: dict[str, object]) -> None:
            if transfer_attempt_id == attempt_id or reference["problem"] == value["problem"]:
                raise AttemptError("independent transfer must use a different attempt and problem")
            follow_up = value["follow_up"]
            assistance = value["assistance"]
            assert isinstance(follow_up, dict) and isinstance(assistance, dict)
            refs = follow_up["independent_transfer_evidence"]
            assert isinstance(refs, list)
            if any(item.get("evidence_id") == evidence_id for item in refs):
                return
            refs.append(reference)
            assistance["independent_transfer_cleared_at"] = reference["verified_at"]

        return self.mutate(
            topic,
            attempt_id,
            "attempt_independent_transfer_verified",
            reference,
            update,
            event_id=f"{attempt_id}:transfer:{evidence_id}",
            allow_completed_evidence=True,
        )

    def resolve_workspace(self, record: Mapping[str, object]) -> Path:
        validated = validate_attempt(record)
        workspace = self.topics_root / str(validated["workspace_ref"])
        return self._safe_workspace(workspace, str(validated["topic"]))

    def workspace_reference(self, topic: str, workspace: Path) -> str:
        safe = self._safe_workspace(workspace, topic)
        return Path("drills", topic, safe.name).as_posix()

    def _safe_workspace(self, workspace: Path, topic: str) -> Path:
        raw_owned = self.topics_root / "drills" / topic
        for candidate_parent in (self.topics_root, self.topics_root / "drills", raw_owned):
            if candidate_parent.exists() and candidate_parent.is_symlink():
                raise AttemptError("attempt workspace parent is unsafe")
        owned = raw_owned.resolve()
        if workspace.is_symlink():
            raise AttemptError("attempt workspace is missing or unsafe")
        candidate = workspace.expanduser().absolute()
        resolved = candidate.resolve()
        if (
            candidate.parent.resolve() != owned
            or resolved.parent != owned
            or candidate.name in {"", ".", ".."}
        ):
            raise AttemptError("attempt workspace is outside its owned directory")
        if not candidate.exists() or not candidate.is_file():
            raise AttemptError("attempt workspace is missing or unsafe")
        return resolved

    def _read_workspace(self, workspace: Path) -> bytes:
        topic = workspace.parent.name
        safe = self._safe_workspace(workspace, topic)
        if os.name != "nt":
            directory_fd = self._open_workspace_directory(topic)
            try:
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(safe.name, flags, dir_fd=directory_fd)
                try:
                    opened = os.fstat(descriptor)
                    if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_SNAPSHOT_BYTES * 4:
                        raise AttemptError("attempt workspace is too large or unsafe")
                    chunks: list[bytes] = []
                    remaining = MAX_SNAPSHOT_BYTES * 4 + 1
                    while remaining:
                        chunk = os.read(descriptor, min(64 * 1024, remaining))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                finally:
                    os.close(descriptor)
            except OSError as exc:
                raise AttemptError("attempt workspace could not be read safely") from exc
            finally:
                os.close(directory_fd)
            data = b"".join(chunks)
            if len(data) > MAX_SNAPSHOT_BYTES * 4:
                raise AttemptError("attempt workspace is too large to snapshot")
            return data

        try:
            return windows_attempt_io.read_workspace_file(
                self.topics_root,
                topic,
                safe.name,
                max_bytes=MAX_SNAPSHOT_BYTES * 4,
            )
        except windows_attempt_io.WindowsSecureIOError as exc:
            raise AttemptError("attempt workspace could not be read safely") from exc

    def _open_workspace_directory(self, topic: str) -> int:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptors: list[int] = []
        try:
            root_fd = os.open(self.topics_root, flags)
            descriptors.append(root_fd)
            drills_fd = os.open("drills", flags, dir_fd=root_fd)
            descriptors.append(drills_fd)
            topic_fd = os.open(topic, flags, dir_fd=drills_fd)
            descriptors.append(topic_fd)
            descriptors.pop()
            return topic_fd
        except OSError as exc:
            raise AttemptError("attempt workspace parent could not be opened safely") from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def _write_workspace_exclusive(self, topic: str, path: Path, data: bytes) -> None:
        safe_parent = self.topics_root / "drills" / topic
        if path.absolute().parent.resolve() != safe_parent.resolve():
            raise AttemptError("retry workspace is outside its owned directory")
        if os.name == "nt":
            try:
                windows_attempt_io.create_workspace_file_exclusive(
                    self.topics_root, topic, path.name, data
                )
            except windows_attempt_io.WindowsSecureIOError as exc:
                raise AttemptError(
                    f"retry workspace could not be created safely: {exc}"
                ) from exc
            return
        directory_fd = self._open_workspace_directory(topic)
        descriptor = -1
        try:
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(path.name, flags, 0o600, dir_fd=directory_fd)
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
            os.fsync(directory_fd)
        except OSError as exc:
            raise AttemptError("retry workspace could not be created safely") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(directory_fd)

    def _commit(
        self,
        record: dict[str, object],
        event_type: str,
        event_data: Mapping[str, object],
        *,
        event_id: str | None = None,
        previous_revision: int,
    ) -> None:
        topic = str(record["topic"])
        attempt_id = str(record["attempt_id"])
        identifier = event_id or f"event_{uuid4().hex}"
        event = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event_id": identifier,
            "attempt_id": attempt_id,
            "attempt_revision": record["revision"],
            "ts": _timestamp(self.now),
            "event_type": event_type,
            "data": copy.deepcopy(dict(event_data)),
        }
        validated_record = validate_attempt(record)
        validated_event = _validate_event(event, attempt_id)
        encoded_event = (
            json.dumps(
                validated_event,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded_event) > MAX_EVENT_LINE_BYTES:
            raise AttemptError("attempt event is too large")
        journal = {
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "topic": topic,
            "topic_generation": record["topic_generation"],
            "previous_revision": previous_revision,
            "next_revision": record["revision"],
            "created_at": _timestamp(self.now),
            "record": validated_record,
            "event": validated_event,
        }
        encoded_journal = (
            json.dumps(journal, indent=2, sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
        if len(encoded_journal) > MAX_RECORD_BYTES + MAX_EVENT_LINE_BYTES:
            raise AttemptError("attempt journal is too large")
        journal_path = self.journal_path(topic, attempt_id)
        self.write_atomic(journal_path, encoded_journal.decode("utf-8"))
        self._recover(topic, attempt_id)

    def _recover(self, topic: str, attempt_id: str) -> None:
        journal_path = self.journal_path(topic, attempt_id)
        if not journal_path.exists():
            return
        raw = self._read_json(journal_path, MAX_RECORD_BYTES + MAX_TEXT)
        required = {
            "schema_version",
            "attempt_id",
            "topic",
            "topic_generation",
            "previous_revision",
            "next_revision",
            "created_at",
            "record",
            "event",
        }
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise AttemptError("attempt journal envelope is malformed")
        if (
            raw["schema_version"] != JOURNAL_SCHEMA_VERSION
            or raw["attempt_id"] != attempt_id
            or raw["topic"] != topic
            or not isinstance(raw["previous_revision"], int)
            or isinstance(raw["previous_revision"], bool)
            or not isinstance(raw["next_revision"], int)
            or isinstance(raw["next_revision"], bool)
            or raw["next_revision"] != raw["previous_revision"] + 1
        ):
            raise AttemptError("attempt journal envelope is invalid")
        _validate_timestamp(raw["created_at"], "attempt journal created_at")
        if self.current_generation(topic) != raw["topic_generation"]:
            raise AttemptError("attempt journal belongs to a deleted or recreated topic")
        record = raw.get("record")
        event = raw.get("event")
        if not isinstance(record, Mapping) or not isinstance(event, Mapping):
            raise AttemptError("attempt journal is malformed")
        validated = validate_attempt(record)
        validated_event = _validate_event(event, attempt_id)
        if (
            validated["attempt_id"] != attempt_id
            or validated["topic"] != topic
            or validated["topic_generation"] != raw["topic_generation"]
            or validated["revision"] != raw["next_revision"]
            or validated_event["attempt_revision"] != raw["next_revision"]
        ):
            raise AttemptError("attempt journal identity is invalid")
        events_path = self.events_path(topic, attempt_id)
        for current_event in iter_events(events_path, attempt_id=attempt_id):
            if current_event["event_id"] == validated_event["event_id"]:
                if current_event != validated_event:
                    raise AttemptError("attempt event id has conflicting content")
                continue
            if current_event["attempt_revision"] == validated_event["attempt_revision"]:
                raise AttemptError("attempt revision already has a different event")
        state_path = self.state_path(topic, attempt_id)
        if state_path.exists():
            current = self._read_state(state_path)
            current_revision = current["revision"]
            if current_revision == raw["next_revision"]:
                if current != validated:
                    raise AttemptError("attempt journal conflicts with current revision")
            elif current_revision == raw["previous_revision"]:
                self.write_atomic(
                    state_path,
                    json.dumps(validated, indent=2, sort_keys=True) + "\n",
                )
            else:
                raise AttemptError("attempt journal is stale and cannot be replayed")
        elif raw["previous_revision"] == 0:
            self.write_atomic(
                state_path,
                json.dumps(validated, indent=2, sort_keys=True) + "\n",
            )
        else:
            raise AttemptError("attempt journal cannot skip a missing prior revision")
        self._append_event_once(topic, attempt_id, validated_event)
        journal_path.unlink()

    def _append_event_once(
        self, topic: str, attempt_id: str, event: dict[str, object]
    ) -> None:
        path = self.events_path(topic, attempt_id)
        if path.is_symlink():
            raise AttemptError("attempt event log is unsafe")
        validated_event = _validate_event(event, attempt_id)
        existing_events = list(iter_events(path, attempt_id=attempt_id))
        event_id = validated_event["event_id"]
        for current in existing_events:
            if current["event_id"] == event_id:
                if current != validated_event:
                    raise AttemptError("attempt event id has conflicting content")
                return
            if current["attempt_revision"] == validated_event["attempt_revision"]:
                raise AttemptError("attempt revision already has a different event")
        text = (
            _read_bounded_text(path, MAX_EVENTS_BYTES, "attempt event log")
            if path.exists()
            else ""
        )
        if text and not text.endswith("\n"):
            text += "\n"
        text += json.dumps(validated_event, sort_keys=True) + "\n"
        if len(text.encode("utf-8")) > MAX_EVENTS_BYTES:
            raise AttemptError("attempt event log is too large")
        self.write_atomic(path, text)

    def _read_state(self, path: Path) -> dict[str, object]:
        raw = self._read_json(path, MAX_RECORD_BYTES)
        if not isinstance(raw, Mapping):
            raise AttemptError("attempt record is malformed")
        return validate_attempt(raw)

    @staticmethod
    def _read_json(path: Path, limit: int) -> object:
        if not path.exists():
            raise AttemptError("attempt not found")
        try:
            return json.loads(_read_bounded_text(path, limit, "attempt storage"))
        except json.JSONDecodeError as exc:
            raise AttemptError("attempt storage is corrupted") from exc

    @staticmethod
    def _validate_id(attempt_id: str) -> None:
        if not ATTEMPT_ID_PATTERN.fullmatch(attempt_id):
            raise AttemptError("invalid attempt_id")


def iter_events(
    path: Path, *, attempt_id: str | None = None
) -> Iterator[dict[str, object]]:
    """Yield a strict event replay stream for diagnostics and tests."""
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise AttemptError("attempt event log is unsafe")
    seen: set[str] = set()
    inferred_id = attempt_id
    suffix = ".events.jsonl"
    if inferred_id is None and path.name.endswith(suffix):
        inferred_id = path.name[: -len(suffix)]
    if inferred_id is None or not ATTEMPT_ID_PATTERN.fullmatch(inferred_id):
        raise AttemptError("attempt event log identity is invalid")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_EVENTS_BYTES:
            os.close(descriptor)
            raise AttemptError("attempt event log is too large or unsafe")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            total = 0
            for line in handle:
                total += len(line.encode("utf-8"))
                if total > MAX_EVENTS_BYTES or len(line.encode("utf-8")) > MAX_EVENT_LINE_BYTES:
                    raise AttemptError("attempt event log is too large")
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise AttemptError("attempt event log is corrupted") from exc
                event = _validate_event(raw, inferred_id)
                if event["event_id"] in seen:
                    continue
                seen.add(str(event["event_id"]))
                yield event
    except OSError as exc:
        raise AttemptError("attempt event log could not be read") from exc
    except UnicodeDecodeError as exc:
        raise AttemptError("attempt event log is not UTF-8") from exc
