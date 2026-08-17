from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
from collections.abc import Mapping
from contextlib import nullcontext
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from openlearn.models import TutorSessionKind


TurnIntent = Literal["answer", "question", "confusion", "navigation"]
ProgressionIntent = Literal["continue", "skip", "practice", "revisit", "deepen"]
OperationStatus = Literal[
    "saved",
    "reserved",
    "judging",
    "generating",
    "generated",
    "validating",
    "committed",
    "retryable_error",
    "conflict",
]


class TutorOperationError(RuntimeError):
    pass


class TutorConflictError(TutorOperationError):
    pass


class FollowUpProposalError(TutorOperationError):
    pass


class FollowUpProposalConflictError(FollowUpProposalError):
    pass


class FollowUpProviderNotReadyError(FollowUpProposalError):
    pass


@dataclass(frozen=True)
class TutorMove:
    move_id: str
    revision: int
    kind: str
    content: str
    action_kind: str
    prompt: str
    history_summary: str


@dataclass(frozen=True)
class TutorTurnResult:
    submission_id: str
    status: OperationStatus
    input_status: str
    message_kind: str
    move: TutorMove | None
    error_code: str | None = None
    error_message: str | None = None
    preview: str | None = None
    payload_hash: str | None = None


@dataclass
class _LiveTurnState:
    phase: OperationStatus = "saved"
    preview: str | None = None


@dataclass(frozen=True)
class _LessonSource:
    content: str
    lesson_id: str
    title: str
    revision: int
    skill_ref: dict[str, str] | None


_GENERATION_LOCK = threading.RLock()
_COURSE_LOCKS: dict[str, threading.RLock] = {}
_COURSE_LOCKS_GUARD = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="openlearn-tutor")
_FUTURES: dict[tuple[str, str], Future[TutorTurnResult]] = {}
_FUTURES_GUARD = threading.Lock()
_RUNNING: set[tuple[str, str]] = set()
_LIVE_TURNS: dict[tuple[str, str], _LiveTurnState] = {}
_OPERATION_TIMEOUT = timedelta(minutes=3)
_FOLLOW_UP_SCHEMA_VERSION = 1
_FOLLOW_UP_INTEREST_LIMIT = 2_000
_FOLLOW_UP_CLAIM_TIMEOUT = timedelta(minutes=5)
_FOLLOW_UP_RUNNING: dict[tuple[str, str], str] = {}
_FOLLOW_UP_RUNNING_GUARD = threading.Lock()


def _progression_checkpoint(_stage: str) -> None:
    """Fault-injection seam for durable curriculum-operation boundaries."""


def _course_lock(slug: str) -> threading.RLock:
    with _COURSE_LOCKS_GUARD:
        return _COURSE_LOCKS.setdefault(slug, threading.RLock())


def _active_operation_key(session_kind: TutorSessionKind) -> str:
    return "active_side_chat" if session_kind == "side_chat" else "active_turn"


def _visible_curriculum_skill_ref(
    state: Mapping[str, object],
) -> dict[str, str] | None:
    canonical = state.get("interview_curriculum")
    if not isinstance(canonical, Mapping):
        return None
    cursor = canonical.get("cursor")
    active = canonical.get("active_operation")
    if isinstance(active, Mapping):
        rollback = active.get("rollback")
        rollback_cursor = rollback.get("cursor") if isinstance(rollback, Mapping) else None
        rollback_value = (
            rollback_cursor.get("value")
            if isinstance(rollback_cursor, Mapping)
            and rollback_cursor.get("present") is True
            else None
        )
        if isinstance(rollback_value, Mapping):
            cursor = rollback_value
    ref = cursor.get("skill_ref") if isinstance(cursor, Mapping) else None
    keys = ("graph_id", "graph_version", "mastery_policy_version", "skill_id")
    if not isinstance(ref, Mapping) or not all(isinstance(ref.get(key), str) for key in keys):
        return None
    return {key: str(ref[key]) for key in keys}


def _historical_lesson_receipts(
    slug: str, state: dict[str, object]
) -> list[dict[str, object]]:
    """Read the bounded durable navigation receipts that authorize old lessons."""
    from openlearn import cli

    submission_ids: set[str] = set()
    raw_receipts = state.get("_turn_receipts")
    if isinstance(raw_receipts, dict):
        for key in raw_receipts:
            match = re.fullmatch(r"operation_([a-f0-9]{32})", str(key))
            if match is not None:
                submission_ids.add(str(UUID(hex=match.group(1))))
    receipt_directory = cli.topic_operation_receipts_dir(slug)
    if receipt_directory.exists():
        for path in receipt_directory.iterdir():
            match = re.fullmatch(r"operation_([a-f0-9]{32})\.json", path.name)
            if match is not None:
                submission_ids.add(str(UUID(hex=match.group(1))))

    receipts: list[dict[str, object]] = []
    for submission_id in sorted(submission_ids):
        receipt = cli.load_operation_receipt(slug, submission_id, state=state)
        if isinstance(receipt, dict) and receipt.get("receipt_kind") != "caught_up":
            receipts.append(receipt)
    return receipts


