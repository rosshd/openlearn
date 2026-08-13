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
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

from openlearn.models import TutorSessionKind


TurnIntent = Literal["answer", "question", "confusion", "navigation"]
ProgressionIntent = Literal["continue", "skip", "practice", "revisit"]
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


_GENERATION_LOCK = threading.RLock()
_COURSE_LOCKS: dict[str, threading.RLock] = {}
_COURSE_LOCKS_GUARD = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="openlearn-tutor")
_FUTURES: dict[tuple[str, str], Future[TutorTurnResult]] = {}
_FUTURES_GUARD = threading.Lock()
_RUNNING: set[tuple[str, str]] = set()
_LIVE_TURNS: dict[tuple[str, str], _LiveTurnState] = {}
_OPERATION_TIMEOUT = timedelta(minutes=3)


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
    receipts = cli._validated_turn_receipts(state)
    permanent = receipts.get(f"operation_{submission_id.replace('-', '')}")
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
        receipts = cli._validated_turn_receipts(state)
        permanent = receipts.get(f"operation_{submission_id.replace('-', '')}")
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
        internal[active_key] = (
            None
            if status in {"committed", "retryable_error", "conflict"}
            else {
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
        )
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
    if isinstance(owner_pid, int) and owner_pid > 0:
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
        permanent = receipts.get(f"operation_{sid.replace('-', '')}")
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
                for key in ("cursor", "deferred", "session_id")
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
    course_revision_value = course_revision(slug)
    if expected_revision is not None and expected_revision != course_revision_value:
        raise TutorConflictError("course changed; refresh before submitting again")
    state = cli.load_state(slug)
    internal = _internal_state(state)
    active_key = _active_operation_key(session_kind)
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
            return course_revision_value, recovered
        if recovered is None and active_sid != sid:
            raise TutorConflictError("another tutor turn is already active for this course")
        if recovered is None and _future_active(slug, sid):
            return course_revision_value, operation_status(slug, sid)
    if (
        replay is not None
        and replay.status == "retryable_error"
        and _future_active(slug, sid)
    ):
        return course_revision_value, replay
    revision = course_revision_value
    source_lesson: str | None = None
    source_lesson_skill_ref: dict[str, str] | None = None
    if session_kind == cli.SIDE_CHAT_SESSION_KIND:
        source_values = (
            source_lesson_id,
            source_lesson_title,
            source_lesson_revision,
        )
        if any(value is not None for value in source_values) and any(
            value is None for value in source_values
        ):
            raise TutorConflictError(
                "The visible lesson changed. Your question was not submitted; refresh and retry."
            )
        side_revision = internal.get("side_chat_revision")
        revision = side_revision if isinstance(side_revision, int) and side_revision >= 0 else 0
        source_topic = cli.read_topic(slug)
        source_entry = cli.last_tutor_lesson_entry(source_topic)
        source_lesson = source_entry[1]["response"].strip() if source_entry else ""
        expected_source_id = (
            cli.tutor_lesson_entry_id(source_entry[1]) if source_entry else ""
        )
        if all(value is None for value in source_values):
            source_lesson_id = expected_source_id
            source_lesson_title = (
                cli.tutor_response_focus_title(source_lesson) or "Saved lesson"
            )
            source_lesson_revision = course_revision_value
        elif (
            source_lesson_revision != course_revision_value
            or source_lesson_id != expected_source_id
        ):
            raise TutorConflictError(
                "The visible lesson changed. Your question was not submitted; refresh and retry."
            )
        source_lesson_skill_ref = _visible_curriculum_skill_ref(state)
    if session_kind != cli.SIDE_CHAT_SESSION_KIND:
        cli.save_pending_learner_prompt(slug, normalized)
    _save_operation(
        slug,
        submission_id=sid,
        status="saved",
        expected_revision=revision,
        prompt=normalized,
        payload_hash=payload_hash,
        owner_pid=os.getpid(),
        session_kind=session_kind,
        source_course_revision=course_revision_value,
        source_lesson=source_lesson,
        source_lesson_id=source_lesson_id,
        source_lesson_title=source_lesson_title,
        source_lesson_revision=source_lesson_revision,
        source_lesson_skill_ref=source_lesson_skill_ref,
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
    active_progression_before = (
        canonical_before.get("active_operation")
        if intent == "navigation"
        and session_kind != cli.SIDE_CHAT_SESSION_KIND
        and isinstance(canonical_before, dict)
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
                    else None
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
        or progression_intent not in {"continue", "skip", "practice", "revisit"}
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
            for key in ("cursor", "deferred", "session_id"):
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
