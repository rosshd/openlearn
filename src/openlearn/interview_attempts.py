"""Durable local interview-problem attempts and resumable workspaces."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

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
    for field in (
        "started_at",
        "last_active_at",
        "completed_at",
        "abandoned_at",
        "cancelled_at",
        "resumed_at",
    ):
        timestamp = value[field]
        if timestamp is not None and not isinstance(timestamp, str):
            raise AttemptError(f"{field} is invalid")
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
    identifiers: set[str] = set()
    for run in test_runs:
        if not isinstance(run, Mapping) or not isinstance(run.get("run_id"), str):
            raise AttemptError("attempt test run is invalid")
        run_id = str(run["run_id"])
        if run_id in identifiers:
            raise AttemptError("attempt contains duplicate test runs")
        identifiers.add(run_id)
        outcome = run.get("outcome")
        if outcome not in LEARNER_OUTCOMES | INFRASTRUCTURE_OUTCOMES | {"pending"}:
            raise AttemptError("attempt test outcome is invalid")
        if run.get("learner_failure") is not (outcome in LEARNER_OUTCOMES - {"passed"}):
            raise AttemptError("attempt runner classification is inconsistent")
    assistance = value["assistance"]
    reasoning = value["reasoning"]
    follow_up = value["follow_up"]
    if not isinstance(assistance, Mapping) or not isinstance(reasoning, Mapping):
        raise AttemptError("attempt assistance or reasoning is invalid")
    if not isinstance(follow_up, Mapping):
        raise AttemptError("attempt follow-up is invalid")
    if _json_size(value) > MAX_RECORD_BYTES:
        raise AttemptError("attempt record is too large")
    return copy.deepcopy(dict(value))


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
        attempt_id: str | None = None,
    ) -> dict[str, object]:
        if self.current_generation(topic) != topic_generation:
            raise AttemptError("topic changed or was deleted before attempt creation")
        identifier = attempt_id or f"attempt_{uuid4().hex}"
        self._validate_id(identifier)
        workspace_ref = self.workspace_reference(topic, workspace)
        timestamp = _timestamp(self.now)
        assistance_value = {
            "hints": [],
            "scaffolding": [],
            "editorial_exposed": False,
            "full_solution_exposed": False,
            "tutor_interventions": [],
        }
        if assistance:
            assistance_value.update(copy.deepcopy(dict(assistance)))
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
                "reasoning": {
                    "complexity": "",
                    "edge_cases": "",
                    "reflection": "",
                },
                "evidence_refs": [],
                "follow_up": {"scheduled_at": None, "transfer_activity_id": None},
            }
        )
        state_path = self.state_path(topic, identifier)
        with self.lock(state_path):
            if state_path.exists():
                raise AttemptError("attempt already exists")
            self._commit(record, "attempt_created", {})
        return record

    def load(self, topic: str, attempt_id: str) -> dict[str, object]:
        state_path = self.state_path(topic, attempt_id)
        with self.lock(state_path):
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
        with self.lock(state_path):
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
            self._commit(validated, event_type, event_data, event_id=event_id)
            return validated

    def resume(self, topic: str, attempt_id: str) -> dict[str, object]:
        def update(value: dict[str, object]) -> None:
            if value["status"] != "active":
                raise AttemptError("only unfinished active attempts can be resumed")
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
        self.write_atomic(retry_workspace, source_text)
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
        self, topic: str, attempt_id: str, *, visibility: str = "public"
    ) -> tuple[dict[str, object], str]:
        if visibility not in {"public", "hidden"}:
            raise AttemptError("test visibility is invalid")
        run_id = f"run_{uuid4().hex}"
        run = {
            "run_id": run_id,
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
        }

        def update(value: dict[str, object]) -> None:
            runs = value["test_runs"]
            assert isinstance(runs, list)
            if len(runs) >= MAX_TEST_RUNS:
                raise AttemptError("attempt has too many test runs")
            runs.append(run)

        record = self.mutate(
            topic,
            attempt_id,
            "attempt_test_started",
            {"run_id": run_id, "visibility": visibility},
            update,
            event_id=f"{attempt_id}:{run_id}:started",
        )
        return record, run_id

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
                    "learner_failure": outcome in LEARNER_OUTCOMES - {"passed"},
                    "passed": outcome == "passed",
                    "timeout": outcome == "timeout",
                    "failure_class": (
                        outcome
                        if outcome in LEARNER_OUTCOMES - {"passed"}
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

    def complete(
        self,
        topic: str,
        attempt_id: str,
        *,
        disposition: str | None = None,
    ) -> dict[str, object]:
        def update(value: dict[str, object]) -> None:
            if value["status"] != "active":
                raise AttemptError("only an active attempt can be completed")
            assistance = value["assistance"]
            assert isinstance(assistance, Mapping)
            assisted = bool(
                assistance.get("hints")
                or assistance.get("scaffolding")
                or assistance.get("editorial_exposed")
                or assistance.get("full_solution_exposed")
            )
            selected = disposition or (
                "solved_with_help" if assisted else "solved_independently"
            )
            if selected not in {"solved_independently", "solved_with_help", "partial"}:
                raise AttemptError("completed attempt disposition is invalid")
            if assistance.get("full_solution_exposed") and selected == "solved_independently":
                selected = "solved_with_help"
            value["status"] = "completed"
            value["disposition"] = selected
            value["completed_at"] = _timestamp(self.now)

        return self.mutate(topic, attempt_id, "attempt_completed", {}, update)

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
                if item and item not in values:
                    values.append(item)
            if additions["editorial_exposed"]:
                assistance["editorial_exposed"] = True
            if additions["full_solution_exposed"]:
                assistance["full_solution_exposed"] = True

        event_id = hashlib.sha256(
            json.dumps(additions, sort_keys=True).encode("utf-8")
        ).hexdigest()[:32]
        return self.mutate(
            topic,
            attempt_id,
            "attempt_assistance_recorded",
            additions,
            update,
            event_id=f"{attempt_id}:assistance:{event_id}",
        )

    def record_reasoning(
        self,
        topic: str,
        attempt_id: str,
        *,
        complexity: str = "",
        edge_cases: str = "",
        reflection: str = "",
    ) -> dict[str, object]:
        additions = {
            "complexity": _bounded_text(complexity, "complexity"),
            "edge_cases": _bounded_text(edge_cases, "edge cases"),
            "reflection": _bounded_text(reflection, "reflection"),
        }

        def update(value: dict[str, object]) -> None:
            reasoning = value["reasoning"]
            assert isinstance(reasoning, dict)
            for field, text in additions.items():
                if text:
                    reasoning[field] = text

        return self.mutate(
            topic,
            attempt_id,
            "attempt_reasoning_recorded",
            {key: value for key, value in additions.items() if value},
            update,
        )

    def resolve_workspace(self, record: Mapping[str, object]) -> Path:
        validated = validate_attempt(record)
        workspace = self.topics_root / str(validated["workspace_ref"])
        return self._safe_workspace(workspace, str(validated["topic"]))

    def workspace_reference(self, topic: str, workspace: Path) -> str:
        safe = self._safe_workspace(workspace, topic)
        try:
            return safe.relative_to(self.topics_root.resolve()).as_posix()
        except ValueError as exc:
            raise AttemptError("attempt workspace is outside local topic storage") from exc

    def _safe_workspace(self, workspace: Path, topic: str) -> Path:
        raw_owned = self.topics_root / "drills" / topic
        for candidate_parent in (self.topics_root, self.topics_root / "drills", raw_owned):
            if candidate_parent.exists() and candidate_parent.is_symlink():
                raise AttemptError("attempt workspace parent is unsafe")
        owned = (self.topics_root / "drills" / topic).resolve()
        if workspace.is_symlink():
            raise AttemptError("attempt workspace is missing or unsafe")
        candidate = workspace.expanduser().resolve()
        if not candidate.is_relative_to(owned):
            raise AttemptError("attempt workspace is outside its owned directory")
        if not candidate.exists() or not candidate.is_file() or candidate.is_symlink():
            raise AttemptError("attempt workspace is missing or unsafe")
        return candidate

    def _read_workspace(self, workspace: Path) -> bytes:
        try:
            before = workspace.stat()
            if before.st_size > MAX_SNAPSHOT_BYTES * 4:
                raise AttemptError("attempt workspace is too large to snapshot")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(workspace, flags)
            try:
                data = os.read(descriptor, MAX_SNAPSHOT_BYTES * 4 + 1)
                after = os.fstat(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise AttemptError("attempt workspace could not be read safely") from exc
        if len(data) > MAX_SNAPSHOT_BYTES * 4:
            raise AttemptError("attempt workspace is too large to snapshot")
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise AttemptError("attempt workspace changed during snapshot")
        return data

    def _commit(
        self,
        record: dict[str, object],
        event_type: str,
        event_data: Mapping[str, object],
        *,
        event_id: str | None = None,
    ) -> None:
        topic = str(record["topic"])
        attempt_id = str(record["attempt_id"])
        identifier = event_id or f"event_{uuid4().hex}"
        event = {
            "schema_version": 1,
            "event_id": identifier,
            "attempt_id": attempt_id,
            "attempt_revision": record["revision"],
            "ts": _timestamp(self.now),
            "event_type": event_type,
            "data": copy.deepcopy(dict(event_data)),
        }
        journal = {"record": record, "event": event}
        journal_path = self.journal_path(topic, attempt_id)
        self.write_atomic(journal_path, json.dumps(journal, indent=2, sort_keys=True) + "\n")
        self._recover(topic, attempt_id)

    def _recover(self, topic: str, attempt_id: str) -> None:
        journal_path = self.journal_path(topic, attempt_id)
        if not journal_path.exists():
            return
        raw = self._read_json(journal_path, MAX_RECORD_BYTES + MAX_TEXT)
        if not isinstance(raw, Mapping):
            raise AttemptError("attempt journal is malformed")
        record = raw.get("record")
        event = raw.get("event")
        if not isinstance(record, Mapping) or not isinstance(event, Mapping):
            raise AttemptError("attempt journal is malformed")
        validated = validate_attempt(record)
        if validated["attempt_id"] != attempt_id or validated["topic"] != topic:
            raise AttemptError("attempt journal identity is invalid")
        self.write_atomic(
            self.state_path(topic, attempt_id),
            json.dumps(validated, indent=2, sort_keys=True) + "\n",
        )
        self._append_event_once(topic, attempt_id, dict(event))
        journal_path.unlink()

    def _append_event_once(
        self, topic: str, attempt_id: str, event: dict[str, object]
    ) -> None:
        path = self.events_path(topic, attempt_id)
        if path.is_symlink():
            raise AttemptError("attempt event log is unsafe")
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if len(text.encode("utf-8")) > MAX_EVENTS_BYTES:
            raise AttemptError("attempt event log is too large")
        event_id = event.get("event_id")
        for line in text.splitlines():
            try:
                current = json.loads(line)
            except json.JSONDecodeError:
                raise AttemptError("attempt event log is corrupted")
            if isinstance(current, Mapping) and current.get("event_id") == event_id:
                return
        if text and not text.endswith("\n"):
            text += "\n"
        text += json.dumps(event, sort_keys=True) + "\n"
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
        if path.is_symlink() or not path.is_file():
            raise AttemptError("attempt storage is unsafe")
        if path.stat().st_size > limit:
            raise AttemptError("attempt storage is too large")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AttemptError("attempt storage is corrupted") from exc

    @staticmethod
    def _validate_id(attempt_id: str) -> None:
        if not ATTEMPT_ID_PATTERN.fullmatch(attempt_id):
            raise AttemptError("invalid attempt_id")


def iter_events(path: Path) -> Iterator[dict[str, object]]:
    """Yield a strict event replay stream for diagnostics and tests."""
    if not path.exists():
        return
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AttemptError("attempt event log is corrupted") from exc
        if not isinstance(event, dict) or not isinstance(event.get("event_id"), str):
            raise AttemptError("attempt event is malformed")
        if event["event_id"] in seen:
            continue
        seen.add(str(event["event_id"]))
        yield event