def _resolved_side_chat_source(
    slug: str,
    state: dict[str, object],
    lesson_id: str | None,
    title: str | None,
    revision: int | None,
) -> _LessonSource:
    """Resolve a current or historical visible lesson from durable identities."""
    from openlearn import application, cli

    projection = application.interview_learning(slug)
    if projection is None:
        topic = cli.read_topic(slug)
        source_entry = cli.last_tutor_lesson_entry(topic)
        content = source_entry[1]["response"].strip() if source_entry else ""
        expected_id = cli.tutor_lesson_entry_id(source_entry[1]) if source_entry else ""
        expected_title = cli.tutor_response_focus_title(content) or "Saved lesson"
        current_revision = course_revision(slug)
        if any(value is not None for value in (lesson_id, title, revision)) and (
            lesson_id != expected_id
            or title != expected_title
            or revision != current_revision
        ):
            raise TutorConflictError(
                "The visible lesson changed. Your question was not submitted; refresh and retry."
            )
        return _LessonSource(
            content=content,
            lesson_id=expected_id,
            title=expected_title,
            revision=current_revision,
            skill_ref=None,
        )
    if lesson_id is None and title is None and revision is None:
        return _LessonSource(
            content=projection.committed_lesson.content,
            lesson_id=projection.committed_lesson.lesson_id,
            title=projection.committed_lesson.title,
            revision=projection.revision,
            skill_ref=_visible_curriculum_skill_ref(state),
        )
    if lesson_id is None or title is None or revision is None:
        raise TutorConflictError(
            "The visible lesson changed. Your question was not submitted; refresh and retry."
        )
    if (
        lesson_id == projection.committed_lesson.lesson_id
        and title == projection.committed_lesson.title
        and revision == projection.revision
    ):
        return _LessonSource(
            content=projection.committed_lesson.content,
            lesson_id=lesson_id,
            title=title,
            revision=revision,
            skill_ref=_visible_curriculum_skill_ref(state),
        )
    if revision >= projection.revision:
        raise TutorConflictError(
            "The visible lesson changed. Your question was not submitted; refresh and retry."
        )

    topic = cli.read_topic(slug)
    _context, session_log = cli.split_session_log(topic.body)
    entries = cli.session_entries(session_log)
    matching_entries = [
        entry
        for entry in entries
        if entry.get("kind") != cli.SIDE_CHAT_SESSION_KIND
        and entry.get("response", "").strip()
        and cli.tutor_lesson_entry_id(entry) == lesson_id
    ]
    if len(matching_entries) != 1:
        raise TutorConflictError(
            "The visible lesson changed. Your question was not submitted; refresh and retry."
        )
    entry = matching_entries[0]
    mutation_id = entry.get("mutation_id")
    receipts = [
        receipt
        for receipt in _historical_lesson_receipts(slug, state)
        if receipt.get("mutation_id") == mutation_id
    ]
    if len(receipts) != 1:
        raise TutorConflictError(
            "The visible lesson changed. Your question was not submitted; refresh and retry."
        )
    receipt = receipts[0]
    target = receipt.get("target")
    skill_ref = target.get("skill_ref") if isinstance(target, dict) else None
    identity_keys = (
        "graph_id",
        "graph_version",
        "mastery_policy_version",
        "skill_id",
    )
    normalized_ref = (
        {key: str(skill_ref[key]) for key in identity_keys}
        if isinstance(skill_ref, dict)
        and all(isinstance(skill_ref.get(key), str) and skill_ref.get(key) for key in identity_keys)
        else None
    )
    content = entry["response"].strip()
    response_hash = receipt.get("response_sha256")
    if (
        receipt.get("final_revision") != revision
        or not isinstance(target, dict)
        or target.get("skill_label") != title
        or normalized_ref is None
        or not isinstance(response_hash, str)
        or hashlib.sha256(content.encode("utf-8")).hexdigest() != response_hash
    ):
        raise TutorConflictError(
            "The visible lesson changed. Your question was not submitted; refresh and retry."
        )
    return _LessonSource(
        content=content,
        lesson_id=lesson_id,
        title=title,
        revision=revision,
        skill_ref=normalized_ref,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _turn_payload_hash(
    text: str,
    intent: TurnIntent,
    session_kind: TutorSessionKind,
    progression_intent: ProgressionIntent | None = None,
    source_lesson_id: str | None = None,
    source_lesson_title: str | None = None,
    source_lesson_revision: int | None = None,
) -> str:
    payload = {
        "text": text,
        "intent": intent,
        "session_kind": session_kind,
        "progression_intent": progression_intent,
        "source_lesson_id": source_lesson_id,
        "source_lesson_title": source_lesson_title,
        "source_lesson_revision": source_lesson_revision,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _normalize_submission_id(value: str | None) -> str:
    if value is None:
        return str(uuid4())
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise TutorOperationError("submission ID must be a UUID") from exc


def _internal_state(state: dict[str, object]) -> dict[str, object]:
    value = state.get("_openlearn_internal")
    if isinstance(value, dict):
        internal = copy.deepcopy(value)
    else:
        internal = {}
    internal.setdefault("schema_version", 1)
    internal.setdefault("course_revision", 0)
    internal.setdefault("turn_results", {})
    return internal


def course_revision(slug: str) -> int:
    from openlearn import cli

    state = cli.load_state(slug)
    internal = _internal_state(state)
    value = internal.get("course_revision")
    return value if isinstance(value, int) and value >= 0 else 0


def _follow_up_proposal_path(slug: str, submission_id: str):
    from openlearn import cli

    return cli.topic_data_dir(slug) / "follow-up-proposals" / f"{submission_id}.json"


def _follow_up_payload_hash(
    slug: str,
    generation: str,
    interests: str,
    weak_areas: tuple[str, ...],
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "slug": slug,
                "generation": generation,
                "interests": interests,
                "weak_areas": weak_areas,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _proposal_from_record(
    record: Mapping[str, object], *, replayed: bool = False
):
    from openlearn.application import FollowUpProposal

    required_strings = (
        "source_slug",
        "submission_id",
        "state",
        "interests",
        "payload_hash",
    )
    if record.get("schema_version") != _FOLLOW_UP_SCHEMA_VERSION or any(
        not isinstance(record.get(key), str) for key in required_strings
    ):
        raise FollowUpProposalError("saved follow-up proposal is malformed")
    state = str(record["state"])
    if state not in {"pending", "ready", "error", "confirmed"}:
        raise FollowUpProposalError("saved follow-up proposal has an invalid state")
    weak_raw = record.get("weak_areas")
    if not isinstance(weak_raw, list) or any(not isinstance(item, str) for item in weak_raw):
        raise FollowUpProposalError("saved follow-up proposal has malformed weak areas")
    claim = record.get("claim")
    if claim is not None:
        token = claim.get("token") if isinstance(claim, dict) else None
        owner_id = claim.get("owner_id") if isinstance(claim, dict) else None
        owner_pid = claim.get("owner_pid") if isinstance(claim, dict) else None
        expires_at = claim.get("expires_at") if isinstance(claim, dict) else None
        if (
            not isinstance(claim, dict)
            or not isinstance(token, str)
            or not token
            or not isinstance(owner_id, str)
            or not owner_id
            or isinstance(owner_pid, bool)
            or not isinstance(owner_pid, int)
            or owner_pid <= 0
            or not isinstance(expires_at, str)
        ):
            raise FollowUpProposalError("saved follow-up proposal has a malformed claim")
        try:
            expiration = datetime.fromisoformat(expires_at)
        except ValueError as exc:
            raise FollowUpProposalError(
                "saved follow-up proposal has a malformed claim"
            ) from exc
        if expiration.tzinfo is None:
            raise FollowUpProposalError("saved follow-up proposal has a malformed claim")
    return FollowUpProposal(
        source_slug=str(record["source_slug"]),
        submission_id=str(record["submission_id"]),
        state=state,
        interests=str(record["interests"]),
        weak_areas=tuple(weak_raw),
        title=str(record.get("title") or ""),
        goal=str(record.get("goal") or ""),
        error_code=(
            str(record["error_code"])
            if isinstance(record.get("error_code"), str)
            else None
        ),
        error_message=(
            str(record["error_message"])
            if isinstance(record.get("error_message"), str)
            else None
        ),
        created_slug=(
            str(record["created_slug"])
            if isinstance(record.get("created_slug"), str)
            else None
        ),
        replayed=replayed,
    )


def _read_follow_up_record_unlocked(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FollowUpProposalError("saved follow-up proposal cannot be read") from exc
    if not isinstance(value, dict):
        raise FollowUpProposalError("saved follow-up proposal is malformed")
    _proposal_from_record(value)
    return value


def _read_follow_up_record(slug: str, submission_id: str) -> dict[str, object] | None:
    from openlearn import cli

    path = _follow_up_proposal_path(slug, submission_id)
    with cli.file_lock(path):
        return _read_follow_up_record_unlocked(path)


def _write_follow_up_record_unlocked(path: Path, record: Mapping[str, object]) -> None:
    from openlearn import cli

    cli.write_text_atomic(
        path,
        json.dumps(dict(record), indent=2, sort_keys=True) + "\n",
    )
    path.chmod(0o600)


def _write_follow_up_record(record: Mapping[str, object]) -> None:
    from openlearn import cli

    slug = str(record["source_slug"])
    path = _follow_up_proposal_path(slug, str(record["submission_id"]))
    with cli.file_lock(path):
        cli.raise_if_topic_tombstoned(slug)
        _write_follow_up_record_unlocked(path, record)


def _reserve_follow_up_record(
    record: Mapping[str, object],
) -> dict[str, object] | None:
    """Atomically publish one pending record or return the winner."""
    from openlearn import cli

    slug = str(record["source_slug"])
    submission_id = str(record["submission_id"])
    path = _follow_up_proposal_path(slug, submission_id)
    with cli.file_lock(path):
        existing = _read_follow_up_record_unlocked(path)
        if existing is not None:
            return existing
        cli.raise_if_topic_tombstoned(slug)
        _write_follow_up_record_unlocked(path, record)
    return None


def _follow_up_claim_is_active(
    record: Mapping[str, object], operation_key: tuple[str, str]
) -> bool:
    claim = record.get("claim")
    if not isinstance(claim, dict):
        return False
    expiration_raw = claim.get("expires_at")
    owner_id = claim.get("owner_id")
    owner_pid = claim.get("owner_pid")
    if (
        not isinstance(expiration_raw, str)
        or not isinstance(owner_id, str)
        or not isinstance(owner_pid, int)
    ):
        return False
    try:
        expiration = datetime.fromisoformat(expiration_raw)
    except ValueError:
        return False
    if expiration.tzinfo is None or expiration <= datetime.now(timezone.utc):
        return False
    if owner_pid == os.getpid():
        with _FOLLOW_UP_RUNNING_GUARD:
            return _FOLLOW_UP_RUNNING.get(operation_key) == owner_id
    if os.name == "nt":
        return True
    try:
        os.kill(owner_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _claim_follow_up_record(
    slug: str, submission_id: str, owner_id: str
) -> tuple[dict[str, object], str | None]:
    """Claim a retryable proposal with one durable cross-process owner."""
    from openlearn import cli

    path = _follow_up_proposal_path(slug, submission_id)
    operation_key = (slug, submission_id)
    with cli.file_lock(path):
        record = _read_follow_up_record_unlocked(path)
        if record is None:
            raise FollowUpProposalError("follow-up proposal not found")
        if record.get("state") not in {"pending", "error"}:
            return record, None
        if _follow_up_claim_is_active(record, operation_key):
            return record, None
        token = str(uuid4())
        claimed = {
            **record,
            "state": "pending",
            "error_code": None,
            "error_message": None,
            "claim": {
                "token": token,
                "owner_id": owner_id,
                "owner_pid": os.getpid(),
                "expires_at": (
                    datetime.now(timezone.utc) + _FOLLOW_UP_CLAIM_TIMEOUT
                ).isoformat(),
            },
            "updated_at": _now(),
        }
        cli.raise_if_topic_tombstoned(slug)
        _write_follow_up_record_unlocked(path, claimed)
    return claimed, token


def _finish_claimed_follow_up_record(
    record: Mapping[str, object],
    claim_token: str,
    changes: Mapping[str, object],
):
    """Publish a provider result only while this worker still owns the claim."""
    from openlearn import cli

    slug = str(record["source_slug"])
    submission_id = str(record["submission_id"])
    path = _follow_up_proposal_path(slug, submission_id)
    with cli.file_lock(path):
        current = _read_follow_up_record_unlocked(path)
        if current is None:
            raise FollowUpProposalError("follow-up proposal not found")
        claim = current.get("claim")
        if not isinstance(claim, dict) or claim.get("token") != claim_token:
            return replace(_proposal_from_record(current), replayed=True)
        finished = {**current, **changes, "updated_at": _now()}
        finished.pop("claim", None)
        cli.raise_if_topic_tombstoned(slug)
        _write_follow_up_record_unlocked(path, finished)
    return _proposal_from_record(finished)


def _run_follow_up_generation(slug: str, submission_id: str):
    operation_key = (slug, submission_id)
    owner_id = str(uuid4())
    with _FOLLOW_UP_RUNNING_GUARD:
        already_running = operation_key in _FOLLOW_UP_RUNNING
        if not already_running:
            _FOLLOW_UP_RUNNING[operation_key] = owner_id
    if already_running:
        current = _read_follow_up_record(slug, submission_id)
        if current is None:
            raise FollowUpProposalError("follow-up proposal not found")
        return replace(_proposal_from_record(current), replayed=True)
    try:
        claimed, claim_token = _claim_follow_up_record(slug, submission_id, owner_id)
        if claim_token is None:
            return replace(_proposal_from_record(claimed), replayed=True)
        return _generate_follow_up_record(claimed, claim_token)
    finally:
        with _FOLLOW_UP_RUNNING_GUARD:
            if _FOLLOW_UP_RUNNING.get(operation_key) == owner_id:
                _FOLLOW_UP_RUNNING.pop(operation_key, None)


def follow_up_proposal_status(slug: str, submission_id: str):
    """Return one durable proposal operation without starting or retrying it."""
    sid = _normalize_submission_id(submission_id)
    record = _read_follow_up_record(slug, sid)
    return _proposal_from_record(record) if record is not None else None


def _parse_follow_up_response(raw: str) -> tuple[str, str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FollowUpProposalError("provider returned an invalid follow-up proposal") from exc
    if not isinstance(value, dict) or set(value) != {"title", "goal"}:
        raise FollowUpProposalError("provider returned an invalid follow-up proposal")
    title = value.get("title")
    goal = value.get("goal")
    if (
        not isinstance(title, str)
        or not isinstance(goal, str)
        or title != title.strip()
        or goal != goal.strip()
        or not title
        or not goal
        or len(title) > 120
        or len(goal) > 2_000
        or any(character in title for character in "\r\n<>")
    ):
        raise FollowUpProposalError("provider returned an invalid follow-up proposal")
    return title, goal


def _generate_follow_up_record(record: dict[str, object], claim_token: str):
    from openlearn import config, providers

    try:
        credentials = config.effective_provider_credentials()
        raw = providers.chat_completion(
            credentials,
            system=(
                "Create one focused advanced course proposal. Return JSON only with exactly "
                "two string fields: title and goal. The title must be plain text under 120 "
                "characters. The goal must be a concrete learner-facing sentence. Do not "
                "include reasoning, Markdown, or additional fields."
            ),
            user=json.dumps(
                {
                    "source_course": record["source_title"],
                    "source_goal": record["source_goal"],
                    "weak_areas": record["weak_areas"],
                    "learner_interests": record["interests"],
                },
                sort_keys=True,
            ),
        )
        title, goal = _parse_follow_up_response(raw)
    except (providers.ProviderError, config.ConfigError):
        return _finish_claimed_follow_up_record(
            record,
            claim_token,
            {
                "state": "error",
                "error_code": "provider_unavailable",
                "error_message": (
                    "The tutor provider could not create this proposal. Check the connection "
                    "or retry in a moment."
                ),
            },
        )
    except FollowUpProposalError:
        return _finish_claimed_follow_up_record(
            record,
            claim_token,
            {
                "state": "error",
                "error_code": "invalid_provider_response",
                "error_message": (
                    "The tutor returned an unusable course proposal. Retry with the same request."
                ),
            },
        )
    return _finish_claimed_follow_up_record(
        record,
        claim_token,
        {
            "state": "ready",
            "title": title,
            "goal": goal,
            "error_code": None,
            "error_message": None,
        },
    )


def request_follow_up_proposal(
    slug: str, *, interests: str, submission_id: str
):
    """Explicitly generate one idempotent proposal without creating a course."""
    from openlearn import application, cli, config

    sid = _normalize_submission_id(submission_id)
    normalized_interests = interests.strip()
    if len(normalized_interests) > _FOLLOW_UP_INTEREST_LIMIT:
        raise FollowUpProposalError("follow-up interests must be at most 2000 characters")
    snapshot = application.course(slug)
    recommendation = snapshot.card.library.recommendation
    if not snapshot.card.library.first_pass_complete:
        raise FollowUpProposalError(
            "Finish the course's first pass before generating a specialized follow-up."
        )
    if recommendation is not None and recommendation.kind == "curated":
        raise FollowUpProposalError(
            "This course already has a curated specialized follow-up."
        )
    generation = cli.current_topic_generation(slug)
    if generation is None:
        raise FollowUpProposalError("source course no longer exists")
    weak_areas = snapshot.card.library.weak_areas
    payload_hash = _follow_up_payload_hash(
        slug, generation, normalized_interests, weak_areas
    )
    existing = _read_follow_up_record(slug, sid)
    if existing is not None:
        if existing.get("payload_hash") != payload_hash:
            raise FollowUpProposalConflictError(
                "submission ID was already used for a different follow-up proposal"
            )
        return replace(_proposal_from_record(existing), replayed=True)
    if not config.provider_is_configured(require_verified=True):
        raise FollowUpProviderNotReadyError(
            "Connect and verify a tutor provider before generating a follow-up course."
        )
    record: dict[str, object] = {
        "schema_version": _FOLLOW_UP_SCHEMA_VERSION,
        "source_slug": slug,
        "source_generation": generation,
        "source_title": snapshot.card.title,
        "source_goal": snapshot.card.goal,
        "submission_id": sid,
        "payload_hash": payload_hash,
        "state": "pending",
        "interests": normalized_interests,
        "weak_areas": list(weak_areas),
        "title": "",
        "goal": "",
        "error_code": None,
        "error_message": None,
        "created_slug": None,
        "updated_at": _now(),
    }
    winner = _reserve_follow_up_record(record)
    if winner is not None:
        if winner.get("payload_hash") != payload_hash:
            raise FollowUpProposalConflictError(
                "submission ID was already used for a different follow-up proposal"
            )
        return replace(_proposal_from_record(winner), replayed=True)
    return _run_follow_up_generation(slug, sid)


def retry_follow_up_proposal(slug: str, submission_id: str):
    """Retry one saved provider error without creating a second operation."""
    from openlearn import config

    sid = _normalize_submission_id(submission_id)
    record = _read_follow_up_record(slug, sid)
    if record is None:
        raise FollowUpProposalError("follow-up proposal not found")
    if record.get("state") not in {"pending", "error"}:
        return replace(_proposal_from_record(record), replayed=True)
    if not config.provider_is_configured(require_verified=True):
        raise FollowUpProviderNotReadyError(
            "Connect and verify a tutor provider before retrying this proposal."
        )
    return _run_follow_up_generation(slug, sid)


def confirm_follow_up_proposal(slug: str, submission_id: str):
    """Create a generated course exactly once after explicit confirmation."""
    from openlearn import application

    sid = _normalize_submission_id(submission_id)
    record = _read_follow_up_record(slug, sid)
    if record is None:
        raise FollowUpProposalError("follow-up proposal not found")
    state = record.get("state")
    if state == "confirmed" and isinstance(record.get("created_slug"), str):
        return application.FollowUpCourseResult(
            source_slug=slug,
            submission_id=sid,
            course_slug=str(record["created_slug"]),
            created=False,
        )
    if state != "ready":
        raise FollowUpProposalError("follow-up proposal is not ready for confirmation")
    created = application.create_course(
        application.CourseCreationRequest(
            name=str(record["title"]),
            goal=str(record["goal"]),
            submission_id=sid,
        )
    )
    confirmed = {
        **record,
        "state": "confirmed",
        "created_slug": created.course.slug,
        "updated_at": _now(),
    }
    _write_follow_up_record(confirmed)
    return application.FollowUpCourseResult(
        source_slug=slug,
        submission_id=sid,
        course_slug=created.course.slug,
        created=created.created,
    )


def operation_result(slug: str, submission_id: str) -> TutorTurnResult | None:
    from openlearn import cli

    state = cli.load_state(slug)
    internal = _internal_state(state)
    results = internal.get("turn_results")
    if not isinstance(results, dict):
        return None
    raw = results.get(submission_id)
    if isinstance(raw, dict):
        return _result_from_dict(raw)
    permanent = cli.load_operation_receipt(slug, submission_id, state=state)
    if not isinstance(permanent, dict):
        return None
    return _result_from_permanent_receipt(slug, permanent)


def operation_status(slug: str, submission_id: str) -> TutorTurnResult | None:
    """Return either a terminal receipt or the current durable operation state."""
    from openlearn import cli

    key = (slug, submission_id)
    with _FUTURES_GUARD:
        if key in _RUNNING:
            live = _LIVE_TURNS.get(key, _LiveTurnState())
            return TutorTurnResult(
                submission_id=submission_id,
                status=live.phase,
                input_status="saved",
                message_kind="",
                move=None,
                preview=live.preview,
            )
    with _course_lock(slug):
        # Read the receipt and active operation from one state snapshot. A turn
        # can commit between two reads, leaving no active turn in the newer
        # snapshot even though the receipt was absent from the older one.
        state = cli.load_state(slug)
        internal = _internal_state(state)
        results = internal.get("turn_results")
        if isinstance(results, dict):
            raw_result = results.get(submission_id)
            if isinstance(raw_result, dict):
                return _result_from_dict(raw_result)
        permanent = cli.load_operation_receipt(slug, submission_id, state=state)
        if isinstance(permanent, dict):
            return _result_from_permanent_receipt(slug, permanent)
        active = next(
            (
                value
                for key_name in ("active_turn", "active_side_chat")
                if isinstance((value := internal.get(key_name)), dict)
                and value.get("submission_id") == submission_id
            ),
            None,
        )
        if not isinstance(active, dict):
            return None
        active_kind: TutorSessionKind = (
            "side_chat"
            if internal.get("active_side_chat") is active
            else "chat"
        )
        recovered = _recover_active_turn(slug, active, session_kind=active_kind)
        if recovered is not None:
            return recovered
        with _FUTURES_GUARD:
            live = _LIVE_TURNS.get((slug, submission_id))
            status = live.phase if live is not None else active.get("status")
            preview = live.preview if live is not None else None
        if status not in {
            "saved",
            "reserved",
            "judging",
            "generating",
            "generated",
            "validating",
        }:
            return None
        return TutorTurnResult(
            submission_id=submission_id,
            status=status,
            input_status="saved",
            message_kind="",
            move=None,
            preview=preview,
        )


def _result_from_dict(raw: dict[str, object]) -> TutorTurnResult:
    raw_move = raw.get("move")
    move = TutorMove(**raw_move) if isinstance(raw_move, dict) else None
    return TutorTurnResult(
        submission_id=str(raw["submission_id"]),
        status=str(raw["status"]),  # type: ignore[arg-type]
        input_status=str(raw["input_status"]),
        message_kind=str(raw["message_kind"]),
        move=move,
        error_code=str(raw["error_code"]) if raw.get("error_code") else None,
        error_message=str(raw["error_message"]) if raw.get("error_message") else None,
        payload_hash=str(raw["payload_hash"]) if raw.get("payload_hash") else None,
    )


def _receipt_dict(result: TutorTurnResult) -> dict[str, object]:
    receipt = asdict(result)
    receipt.pop("preview", None)
    return receipt


def _compact_result_dict(result: TutorTurnResult) -> dict[str, object]:
    compact = _receipt_dict(result)
    move = compact.get("move")
    if isinstance(move, dict):
        move = dict(move)
        move.pop("content", None)
        move["history_summary"] = ""
        compact["move"] = move
    return compact


def _durable_response_for_mutation(slug: str, mutation_id: str) -> str:
    from openlearn import cli

    body = cli.read_topic(slug).body
    marker = f"<!-- openlearn-turn:{mutation_id} -->"
    marker_index = body.find(marker)
    if marker_index < 0:
        raise cli.OpenLearnError(
            "saved tutor turn receipt has no matching durable session response"
        )
    response_marker = "\n**Response**\n\n"
    response_start = body.find(response_marker, marker_index + len(marker))
    if response_start < 0:
        raise cli.OpenLearnError(
            "saved tutor turn receipt has a malformed durable session response"
        )
    response_start += len(response_marker)
    next_turn = body.find("\n\n<!-- openlearn-turn:", response_start)
    response = body[response_start : next_turn if next_turn >= 0 else len(body)].strip()
    if not response:
        raise cli.OpenLearnError(
            "saved tutor turn receipt has an empty durable session response"
        )
    return response


def _result_from_permanent_receipt(
    slug: str, receipt: dict[str, object]
) -> TutorTurnResult:
    raw_result = receipt.get("result")
    mutation_id = receipt.get("mutation_id")
    response_hash = receipt.get("response_sha256")
    if not isinstance(raw_result, dict) or not isinstance(mutation_id, str):
        raise TutorOperationError("saved tutor operation receipt is malformed")
    if receipt.get("schema_version") == 1:
        return _result_from_dict(raw_result)
    response = (
        "You are caught up on the current curriculum. Choose Practice now "
        "or return when the next review is due."
        if receipt.get("receipt_kind") == "caught_up"
        else _durable_response_for_mutation(slug, mutation_id)
    )
    if hashlib.sha256(response.encode("utf-8")).hexdigest() != response_hash:
        from openlearn import cli

        raise cli.OpenLearnError(
            "saved tutor turn receipt does not match its durable session response"
        )
    restored = copy.deepcopy(raw_result)
    move = restored.get("move")
    if not isinstance(move, dict):
        raise TutorOperationError("saved tutor operation receipt has no move")
    move["content"] = response
    if receipt.get("receipt_kind") == "caught_up":
        move["history_summary"] = "Caught up; practice is available."
    return _result_from_dict(restored)


def _clear_live_turn(key: tuple[str, str]) -> None:
    with _FUTURES_GUARD:
        _LIVE_TURNS.pop(key, None)


def _turn_failure(exc: Exception) -> tuple[str, str]:
    from openlearn import cli

    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, cli.ProviderRequestError):
            messages = {
                "provider_credentials": "Your response is saved, but the provider rejected its credentials. Review Provider settings, then retry.",
                "provider_rate_limited": "Your response is saved. The provider is rate limited, so wait briefly and retry.",
                "provider_unavailable": "Your response is saved, but the model provider could not finish this turn. Retry, or review Provider settings if it repeats.",
            }
            return current.category, messages.get(
                current.category, messages["provider_unavailable"]
            )
        if isinstance(current, cli.JudgeOutputError):
            return (
                "judge_invalid_output",
                "Your answer is saved, but the selected judge model did not return usable grading data. Retry now, or review Provider settings if it repeats.",
            )
        current = current.__cause__ or current.__context__
    return (
        "turn_failure",
        "Your response is saved, but openlearn could not finish this turn. Retry without retyping it.",
    )


def _save_operation(
    slug: str,
    *,
    submission_id: str,
    status: OperationStatus,
    expected_revision: int,
    prompt: str,
    result: TutorTurnResult | None = None,
    error_code: str | None = None,
    payload_hash: str | None = None,
    owner_pid: int | None = None,
    session_kind: TutorSessionKind = "chat",
    source_course_revision: int | None = None,
    source_lesson: str | None = None,
    source_lesson_id: str | None = None,
    source_lesson_title: str | None = None,
    source_lesson_revision: int | None = None,
    source_lesson_skill_ref: Mapping[str, str] | None = None,
) -> None:
    from openlearn import cli

    def update(state: dict[str, object]) -> None:
        internal = _internal_state(state)
        active_key = _active_operation_key(session_kind)
        previous_active = internal.get(active_key)
        previous_active = previous_active if isinstance(previous_active, dict) else {}
        previous_submission_id = previous_active.get("submission_id")
        terminal = status in {"committed", "retryable_error", "conflict"}
        if not terminal and previous_active and previous_submission_id != submission_id:
            raise TutorConflictError("another tutor turn is already active for this course")
        if terminal:
            # A late worker may report failure after another process reserved a
            # newer turn. Only the owner of the current slot may clear it.
            if previous_submission_id == submission_id:
                internal[active_key] = None
        else:
            internal[active_key] = {
                "submission_id": submission_id,
                "status": status,
                "expected_revision": expected_revision,
                "prompt": prompt,
                "payload_hash": payload_hash or previous_active.get("payload_hash"),
                "owner_pid": owner_pid or previous_active.get("owner_pid"),
                "source_course_revision": (
                    source_course_revision
                    if source_course_revision is not None
                    else previous_active.get("source_course_revision")
                ),
                "source_lesson": (
                    source_lesson
                    if source_lesson is not None
                    else previous_active.get("source_lesson")
                ),
                "source_lesson_id": (
                    source_lesson_id
                    if source_lesson_id is not None
                    else previous_active.get("source_lesson_id")
                ),
                "source_lesson_title": (
                    source_lesson_title
                    if source_lesson_title is not None
                    else previous_active.get("source_lesson_title")
                ),
                "source_lesson_revision": (
                    source_lesson_revision
                    if source_lesson_revision is not None
                    else previous_active.get("source_lesson_revision")
                ),
                "source_lesson_skill_ref": (
                    dict(source_lesson_skill_ref)
                    if source_lesson_skill_ref is not None
                    else previous_active.get("source_lesson_skill_ref")
                ),
                "updated_at": _now(),
            }
        if result is None and status in {"saved", "judging", "generating", "validating"}:
            results = internal.get("turn_results")
            if isinstance(results, dict) and submission_id in results:
                results = dict(results)
                results.pop(submission_id, None)
                internal["turn_results"] = results
            last_error = internal.get("last_turn_error")
            if isinstance(last_error, dict) and last_error.get("submission_id") == submission_id:
                internal.pop("last_turn_error", None)
        if result is not None:
            results = internal.get("turn_results")
            results = dict(results) if isinstance(results, dict) else {}
            results[submission_id] = _receipt_dict(result)
            while len(results) > 50:
                results.pop(next(iter(results)))
            internal["turn_results"] = results
        if error_code:
            internal["last_turn_error"] = {
                "submission_id": submission_id,
                "code": error_code,
                "updated_at": _now(),
            }
        state["_openlearn_internal"] = internal

    cli.update_state_atomic(slug, update)


def _move_from_answer(
    answer: str, revision: int, metadata: dict[str, object]
) -> TutorMove:
    pending = metadata.get("pending_question")
    prompt = ""
    action_kind = "continue"
    if isinstance(pending, dict) and isinstance(pending.get("question"), str):
        prompt = str(pending["question"])
        action_kind = str(pending.get("kind") or "free_response")
    if re.search(r"(?im)^\*\*feedback:\*\*", answer):
        kind = "feedback"
    elif prompt:
        kind = "check"
    elif re.search(r"(?im)^\*\*example:\*\*", answer):
        kind = "example"
    else:
        kind = "lesson"
    summary = " ".join(answer.split())[:180]
    return TutorMove(
        move_id=f"move-{revision}",
        revision=revision,
        kind=kind,
        content=answer,
        action_kind=action_kind,
        prompt=prompt,
        history_summary=summary,
    )


def _message_kind(intent: TurnIntent) -> str:
    return {
        "question": "question",
        "confusion": "confusion",
        "navigation": "navigation",
    }.get(intent, "answer")


def _validate_turn(text: str, submission_id: str | None) -> tuple[str, str]:
    if not isinstance(text, str) or not text.strip():
        raise TutorOperationError("learner response is required")
    if len(text.encode("utf-8")) > 32_000:
        raise TutorOperationError("learner response is too long")
    return _normalize_submission_id(submission_id), text.strip()


def _future_active(slug: str, submission_id: str) -> bool:
    key = (slug, submission_id)
    with _FUTURES_GUARD:
        future = _FUTURES.get(key)
        return key in _RUNNING or (future is not None and not future.done())


def _active_turn_age(active: dict[str, object]) -> timedelta | None:
    raw = active.get("updated_at")
    if not isinstance(raw, str):
        return None
    try:
        updated_at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc)


def _recover_active_turn(
    slug: str,
    active: dict[str, object],
    *,
    session_kind: TutorSessionKind = "chat",
) -> TutorTurnResult | None:
    """Turn an expired or process-orphaned operation into a durable receipt."""
    submission_id = active.get("submission_id")
    if not isinstance(submission_id, str):
        return None
    revision = active.get("expected_revision")
    expected_revision = revision if isinstance(revision, int) else course_revision(slug)
    prompt = active.get("prompt")
    normalized = prompt if isinstance(prompt, str) else ""
    payload_hash = active.get("payload_hash")
    owner_pid = active.get("owner_pid")
    age = _active_turn_age(active)
    # A live worker owns the operation even if a slow provider exhausts the
    # normal restart-recovery window. Only orphaned durable records expire.
    if _future_active(slug, submission_id):
        return None
    if isinstance(owner_pid, int) and owner_pid > 0 and owner_pid != os.getpid():
        try:
            os.kill(owner_pid, 0)
        except (OSError, ProcessLookupError):
            pass
        else:
            return None
    if age is None:
        code = "operation_interrupted"
        message = "Your response was saved, but openlearn restarted. Retry this tutor turn."
    elif age >= _OPERATION_TIMEOUT:
        code = "operation_timed_out"
        message = "Your response is saved. Retry this tutor turn."
    else:
        code = "operation_interrupted"
        message = "Your response was saved, but openlearn restarted. Retry this tutor turn."
    result = TutorTurnResult(
        submission_id=submission_id,
        status="retryable_error",
        input_status="saved",
        message_kind="",
        move=None,
        error_code=code,
        error_message=message,
        payload_hash=str(payload_hash) if isinstance(payload_hash, str) else None,
    )
    _save_operation(
        slug,
        submission_id=submission_id,
        status="retryable_error",
        expected_revision=expected_revision,
        prompt=normalized,
        result=result,
        error_code=code,
        payload_hash=str(payload_hash) if isinstance(payload_hash, str) else None,
        session_kind=session_kind,
    )
    return result


def _interview_progression_state(slug: str) -> dict[str, object] | None:
    from openlearn import cli

    value = cli.load_state(slug).get("interview_curriculum")
    return value if isinstance(value, dict) else None


def _pending_interview_target(
    canonical: object, pending_question: object
) -> dict[str, object] | None:
    """Return the pinned target only when it owns the stored learner check."""
    if not isinstance(canonical, dict) or not isinstance(pending_question, dict):
        return None
    committed = canonical.get("committed_check_target")
    if not isinstance(committed, dict):
        return None
    target = committed.get("target")
    committed_ref = committed.get("skill_ref")
    target_ref = target.get("skill_ref") if isinstance(target, dict) else None
    pending_ref = pending_question.get("curriculum_target")
    identity_keys = (
        "graph_id",
        "graph_version",
        "mastery_policy_version",
        "skill_id",
    )
    if (
        not isinstance(target, dict)
        or not isinstance(committed_ref, dict)
        or not isinstance(target_ref, dict)
        or not isinstance(pending_ref, dict)
        or any(
            not isinstance(committed_ref.get(key), str)
            or committed_ref.get(key) != target_ref.get(key)
            or committed_ref.get(key) != pending_ref.get(key)
            for key in identity_keys
        )
        or target.get("evidence_kind") != committed.get("evidence_kind")
        or pending_question.get("curriculum_evidence_kind")
        != committed.get("evidence_kind")
    ):
        return None
    for pending_key, committed_key in (
        ("curriculum_problem_id", "problem_id"),
        ("curriculum_transfer_family", "transfer_family"),
    ):
        if target.get(committed_key) != committed.get(committed_key):
            return None
        pending_value = pending_question.get(pending_key)
        if pending_value is not None and pending_value != committed.get(committed_key):
            return None
    return copy.deepcopy(target)


def _reserve_interview_progression(
    slug: str,
    normalized: str,
    sid: str,
    expected_revision: int | None,
    intent: TurnIntent,
    session_kind: TutorSessionKind,
    *,
    progression_intent: ProgressionIntent,
) -> tuple[int, TutorTurnResult | None]:
    """Atomically reserve one canonical interview target and revision."""
    from openlearn import cli, interview_curriculum

    payload_hash = _turn_payload_hash(
        normalized, intent, session_kind, progression_intent
    )
    cli.recover_turn_commit(slug)
    with cli.topic_store_locks(slug):
        cli.raise_if_topic_tombstoned(slug)
        state = cli._load_state_unlocked(slug)
        canonical = state.get("interview_curriculum")
        if not isinstance(canonical, dict):
            raise TutorOperationError("interview curriculum is not prepared")
        internal = _internal_state(state)
        results = internal.get("turn_results")
        raw_replay = results.get(sid) if isinstance(results, dict) else None
        receipts = cli._validated_turn_receipts(state)
        permanent = cli.load_operation_receipt(slug, sid, state=state)
        replay = (
            _result_from_dict(raw_replay)
            if isinstance(raw_replay, dict)
            else (
                _result_from_permanent_receipt(slug, permanent)
                if isinstance(permanent, dict)
                else None
            )
        )
        if replay is not None:
            if replay.payload_hash not in {None, payload_hash}:
                raise TutorConflictError(
                    "submission ID was already used with different learner input"
                )
            if replay.status == "committed":
                revision = internal.get("course_revision")
                return (revision if isinstance(revision, int) else 0), replay
        revision = internal.get("course_revision")
        revision = revision if isinstance(revision, int) and revision >= 0 else 0
        active = canonical.get("active_operation")
        if isinstance(active, dict):
            active_sid = active.get("submission_id")
            if active_sid != sid:
                raise TutorConflictError(
                    "another curriculum turn is already active for this course"
                )
            if active.get("payload_hash") != payload_hash:
                raise TutorConflictError(
                    "submission ID was already used with different learner input"
                )
            reservation_revision = active.get("reservation_revision")
            if not isinstance(reservation_revision, int) or revision != reservation_revision:
                raise TutorConflictError("saved curriculum reservation is inconsistent")
            internal["active_turn"] = {
                "submission_id": sid,
                "status": "reserved",
                "expected_revision": reservation_revision,
                "prompt": normalized,
                "payload_hash": payload_hash,
                "owner_pid": os.getpid(),
                "progression_intent": progression_intent,
                "updated_at": _now(),
            }
            state["_openlearn_internal"] = internal
            cli.write_text_atomic(
                cli.topic_state_path(slug),
                json.dumps(state, indent=2, sort_keys=True) + "\n",
            )
            topic_text = cli.topic_path(slug).read_text(encoding="utf-8")
            metadata, body = cli.parse_topic(topic_text)
            projected = interview_curriculum.compatibility_projection(canonical)
            if any(metadata.get(key) != value for key, value in projected.items()):
                metadata = dict(metadata)
                metadata.update(projected)
                cli.write_text_atomic(
                    cli.topic_path(slug), cli.format_topic(metadata, body)
                )
            return reservation_revision, None
        active_turn = internal.get("active_turn")
        if isinstance(active_turn, dict):
            if active_turn.get("submission_id") != sid:
                raise TutorConflictError(
                    "another curriculum turn is already active for this course"
                )
            if active_turn.get("payload_hash") != payload_hash:
                raise TutorConflictError(
                    "submission ID was already used with different learner input"
                )
            if active_turn.get("status") != "saved":
                raise TutorConflictError("saved curriculum operation is inconsistent")
        if expected_revision is not None and expected_revision != revision:
            raise TutorConflictError("course changed; refresh before submitting again")
        if not isinstance(active_turn, dict):
            state["pending_learner_prompt"] = normalized
            internal["active_turn"] = {
                "submission_id": sid,
                "status": "saved",
                "expected_revision": revision,
                "prompt": normalized,
                "payload_hash": payload_hash,
                "owner_pid": os.getpid(),
                "progression_intent": progression_intent,
                "updated_at": _now(),
            }
            state["_openlearn_internal"] = internal
            cli.write_text_atomic(
                cli.topic_state_path(slug),
                json.dumps(state, indent=2, sort_keys=True) + "\n",
            )
            _progression_checkpoint("after_saved")
        resolution = interview_curriculum.resolve_progression_target(
            canonical,
            intent=progression_intent,
        )
        if resolution.target is None:
            move = TutorMove(
                move_id=f"caught-up-{revision}",
                revision=revision,
                kind="caught_up",
                content=(
                    "You are caught up on the current curriculum. Choose Practice now "
                    "or return when the next review is due."
                ),
                action_kind="practice",
                prompt="",
                history_summary="Caught up; practice is available.",
            )
            result = TutorTurnResult(
                submission_id=sid,
                status="committed",
                input_status="committed",
                message_kind="navigation",
                move=move,
                payload_hash=payload_hash,
            )
            results = internal.get("turn_results")
            results = dict(results) if isinstance(results, dict) else {}
            results[sid] = _receipt_dict(result)
            while len(results) > 50:
                results.pop(next(iter(results)))
            internal["turn_results"] = results
            internal["active_turn"] = None
            state["_openlearn_internal"] = internal
            state.pop("pending_learner_prompt", None)
            state["interview_curriculum"] = resolution.state
            permanent_receipt: dict[str, object] = {
                "schema_version": 2,
                "receipt_kind": "caught_up",
                "submission_id": sid,
                "payload_hash": payload_hash,
                "base_revision": revision,
                "reservation_revision": revision,
                "final_revision": revision,
                "status": "committed",
                "mutation_id": f"turn_{uuid4().hex}",
                "target": None,
                "reason": resolution.reason,
                "response_sha256": hashlib.sha256(
                    move.content.encode("utf-8")
                ).hexdigest(),
                "result": _compact_result_dict(result),
            }
            permanent_receipt["receipt_sha256"] = cli._payload_sha256(
                permanent_receipt
            )
            receipts = state.get("_turn_receipts")
            receipts = dict(receipts) if isinstance(receipts, dict) else {}
            receipts[f"operation_{sid.replace('-', '')}"] = permanent_receipt
            state["_turn_receipts"] = receipts
            state["_turn_receipts_schema"] = 2
            cli.write_text_atomic(
                cli.topic_state_path(slug),
                json.dumps(state, indent=2, sort_keys=True) + "\n",
            )
            # Caught-up commits do not use the normal turn journal. Publish the
            # authoritative hot receipt first, then externalize and compact it.
            cli._externalize_operation_receipts_unlocked(slug, state)
            cli.write_text_atomic(
                cli.topic_state_path(slug),
                json.dumps(state, indent=2, sort_keys=True) + "\n",
            )
            return revision, result
        reservation_revision = revision + 1
        reserved = resolution.state
        reserved["active_operation"] = {
            "submission_id": sid,
            "payload_hash": payload_hash,
            "learner_prompt": normalized,
            "status": "reserved",
            "base_revision": revision,
            "reservation_revision": reservation_revision,
            "target": resolution.target.to_dict(),
            "reason": resolution.reason,
            "deferred_skill_id": resolution.deferred_skill_id,
            "progression_intent": progression_intent,
            "rollback": {
                key: {
                    "present": key in canonical,
                    "value": copy.deepcopy(canonical.get(key)),
                }
                for key in (
                    "cursor",
                    "deferred",
                    "session_id",
                    "committed_check_target",
                )
            },
            "updated_at": _now(),
        }
        state["interview_curriculum"] = reserved
        state["pending_learner_prompt"] = normalized
        internal["course_revision"] = reservation_revision
        internal["active_turn"] = {
            "submission_id": sid,
            "status": "reserved",
            "expected_revision": reservation_revision,
            "prompt": normalized,
            "payload_hash": payload_hash,
            "owner_pid": os.getpid(),
            "progression_intent": progression_intent,
            "updated_at": _now(),
        }
        state["_openlearn_internal"] = internal
        cli.write_text_atomic(
            cli.topic_state_path(slug),
            json.dumps(state, indent=2, sort_keys=True) + "\n",
        )
        _progression_checkpoint("after_reserved")
        topic_text = cli.topic_path(slug).read_text(encoding="utf-8")
        metadata, body = cli.parse_topic(topic_text)
        projected = interview_curriculum.compatibility_projection(reserved)
        metadata = dict(metadata)
        metadata.update(projected)
        cli.write_text_atomic(cli.topic_path(slug), cli.format_topic(metadata, body))
        return reservation_revision, None


def _prepare_turn(
    slug: str,
    normalized: str,
    sid: str,
    expected_revision: int | None,
    intent: TurnIntent,
    session_kind: TutorSessionKind,
    progression_intent: ProgressionIntent | None,
    source_lesson_id: str | None,
    source_lesson_title: str | None,
    source_lesson_revision: int | None,
) -> tuple[int, TutorTurnResult | None]:
    from openlearn import cli

    payload_hash = _turn_payload_hash(
        normalized,
        intent,
        session_kind,
        progression_intent,
        source_lesson_id,
        source_lesson_title,
        source_lesson_revision,
    )
    if (
        intent == "navigation"
        and session_kind != cli.SIDE_CHAT_SESSION_KIND
        and _interview_progression_state(slug) is not None
    ):
        return _reserve_interview_progression(
            slug,
            normalized,
            sid,
            expected_revision,
            intent,
            session_kind,
            progression_intent=progression_intent or "continue",
        )
    replay = operation_result(slug, sid)
    if replay is not None and replay.payload_hash not in {None, payload_hash}:
        raise TutorConflictError(
            "submission ID was already used with different learner input"
        )
    if replay is not None and replay.status == "committed":
        return course_revision(slug), replay
    active_key = _active_operation_key(session_kind)
    state = cli.load_state(slug)
    internal = _internal_state(state)
    active = internal.get(active_key)
    if isinstance(active, dict):
        active_sid = active.get("submission_id")
        active_hash = active.get("payload_hash")
        if active_sid == sid and isinstance(active_hash, str) and active_hash != payload_hash:
            raise TutorConflictError(
                "submission ID was already used with different learner input"
            )
        recovered = _recover_active_turn(slug, active, session_kind=session_kind)
        if recovered is not None and active_sid == sid:
            return course_revision(slug), recovered
        if recovered is None and active_sid != sid:
            raise TutorConflictError("another tutor turn is already active for this course")
        if recovered is None and _future_active(slug, sid):
            current = operation_status(slug, sid)
            if current is not None:
                return course_revision(slug), current
    if (
        replay is not None
        and replay.status == "retryable_error"
        and _future_active(slug, sid)
    ):
        return course_revision(slug), replay
    source_lesson: str | None = None
    source_lesson_skill_ref: dict[str, str] | None = None
    source_course_revision: int | None = None
    if session_kind == cli.SIDE_CHAT_SESSION_KIND:
        course_revision_value = internal.get("course_revision", 0)
        course_revision_value = (
            course_revision_value
            if isinstance(course_revision_value, int) and course_revision_value >= 0
            else 0
        )
        source_course_revision = course_revision_value
        resolved = _resolved_side_chat_source(
            slug,
            state,
            source_lesson_id,
            source_lesson_title,
            source_lesson_revision,
        )
        source_lesson = resolved.content
        source_lesson_id = resolved.lesson_id
        source_lesson_title = resolved.title
        source_lesson_revision = resolved.revision
        source_lesson_skill_ref = resolved.skill_ref
    reservation: dict[str, object] = {}

    def reserve(state_after: dict[str, object]) -> None:
        current_internal = _internal_state(state_after)
        course_value = current_internal.get("course_revision", 0)
        current_course_revision = (
            course_value if isinstance(course_value, int) and course_value >= 0 else 0
        )
        namespace_value = (
            current_internal.get("side_chat_revision", 0)
            if session_kind == cli.SIDE_CHAT_SESSION_KIND
            else current_course_revision
        )
        namespace_revision = (
            namespace_value
            if isinstance(namespace_value, int) and namespace_value >= 0
            else 0
        )
        expected_value = (
            current_course_revision
            if session_kind == cli.SIDE_CHAT_SESSION_KIND
            else namespace_revision
        )
        if expected_revision is not None and expected_revision != expected_value:
            raise TutorConflictError("course changed; refresh before submitting again")
        if (
            session_kind == cli.SIDE_CHAT_SESSION_KIND
            and source_course_revision != current_course_revision
        ):
            raise TutorConflictError(
                "The visible lesson changed. Your question was not submitted; refresh and retry."
            )
        current = current_internal.get(active_key)
        if isinstance(current, dict):
            if current.get("submission_id") != sid:
                raise TutorConflictError(
                    "another tutor turn is already active for this course"
                )
            if current.get("payload_hash") != payload_hash:
                raise TutorConflictError(
                    "submission ID was already used with different learner input"
                )
            reservation["existing"] = True
            reservation["revision"] = namespace_revision
            return
        if session_kind != cli.SIDE_CHAT_SESSION_KIND:
            state_after["pending_learner_prompt"] = normalized
        current_internal[active_key] = {
            "submission_id": sid,
            "status": "saved",
            "expected_revision": namespace_revision,
            "prompt": normalized,
            "payload_hash": payload_hash,
            "owner_pid": os.getpid(),
            "source_course_revision": current_course_revision,
            "source_lesson": source_lesson,
            "source_lesson_id": source_lesson_id,
            "source_lesson_title": source_lesson_title,
            "source_lesson_revision": source_lesson_revision,
            "source_lesson_skill_ref": (
                dict(source_lesson_skill_ref)
                if source_lesson_skill_ref is not None
                else None
            ),
            "updated_at": _now(),
        }
        state_after["_openlearn_internal"] = current_internal
        reservation["revision"] = namespace_revision

    cli.update_state_atomic(slug, reserve)
    revision = int(reservation["revision"])
    if reservation.get("existing") is True:
        return revision, TutorTurnResult(
            submission_id=sid,
            status="saved",
            input_status="saved",
            message_kind=_message_kind(intent),
            move=None,
            payload_hash=payload_hash,
        )
    return revision, None


def _execute_prepared_turn_inner(
    slug: str,
    normalized: str,
    sid: str,
    revision: int,
    intent: TurnIntent,
    model: str | None,
    session_kind: TutorSessionKind,
    progression_intent: ProgressionIntent | None,
) -> TutorTurnResult:
    from openlearn import cli

    active_key = _active_operation_key(session_kind)
    canonical_before = _interview_progression_state(slug)
    state_snapshot = cli.load_state(slug)
    active_progression_before = (
        canonical_before.get("active_operation")
        if intent == "navigation"
        and session_kind != cli.SIDE_CHAT_SESSION_KIND
        and isinstance(canonical_before, dict)
        else None
    )
    pending_interview_target = (
        _pending_interview_target(
            canonical_before, state_snapshot.get("pending_question")
        )
        if intent == "answer" and session_kind != cli.SIDE_CHAT_SESSION_KIND
        else None
    )
    generated_override = (
        active_progression_before.get("generated_response")
        if isinstance(active_progression_before, dict)
        and active_progression_before.get("status") == "generated"
        and isinstance(active_progression_before.get("generated_response"), str)
        else None
    )
    with _course_lock(slug):
        replay = operation_result(slug, sid)
        if replay is not None and replay.status == "committed":
            return replay
        internal = _internal_state(cli.load_state(slug))
        active = internal.get(active_key)
        if not isinstance(active, dict) or active.get("submission_id") != sid:
            if replay is not None and replay.status == "retryable_error":
                raise TutorOperationError(
                    replay.error_message or "Tutor turn must be retried."
                )
            raise TutorOperationError("Tutor turn is no longer active. Retry it.")
        payload_hash = active.get("payload_hash")
        if not isinstance(payload_hash, str) or not payload_hash:
            raise TutorOperationError("Tutor turn payload identity is missing. Retry it.")
        source_lesson = (
            str(active.get("source_lesson"))
            if session_kind == cli.SIDE_CHAT_SESSION_KIND
            and isinstance(active.get("source_lesson"), str)
            else None
        )
        source_lesson_id = (
            str(active.get("source_lesson_id"))
            if session_kind == cli.SIDE_CHAT_SESSION_KIND
            and isinstance(active.get("source_lesson_id"), str)
            else None
        )
        source_lesson_title = (
            str(active.get("source_lesson_title"))
            if session_kind == cli.SIDE_CHAT_SESSION_KIND
            and isinstance(active.get("source_lesson_title"), str)
            else None
        )
        source_lesson_revision = (
            int(active["source_lesson_revision"])
            if session_kind == cli.SIDE_CHAT_SESSION_KIND
            and isinstance(active.get("source_lesson_revision"), int)
            else None
        )
        source_lesson_skill_ref = (
            {
                str(key): str(value)
                for key, value in active["source_lesson_skill_ref"].items()
            }
            if session_kind == cli.SIDE_CHAT_SESSION_KIND
            and isinstance(active.get("source_lesson_skill_ref"), dict)
            else None
        )
        if generated_override is None:
            _save_operation(
                slug,
                submission_id=sid,
                status="generating",
                expected_revision=revision,
                prompt=normalized,
                payload_hash=payload_hash,
                owner_pid=os.getpid(),
                session_kind=session_kind,
            )
    result_holder: list[TutorTurnResult] = []

    def publish_preview(value: str) -> None:
        key = (slug, sid)
        with _FUTURES_GUARD:
            live = _LIVE_TURNS.setdefault(key, _LiveTurnState())
            live.preview = value[-32_000:] if value else None

    def publish_status(value: cli.TutorTurnPhase) -> None:
        with _FUTURES_GUARD:
            live = _LIVE_TURNS.setdefault((slug, sid), _LiveTurnState())
            live.phase = value

    class TurnObserver:
        def publish_phase(self, phase: cli.TutorTurnPhase) -> None:
            publish_status(phase)

        def publish_preview(self, text: str) -> None:
            publish_preview(text)

    def persist_generated(answer: str) -> None:
        if intent != "navigation":
            return
        with _course_lock(slug):
            def update(state: dict[str, object]) -> None:
                canonical = state.get("interview_curriculum")
                active = canonical.get("active_operation") if isinstance(canonical, dict) else None
                if (
                    not isinstance(active, dict)
                    or active.get("submission_id") != sid
                    or active.get("reservation_revision") != revision
                ):
                    raise TutorConflictError("curriculum reservation changed before generation saved")
                canonical = copy.deepcopy(canonical)
                active = copy.deepcopy(active)
                active["status"] = "generated"
                active["generated_response"] = answer
                active["updated_at"] = _now()
                canonical["active_operation"] = active
                state["interview_curriculum"] = canonical
                internal = _internal_state(state)
                current = internal.get("active_turn")
                if not isinstance(current, dict) or current.get("submission_id") != sid:
                    raise TutorConflictError("tutor operation changed before generation saved")
                current = copy.deepcopy(current)
                current["status"] = "generated"
                current["updated_at"] = _now()
                internal["active_turn"] = current
                state["_openlearn_internal"] = internal

            cli.update_state_atomic(slug, update)

    def commit_receipt(
        answer: str,
        metadata: dict[str, object],
        state_before: dict[str, object],
        state_after: dict[str, object],
        mutation_id: str,
    ) -> None:
        from openlearn import interview_curriculum

        with _course_lock(slug):
            current_state = cli.load_state(slug)
            current_internal = _internal_state(current_state)
            current_active = current_internal.get(active_key)
            active_age = (
                _active_turn_age(current_active)
                if isinstance(current_active, dict)
                else None
            )
            if (
                not isinstance(current_active, dict)
                or current_active.get("submission_id") != sid
                or active_age is None
                or (
                    active_age >= _OPERATION_TIMEOUT
                    and not _future_active(slug, sid)
                )
            ):
                raise TutorOperationError("Tutor turn expired before it could be saved.")
            current_course_revision = current_internal.get("course_revision")
            current_namespace_revision = (
                current_internal.get("side_chat_revision", 0)
                if session_kind == cli.SIDE_CHAT_SESSION_KIND
                else current_course_revision
            )
            if current_namespace_revision != revision:
                raise cli.TurnCommitConflictError(
                    "tutor operation namespace changed while preparing a response"
                )
            if session_kind == cli.SIDE_CHAT_SESSION_KIND:
                # Side chat owns only its independent operation namespace. Rebase
                # its final journal on the latest course state so an overlapping
                # navigation commit remains authoritative.
                state_before.clear()
                state_before.update(copy.deepcopy(current_state))
                state_after.clear()
                state_after.update(copy.deepcopy(current_state))
            internal = copy.deepcopy(current_internal)
            move_revision = (
                current_course_revision
                if session_kind == cli.SIDE_CHAT_SESSION_KIND
                and isinstance(current_course_revision, int)
                else revision + 1
            )
            new_revision = revision + 1
            canonical = state_after.get("interview_curriculum")
            if intent == "navigation" and isinstance(canonical, dict):
                active_progression = canonical.get("active_operation")
                if (
                    not isinstance(active_progression, dict)
                    or active_progression.get("submission_id") != sid
                    or active_progression.get("reservation_revision") != revision
                ):
                    raise cli.TurnCommitConflictError(
                        "curriculum reservation changed while the tutor was preparing a response"
                    )
                target = active_progression.get("target")
                target_ref = target.get("skill_ref") if isinstance(target, dict) else None
                skill_id = (
                    target_ref.get("skill_id") if isinstance(target_ref, dict) else None
                )
                if not isinstance(skill_id, str):
                    raise TutorOperationError("saved curriculum target is malformed")
                canonical = interview_curriculum.record_progression_commit(
                    canonical, skill_id
                )
                canonical["active_operation"] = None
                state_after["interview_curriculum"] = canonical
            move = _move_from_answer(answer, move_revision, metadata)
            result = TutorTurnResult(
                submission_id=sid,
                status="committed",
                input_status="committed",
                message_kind=_message_kind(intent),
                move=move,
                payload_hash=payload_hash,
            )
            results = internal.get("turn_results")
            results = dict(results) if isinstance(results, dict) else {}
            results[sid] = _receipt_dict(result)
            while len(results) > 50:
                results.pop(next(iter(results)))
            if session_kind == cli.SIDE_CHAT_SESSION_KIND:
                internal["side_chat_revision"] = new_revision
            else:
                internal["course_revision"] = new_revision
            internal[active_key] = None
            internal["turn_results"] = results
            last_error = internal.get("last_turn_error")
            if isinstance(last_error, dict) and last_error.get("submission_id") == sid:
                internal.pop("last_turn_error", None)
            state_after["_openlearn_internal"] = internal
            if intent == "navigation" and isinstance(canonical, dict):
                receipts = state_after.get("_turn_receipts")
                receipts = dict(receipts) if isinstance(receipts, dict) else {}
                target = (
                    copy.deepcopy(active_progression_before.get("target"))
                    if isinstance(active_progression_before, dict)
                    else None
                )
                permanent_receipt: dict[str, object] = {
                    "schema_version": 2,
                    "submission_id": sid,
                    "payload_hash": result.payload_hash,
                    "base_revision": revision - 1,
                    "reservation_revision": revision,
                    "final_revision": new_revision,
                    "status": "committed",
                    "mutation_id": mutation_id,
                    "target": target,
                    "reason": (
                        active_progression_before.get("reason")
                        if isinstance(active_progression_before, dict)
                        else None
                    ),
                    "response_sha256": hashlib.sha256(
                        answer.encode("utf-8")
                    ).hexdigest(),
                    "result": _compact_result_dict(result),
                }
                permanent_receipt["receipt_sha256"] = cli._payload_sha256(
                    permanent_receipt
                )
                receipts[f"operation_{sid.replace('-', '')}"] = permanent_receipt
                state_after["_turn_receipts"] = receipts
                state_after["_turn_receipts_schema"] = 2
            result_holder.append(result)

    def progression_events(
        _answer: str,
        _metadata: dict[str, object],
        _state_after: dict[str, object],
    ) -> list[tuple[str, str, dict[str, object]]]:
        if intent != "navigation" or not isinstance(active_progression_before, dict):
            return []
        target = active_progression_before.get("target")
        target_ref = target.get("skill_ref") if isinstance(target, dict) else None
        return [
            (
                slug,
                "interview_curriculum_advanced",
                {
                    "submission_id": sid,
                    "payload_hash": payload_hash,
                    "base_revision": revision - 1,
                    "reservation_revision": revision,
                    "final_revision": revision + 1,
                    "reason": active_progression_before.get("reason"),
                    "skill_ref": dict(target_ref) if isinstance(target_ref, dict) else {},
                    "deferred_skill_id": active_progression_before.get(
                        "deferred_skill_id"
                    ),
                },
            )
        ]

    try:
        generation_guard = (
            nullcontext()
            if session_kind == cli.SIDE_CHAT_SESSION_KIND
            else _GENERATION_LOCK
        )
        with generation_guard:
            cli.ask_topic(
                slug,
                normalized,
                model=model,
                output_func=lambda _text="": None,
                pending_learner_prompt=(
                    None
                    if session_kind == cli.SIDE_CHAT_SESSION_KIND
                    else normalized
                ),
                allow_specialized_actions=False,
                session_kind=session_kind,
                increment_course_revision=(session_kind != cli.SIDE_CHAT_SESSION_KIND),
                side_chat_lesson_override=source_lesson,
                side_chat_source_id=source_lesson_id,
                side_chat_source_title=source_lesson_title,
                side_chat_source_revision=source_lesson_revision,
                side_chat_source_skill_ref=source_lesson_skill_ref,
                message_kind_override=(
                    _message_kind(intent) if intent != "answer" else None
                ),
                commit_state_hook=commit_receipt,
                turn_observer=TurnObserver(),
                generated_state_hook=(
                    persist_generated
                    if isinstance(active_progression_before, dict)
                    else None
                ),
                generated_answer_override=generated_override,
                commit_events_hook=progression_events,
                interview_target=(
                    copy.deepcopy(active_progression_before.get("target"))
                    if isinstance(active_progression_before, dict)
                    and isinstance(active_progression_before.get("target"), dict)
                    else pending_interview_target
                ),
            )
        if not result_holder:
            raise TutorOperationError("tutor turn did not produce a committed result")
        _clear_live_turn((slug, sid))
        return result_holder[0]
    except cli.TurnCommitConflictError as exc:
        _clear_live_turn((slug, sid))
        with _course_lock(slug):
            result = TutorTurnResult(
                submission_id=sid,
                status="conflict",
                input_status="saved",
                message_kind=_message_kind(intent),
                move=None,
                error_code="course_revision_changed",
                error_message=(
                    "This course changed elsewhere. Refresh before retrying your saved response."
                ),
                payload_hash=payload_hash,
            )
            _save_operation(
                slug,
                submission_id=sid,
                status="conflict",
                expected_revision=revision,
                prompt=normalized,
                result=result,
                error_code="course_revision_changed",
                payload_hash=payload_hash,
                session_kind=session_kind,
            )
        raise TutorConflictError(result.error_message) from exc
    except Exception as exc:
        _clear_live_turn((slug, sid))
        with _course_lock(slug):
            committed = operation_result(slug, sid)
            if committed is not None and committed.status == "committed":
                return committed
            if committed is not None and committed.status == "retryable_error":
                raise TutorOperationError(
                    committed.error_message or "Tutor turn must be retried."
                ) from exc
            error_code, error_message = _turn_failure(exc)
            result = TutorTurnResult(
                submission_id=sid,
                status="retryable_error",
                input_status="saved",
                message_kind=_message_kind(intent),
                move=None,
                error_code=error_code,
                error_message=error_message,
                payload_hash=payload_hash,
            )
            _save_operation(
                slug,
                submission_id=sid,
                status="retryable_error",
                expected_revision=revision,
                prompt=normalized,
                result=result,
                error_code=error_code,
                payload_hash=payload_hash,
                session_kind=session_kind,
            )
        if isinstance(exc, TutorOperationError):
            raise
        raise TutorOperationError(result.error_message) from exc


def _execute_prepared_turn(
    slug: str,
    normalized: str,
    sid: str,
    revision: int,
    intent: TurnIntent,
    model: str | None,
    session_kind: TutorSessionKind,
    progression_intent: ProgressionIntent | None,
) -> TutorTurnResult:
    """Run a prepared turn with a durable failure boundary around all setup."""
    from openlearn import cli

    try:
        return _execute_prepared_turn_inner(
            slug,
            normalized,
            sid,
            revision,
            intent,
            model,
            session_kind,
            progression_intent,
        )
    except Exception as exc:
        _clear_live_turn((slug, sid))
        current: TutorTurnResult | None = None
        try:
            current = operation_result(slug, sid)
        except Exception:
            pass
        if current is not None and current.status in {
            "committed",
            "retryable_error",
            "conflict",
        }:
            raise
        payload_hash = _turn_payload_hash(
            normalized, intent, session_kind, progression_intent
        )
        try:
            internal = _internal_state(cli.load_state(slug))
            active = internal.get(_active_operation_key(session_kind))
            if (
                isinstance(active, dict)
                and active.get("submission_id") == sid
                and isinstance(active.get("payload_hash"), str)
            ):
                payload_hash = str(active["payload_hash"])
        except Exception:
            pass
        error_code, error_message = _turn_failure(exc)
        result = TutorTurnResult(
            submission_id=sid,
            status="retryable_error",
            input_status="saved",
            message_kind=_message_kind(intent),
            move=None,
            error_code=error_code,
            error_message=error_message,
            payload_hash=payload_hash,
        )
        with _course_lock(slug):
            _save_operation(
                slug,
                submission_id=sid,
                status="retryable_error",
                expected_revision=revision,
                prompt=normalized,
                result=result,
                error_code=error_code,
                payload_hash=payload_hash,
                session_kind=session_kind,
            )
        if isinstance(exc, (TutorConflictError, TutorOperationError)):
            raise
        raise TutorOperationError(error_message) from exc


def submit_turn(
    slug: str,
    text: str,
    *,
    intent: TurnIntent = "answer",
    submission_id: str | None = None,
    expected_revision: int | None = None,
    model: str | None = None,
    session_kind: TutorSessionKind = "chat",
    progression_intent: ProgressionIntent | None = None,
    source_lesson_id: str | None = None,
    source_lesson_title: str | None = None,
    source_lesson_revision: int | None = None,
) -> TutorTurnResult:
    """Run one durable, presentation-independent learner turn.

    The existing turn journal remains the commit authority. This service adds
    request idempotency and a structured result for non-terminal interfaces.
    """

    sid, normalized = _validate_turn(text, submission_id)
    if intent == "navigation" and progression_intent is None:
        progression_intent = "continue"
    with _course_lock(slug):
        revision, replay = _prepare_turn(
            slug,
            normalized,
            sid,
            expected_revision,
            intent,
            session_kind,
            progression_intent,
            source_lesson_id,
            source_lesson_title,
            source_lesson_revision,
        )
        if replay is not None:
            return replay
        with _FUTURES_GUARD:
            _RUNNING.add((slug, sid))
    try:
        return _execute_prepared_turn(
            slug,
            normalized,
            sid,
            revision,
            intent,
            model,
            session_kind,
            progression_intent,
        )
    finally:
        with _FUTURES_GUARD:
            _RUNNING.discard((slug, sid))


def start_turn(
    slug: str,
    text: str,
    *,
    intent: TurnIntent = "answer",
    submission_id: str | None = None,
    expected_revision: int | None = None,
    model: str | None = None,
    session_kind: TutorSessionKind = "chat",
    progression_intent: ProgressionIntent | None = None,
    source_lesson_id: str | None = None,
    source_lesson_title: str | None = None,
    source_lesson_revision: int | None = None,
) -> TutorTurnResult:
    """Persist a turn, then execute it in the bounded tutor worker pool."""
    sid, normalized = _validate_turn(text, submission_id)
    if intent == "navigation" and progression_intent is None:
        progression_intent = "continue"
    with _course_lock(slug):
        revision, replay = _prepare_turn(
            slug,
            normalized,
            sid,
            expected_revision,
            intent,
            session_kind,
            progression_intent,
            source_lesson_id,
            source_lesson_title,
            source_lesson_revision,
        )
        if replay is not None:
            return replay
        pending = TutorTurnResult(
            submission_id=sid,
            status="saved",
            input_status="saved",
            message_kind=_message_kind(intent),
            move=None,
            payload_hash=_turn_payload_hash(
                normalized,
                intent,
                session_kind,
                progression_intent,
                source_lesson_id,
                source_lesson_title,
                source_lesson_revision,
            ),
        )
        operation_key = (slug, sid)
        with _FUTURES_GUARD:
            _RUNNING.add(operation_key)
        try:
            future = _EXECUTOR.submit(
                _execute_prepared_turn,
                slug,
                normalized,
                sid,
                revision,
                intent,
                model,
                session_kind,
                progression_intent,
            )
        except RuntimeError as exc:
            failed = TutorTurnResult(
                submission_id=sid,
                status="retryable_error",
                input_status="saved",
                message_kind=_message_kind(intent),
                move=None,
                error_code="executor_unavailable",
                error_message="Your response is saved. Retry after restarting openlearn.",
                payload_hash=_turn_payload_hash(
                    normalized,
                    intent,
                    session_kind,
                    progression_intent,
                    source_lesson_id,
                    source_lesson_title,
                    source_lesson_revision,
                ),
            )
            _save_operation(
                slug,
                submission_id=sid,
                status="retryable_error",
                expected_revision=revision,
                prompt=normalized,
                result=failed,
                error_code="executor_unavailable",
                payload_hash=failed.payload_hash,
                session_kind=session_kind,
            )
            with _FUTURES_GUARD:
                _RUNNING.discard(operation_key)
            raise TutorOperationError(failed.error_message) from exc
        with _FUTURES_GUARD:
            _FUTURES[operation_key] = future

        def release(_future: Future[TutorTurnResult]) -> None:
            with _FUTURES_GUARD:
                _FUTURES.pop(operation_key, None)
                _RUNNING.discard(operation_key)
                _LIVE_TURNS.pop(operation_key, None)

        future.add_done_callback(release)
        return pending


def resume_interview_progression(
    slug: str, *, model: str | None = None
) -> TutorTurnResult:
    """Explicitly adopt and finish the exact durable interview reservation."""
    from openlearn import cli

    cli.recover_turn_commit(slug)
    state = cli.load_state(slug)
    canonical = state.get("interview_curriculum")
    active = canonical.get("active_operation") if isinstance(canonical, dict) else None
    if not isinstance(active, dict):
        raise TutorOperationError("This interview course has no reserved turn to resume.")
    sid = active.get("submission_id")
    prompt = active.get("learner_prompt")
    revision = active.get("reservation_revision")
    progression_intent = active.get("progression_intent")
    internal = _internal_state(state)
    active_turn = internal.get("active_turn")
    if not isinstance(prompt, str) and isinstance(active_turn, dict):
        prompt = active_turn.get("prompt")
    if not isinstance(prompt, str):
        prompt = state.get("pending_learner_prompt")
    if (
        not isinstance(sid, str)
        or not isinstance(prompt, str)
        or not isinstance(revision, int)
        or progression_intent not in {"continue", "skip", "practice", "revisit", "deepen"}
    ):
        raise TutorOperationError("The saved interview reservation is incomplete.")
    if _future_active(slug, sid):
        raise TutorConflictError("This interview turn is still running.")
    owner_pid = active_turn.get("owner_pid") if isinstance(active_turn, dict) else None
    if isinstance(owner_pid, int) and owner_pid > 0 and owner_pid != os.getpid():
        try:
            os.kill(owner_pid, 0)
        except (OSError, ProcessLookupError):
            pass
        else:
            raise TutorConflictError("This interview turn is still running in another process.")
    return submit_turn(
        slug,
        prompt,
        intent="navigation",
        submission_id=sid,
        expected_revision=revision,
        model=model,
        progression_intent=progression_intent,
    )


def cancel_interview_progression(slug: str, submission_id: str) -> None:
    """Cancel one abandoned reservation without awarding instructional evidence."""
    from openlearn import cli, interview_curriculum

    cli.recover_turn_commit(slug)
    with cli.topic_store_locks(slug):
        before_state = cli._load_state_unlocked(slug)
        cancellations = before_state.get("_interview_cancellation_receipts")
        if isinstance(cancellations, dict) and submission_id in cancellations:
            return
        canonical = before_state.get("interview_curriculum")
        active = canonical.get("active_operation") if isinstance(canonical, dict) else None
        if not isinstance(active, dict) or active.get("submission_id") != submission_id:
            raise TutorConflictError("The saved interview reservation changed.")
        canonical = copy.deepcopy(canonical)
        rollback = active.get("rollback")
        if isinstance(rollback, dict):
            for key in (
                "cursor",
                "deferred",
                "session_id",
                "committed_check_target",
            ):
                entry = rollback.get(key)
                if not isinstance(entry, dict) or not isinstance(
                    entry.get("present"), bool
                ):
                    raise TutorOperationError(
                        "The saved interview reservation cannot be cancelled safely."
                    )
                if entry["present"]:
                    canonical[key] = copy.deepcopy(entry.get("value"))
                else:
                    canonical.pop(key, None)
        canonical["active_operation"] = None
        after_state = copy.deepcopy(before_state)
        after_state["interview_curriculum"] = canonical
        after_state.pop("pending_learner_prompt", None)
        internal = _internal_state(after_state)
        current_active = internal.get("active_turn")
        if isinstance(current_active, dict) and current_active.get("submission_id") == submission_id:
            internal["active_turn"] = None
        after_state["_openlearn_internal"] = internal
        cancellations = dict(cancellations) if isinstance(cancellations, dict) else {}
        cancellations[submission_id] = {
            "course_revision": internal.get("course_revision", 0),
            "cancelled_at": _now(),
        }
        after_state["_interview_cancellation_receipts"] = cancellations
        topic_text = cli.topic_path(slug).read_text(encoding="utf-8")
        before_metadata, _body = cli.parse_topic(topic_text)
        after_metadata = dict(before_metadata)
        after_metadata.update(interview_curriculum.compatibility_projection(canonical))
    mutation_id = "turn_" + hashlib.sha256(
        f"interview-cancel:{submission_id}".encode("utf-8")
    ).hexdigest()[:32]
    cli._commit_projected_turn(
        slug,
        before_state,
        after_state,
        f"<!-- openlearn-turn:{mutation_id} -->",
        [],
        mutation_id,
        before_metadata=before_metadata,
        after_metadata=after_metadata,
    )
