from __future__ import annotations

import copy
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

from openlearn.models import TutorSessionKind


TurnIntent = Literal["answer", "question", "confusion", "navigation"]
OperationStatus = Literal[
    "saved",
    "judging",
    "generating",
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


_GENERATION_LOCK = threading.RLock()
_COURSE_LOCKS: dict[str, threading.RLock] = {}
_COURSE_LOCKS_GUARD = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="openlearn-tutor")
_FUTURES: dict[tuple[str, str], Future[TutorTurnResult]] = {}
_FUTURES_GUARD = threading.Lock()
_RUNNING: set[tuple[str, str]] = set()
_OPERATION_TIMEOUT = timedelta(minutes=3)


def _course_lock(slug: str) -> threading.RLock:
    with _COURSE_LOCKS_GUARD:
        return _COURSE_LOCKS.setdefault(slug, threading.RLock())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    internal = _internal_state(cli.load_state(slug))
    value = internal.get("course_revision")
    return value if isinstance(value, int) and value >= 0 else 0


def operation_result(slug: str, submission_id: str) -> TutorTurnResult | None:
    from openlearn import cli

    internal = _internal_state(cli.load_state(slug))
    results = internal.get("turn_results")
    if not isinstance(results, dict):
        return None
    raw = results.get(submission_id)
    return _result_from_dict(raw) if isinstance(raw, dict) else None


def operation_status(slug: str, submission_id: str) -> TutorTurnResult | None:
    """Return either a terminal receipt or the current durable operation state."""
    from openlearn import cli

    with _course_lock(slug):
        # Read the receipt and active operation from one state snapshot. A turn
        # can commit between two reads, leaving no active turn in the newer
        # snapshot even though the receipt was absent from the older one.
        internal = _internal_state(cli.load_state(slug))
        results = internal.get("turn_results")
        if isinstance(results, dict):
            raw_result = results.get(submission_id)
            if isinstance(raw_result, dict):
                return _result_from_dict(raw_result)
        active = internal.get("active_turn")
        if not isinstance(active, dict) or active.get("submission_id") != submission_id:
            return None
        recovered = _recover_active_turn(slug, active)
        if recovered is not None:
            return recovered
        status = active.get("status")
        if status not in {"saved", "generating"}:
            return None
        return TutorTurnResult(
            submission_id=submission_id,
            status=status,
            input_status="saved",
            message_kind="",
            move=None,
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
) -> None:
    from openlearn import cli

    def update(state: dict[str, object]) -> None:
        internal = _internal_state(state)
        internal["active_turn"] = (
            None
            if status in {"committed", "retryable_error", "conflict"}
            else {
                "submission_id": submission_id,
                "status": status,
                "expected_revision": expected_revision,
                "prompt": prompt,
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
            results[submission_id] = asdict(result)
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
    slug: str, active: dict[str, object]
) -> TutorTurnResult | None:
    """Turn an expired or process-orphaned operation into a durable receipt."""
    submission_id = active.get("submission_id")
    if not isinstance(submission_id, str):
        return None
    revision = active.get("expected_revision")
    expected_revision = revision if isinstance(revision, int) else course_revision(slug)
    prompt = active.get("prompt")
    normalized = prompt if isinstance(prompt, str) else ""
    age = _active_turn_age(active)
    # A live worker owns the operation even if a slow provider exhausts the
    # normal restart-recovery window. Only orphaned durable records expire.
    if _future_active(slug, submission_id):
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
    )
    _save_operation(
        slug,
        submission_id=submission_id,
        status="retryable_error",
        expected_revision=expected_revision,
        prompt=normalized,
        result=result,
        error_code=code,
    )
    return result


def _prepare_turn(
    slug: str,
    normalized: str,
    sid: str,
    expected_revision: int | None,
) -> tuple[int, TutorTurnResult | None]:
    from openlearn import cli

    replay = operation_result(slug, sid)
    if replay is not None and replay.status == "committed":
        return course_revision(slug), replay
    revision = course_revision(slug)
    if expected_revision is not None and expected_revision != revision:
        raise TutorConflictError("course changed; refresh before submitting again")
    state = cli.load_state(slug)
    internal = _internal_state(state)
    active = internal.get("active_turn")
    if isinstance(active, dict):
        active_sid = active.get("submission_id")
        recovered = _recover_active_turn(slug, active)
        if recovered is not None and active_sid == sid:
            return revision, recovered
        if recovered is None and active_sid != sid:
            raise TutorConflictError("another tutor turn is already active for this course")
        if recovered is None and _future_active(slug, sid):
            return revision, operation_status(slug, sid)
    if (
        replay is not None
        and replay.status == "retryable_error"
        and _future_active(slug, sid)
    ):
        return revision, replay
    cli.save_pending_learner_prompt(slug, normalized)
    _save_operation(
        slug,
        submission_id=sid,
        status="saved",
        expected_revision=revision,
        prompt=normalized,
    )
    return revision, None


def _execute_prepared_turn(
    slug: str,
    normalized: str,
    sid: str,
    revision: int,
    intent: TurnIntent,
    model: str | None,
    session_kind: TutorSessionKind,
) -> TutorTurnResult:
    from openlearn import cli

    with _course_lock(slug):
        replay = operation_result(slug, sid)
        if replay is not None and replay.status == "committed":
            return replay
        internal = _internal_state(cli.load_state(slug))
        active = internal.get("active_turn")
        if not isinstance(active, dict) or active.get("submission_id") != sid:
            if replay is not None and replay.status == "retryable_error":
                raise TutorOperationError(
                    replay.error_message or "Tutor turn must be retried."
                )
            raise TutorOperationError("Tutor turn is no longer active. Retry it.")
        _save_operation(
            slug,
            submission_id=sid,
            status="generating",
            expected_revision=revision,
            prompt=normalized,
        )
    result_holder: list[TutorTurnResult] = []

    def commit_receipt(
        answer: str,
        metadata: dict[str, object],
        state_after: dict[str, object],
    ) -> None:
        with _course_lock(slug):
            current_internal = _internal_state(cli.load_state(slug))
            current_active = current_internal.get("active_turn")
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
            current_revision = current_internal.get("course_revision")
            if current_revision != revision:
                raise cli.TurnCommitConflictError(
                    "course changed while the tutor was preparing a response"
                )
            internal = _internal_state(state_after)
            new_revision = revision + 1
            move = _move_from_answer(answer, new_revision, metadata)
            result = TutorTurnResult(
                submission_id=sid,
                status="committed",
                input_status="committed",
                message_kind=_message_kind(intent),
                move=move,
            )
            results = internal.get("turn_results")
            results = dict(results) if isinstance(results, dict) else {}
            results[sid] = asdict(result)
            while len(results) > 50:
                results.pop(next(iter(results)))
            internal["course_revision"] = new_revision
            internal["active_turn"] = None
            internal["turn_results"] = results
            last_error = internal.get("last_turn_error")
            if isinstance(last_error, dict) and last_error.get("submission_id") == sid:
                internal.pop("last_turn_error", None)
            state_after["_openlearn_internal"] = internal
            result_holder.append(result)

    try:
        with _GENERATION_LOCK:
            cli.ask_topic(
                slug,
                normalized,
                model=model,
                output_func=lambda _text="": None,
                pending_learner_prompt=normalized,
                allow_specialized_actions=False,
                session_kind=session_kind,
                message_kind_override=(
                    _message_kind(intent) if intent != "answer" else None
                ),
                commit_state_hook=commit_receipt,
            )
        if not result_holder:
            raise TutorOperationError("tutor turn did not produce a committed result")
        return result_holder[0]
    except cli.TurnCommitConflictError as exc:
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
            )
            _save_operation(
                slug,
                submission_id=sid,
                status="conflict",
                expected_revision=revision,
                prompt=normalized,
                result=result,
                error_code="course_revision_changed",
            )
        raise TutorConflictError(result.error_message) from exc
    except Exception as exc:
        with _course_lock(slug):
            committed = operation_result(slug, sid)
            if committed is not None and committed.status == "committed":
                return committed
            if committed is not None and committed.status == "retryable_error":
                raise TutorOperationError(
                    committed.error_message or "Tutor turn must be retried."
                ) from exc
            result = TutorTurnResult(
                submission_id=sid,
                status="retryable_error",
                input_status="saved",
                message_kind=_message_kind(intent),
                move=None,
                error_code="provider_or_turn_failure",
                error_message="Your response is saved. Retry when the provider is available.",
            )
            _save_operation(
                slug,
                submission_id=sid,
                status="retryable_error",
                expected_revision=revision,
                prompt=normalized,
                result=result,
                error_code="provider_or_turn_failure",
            )
        if isinstance(exc, TutorOperationError):
            raise
        raise TutorOperationError(result.error_message) from exc


def submit_turn(
    slug: str,
    text: str,
    *,
    intent: TurnIntent = "answer",
    submission_id: str | None = None,
    expected_revision: int | None = None,
    model: str | None = None,
    session_kind: TutorSessionKind = "chat",
) -> TutorTurnResult:
    """Run one durable, presentation-independent learner turn.

    The existing turn journal remains the commit authority. This service adds
    request idempotency and a structured result for non-terminal interfaces.
    """

    sid, normalized = _validate_turn(text, submission_id)
    with _course_lock(slug):
        revision, replay = _prepare_turn(slug, normalized, sid, expected_revision)
        if replay is not None:
            return replay
        with _FUTURES_GUARD:
            _RUNNING.add((slug, sid))
    try:
        return _execute_prepared_turn(
            slug, normalized, sid, revision, intent, model, session_kind
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
) -> TutorTurnResult:
    """Persist a turn, then execute it in the bounded tutor worker pool."""
    sid, normalized = _validate_turn(text, submission_id)
    with _course_lock(slug):
        revision, replay = _prepare_turn(slug, normalized, sid, expected_revision)
        if replay is not None:
            return replay
        pending = TutorTurnResult(
            submission_id=sid,
            status="saved",
            input_status="saved",
            message_kind=_message_kind(intent),
            move=None,
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
            )
            _save_operation(
                slug,
                submission_id=sid,
                status="retryable_error",
                expected_revision=revision,
                prompt=normalized,
                result=failed,
                error_code="executor_unavailable",
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

        future.add_done_callback(release)
        return pending
