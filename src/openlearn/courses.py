"""Shared, output-free course queries and creation operations."""

from __future__ import annotations

import copy
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, cast
from uuid import UUID, uuid4

from openlearn import interview_curriculum, interview_prep
from openlearn import stats as stats_metrics
from openlearn.application import (
    ActionableReview,
    CalibrationContext,
    CourseCard,
    CourseContinuationBlocker,
    CourseActivationDestination,
    CourseActivationResult,
    CourseCreationRequest,
    CourseCreationResult,
    CourseDeletionConfirmationError,
    CourseDeletionPreview,
    CourseDeletionResult,
    CourseLibraryProjection,
    CoursePathItem,
    CourseProgress,
    CourseRecommendation,
    CourseReviewItem,
    CourseReviewQueue,
    CourseSettingsChange,
    CourseSettingsPreview,
    CourseSettingsResult,
    CourseSnapshot,
    DashboardSnapshot,
    FirstPassCoverage,
    InterviewCardProjection,
    ReviewProgress,
    TemplateCatalog,
    TemplateSummary,
    UnitProgress,
    interview_course_path,
    interview_learning_card_projection,
)
from openlearn.course_templates import (
    CourseTemplate,
    available_course_templates,
    load_course_template,
)

CALIBRATION_STATE_KEY = "progressive_calibration"
CREATION_SUBMISSION_STATE_KEY = "course_creation_submission_id"
CREATION_SUBMISSION_METADATA_KEY = "course_creation_submission_id"
CALIBRATION_TEXT_LIMIT = 4_000
LEGACY_RECONCILIATION_SCHEMA_VERSION = 1
RECONCILIATION_SCHEMA_VERSION = 2
ROUTE_ACCEPTANCE_SCHEMA_VERSION = 1
COURSE_SETTINGS_SCHEMA_VERSION = 1
COURSE_SETTINGS_EVENT_TYPE = "course_settings_changed"


class RouteAcceptanceConflictError(RuntimeError):
    """A pending route transaction lost its optimistic concurrency fence."""


class CourseSettingsConflictError(RuntimeError):
    """A course settings transaction lost its optimistic concurrency fence."""


class CourseDeletionConflictError(RuntimeError):
    """A permanent deletion lost its topic-generation fence."""


def _cli():
    """Import the legacy storage boundary lazily while it is being extracted."""
    from openlearn import cli

    return cli


def _course_revision(state: dict[str, object]) -> int:
    internal = state.get("_openlearn_internal")
    if not isinstance(internal, dict):
        return 0
    value = internal.get("course_revision")
    return value if isinstance(value, int) and value >= 0 else 0


def _course_has_active_operation(state: dict[str, object]) -> bool:
    canonical = state.get("interview_curriculum")
    if isinstance(canonical, dict) and isinstance(canonical.get("active_operation"), dict):
        return True
    internal = state.get("_openlearn_internal")
    return isinstance(internal, dict) and any(
        isinstance(internal.get(key), dict) for key in ("active_turn", "active_side_chat")
    )


def activate_course(slug: str) -> CourseActivationResult:
    """Select a course without recording any study activity."""
    cli = _cli()
    topic = cli.read_topic_stats(slug)
    state = cli.load_state(slug)
    destination = "focus"
    profile_path = cli.interview_profile_path(slug)
    if _course_has_active_operation(state):
        destination = "recovery"
    elif profile_path.exists():
        profile = interview_prep.load_profile(profile_path)
        placement = profile.get("placement")
        status = placement.get("status") if isinstance(placement, dict) else None
        if status in {"not_started", "in_progress"}:
            destination = "placement"
        elif topic.metadata.get("course_started") is not True:
            destination = "initialization"
    elif topic.metadata.get("course_started") is not True:
        destination = "initialization"
    from openlearn import application

    if destination not in {"placement", "recovery"} and not application.provider_status().ready:
        destination = "setup"
    cli.activate_topic_without_study(slug)
    return CourseActivationResult(
        slug=slug, destination=cast(CourseActivationDestination, destination)
    )


def preview_course_deletion(slug: str) -> CourseDeletionPreview:
    """Return a side-effect-free, generation-fenced deletion description."""
    cli = _cli()
    canonical = cli.slugify(slug)
    if canonical != slug:
        raise cli.OpenLearnError(f"invalid topic slug: {slug}")
    topic = cli.read_topic_stats(slug)
    generation = cli.current_topic_generation(slug)
    if generation is None:
        raise cli.OpenLearnError(f"topic not found: {slug}")
    categories = ["course content", "learning progress", "tutor history"]
    if (cli.topics_dir() / "drills" / slug).exists():
        categories.append("practice workspaces")
    if (
        cli.attempt_store().topic_dir(slug).exists()
        or (cli.topics_dir() / "interview-attempts" / slug).exists()
    ):
        categories.append("coding attempt records")
    return CourseDeletionPreview(
        slug=slug,
        title=str(topic.metadata.get("topic") or slug.replace("-", " ").title()),
        topic_generation=generation,
        affected_data=tuple(categories),
    )


def confirm_course_deletion(
    preview: CourseDeletionPreview,
    *,
    confirmation_slug: str,
    confirmation_title: str,
) -> CourseDeletionResult:
    """Permanently delete one previewed generation and choose a safe preview."""
    cli = _cli()
    if confirmation_slug != preview.slug or confirmation_title != preview.title:
        raise CourseDeletionConfirmationError(
            "enter the exact course slug and title to confirm permanent deletion"
        )
    try:
        deleted = cli.delete_topic_files(
            preview.slug,
            expected_generation=preview.topic_generation,
            expected_title=preview.title,
            allow_replay=True,
        )
    except cli.OpenLearnError as exc:
        if "generation changed" in str(exc) or "title changed" in str(exc):
            raise CourseDeletionConflictError(str(exc)) from exc
        raise
    cli.clear_active_topic(preview.slug)
    next_selected = dashboard_snapshot().selected_slug
    return CourseDeletionResult(
        slug=preview.slug,
        title=preview.title,
        topic_generation=preview.topic_generation,
        deleted=deleted,
        replayed=not deleted,
        next_selected_slug=next_selected,
    )


def replay_course_deletion(
    slug: str,
    *,
    confirmation_slug: str,
    confirmation_title: str,
    topic_generation: str,
) -> CourseDeletionResult | None:
    """Replay an exact durable deletion identity before reading live course state."""
    cli = _cli()
    tombstone = cli.read_topic_deletion_tombstone(slug)
    if tombstone is None:
        return None
    if confirmation_slug != slug or tombstone.get("deleted_title") != confirmation_title:
        raise CourseDeletionConfirmationError(
            "enter the exact course slug and title to confirm permanent deletion"
        )
    if tombstone.get("deleted_generation") != topic_generation:
        raise CourseDeletionConflictError(
            "This course changed after the deletion page opened."
        )
    preview = CourseDeletionPreview(
        slug=slug,
        title=confirmation_title,
        topic_generation=topic_generation,
        affected_data=(),
    )
    return confirm_course_deletion(
        preview,
        confirmation_slug=confirmation_slug,
        confirmation_title=confirmation_title,
    )


def _normalize_setting_text(value: str | None, current: str, *, label: str) -> str:
    if value is None:
        return current
    if value != value.strip() or not value:
        raise _cli().OpenLearnError(f"course {label} must be non-empty without outer whitespace")
    limit = 200 if label == "name" else 4_000
    if len(value) > limit:
        raise _cli().OpenLearnError(f"course {label} must be at most {limit} characters")
    return value


def _bounded_minutes(value: int | None, current: int, *, label: str) -> int:
    if value is None:
        return current
    if isinstance(value, bool) or not isinstance(value, int) or not 15 <= value <= 10_080:
        raise _cli().OpenLearnError(f"{label} must be between 15 and 10080 minutes")
    return value


def _settings_payload(preview: CourseSettingsPreview) -> dict[str, object]:
    return {
        "slug": preview.slug,
        "topic_generation": preview.topic_generation,
        "expected_revision": preview.expected_revision,
        "expected_profile_revision": preview.expected_profile_revision,
        "title": preview.title,
        "goal": preview.goal,
        "difficulty": preview.difficulty,
        "weekly_minutes": preview.weekly_minutes,
        "session_minutes": preview.session_minutes,
        "outline": preview.outline,
        "interview_fields": dict(preview.interview_fields),
    }


def preview_course_settings(
    slug: str, changes: CourseSettingsChange
) -> CourseSettingsPreview:
    """Normalize one learner-facing settings change without writing storage."""
    cli = _cli()
    recover_course_settings(slug)
    topic = cli.read_topic(slug)
    state = cli.load_state(slug)
    if _course_has_active_operation(state):
        raise CourseSettingsConflictError("another tutor operation is active for this course")
    generation = cli.current_topic_generation(slug)
    if generation is None:
        raise cli.OpenLearnError(f"topic not found: {slug}")
    title = _normalize_setting_text(
        changes.title, str(topic.metadata.get("topic") or topic.slug), label="name"
    )
    goal = _normalize_setting_text(
        changes.goal, str(topic.metadata.get("goal") or ""), label="goal"
    )
    difficulty = (
        changes.difficulty
        if changes.difficulty is not None
        else cli.normalize_mastery_profile(topic.metadata.get("mastery_profile"))
    )
    if difficulty not in {"efficient", "proficient", "deep"}:
        raise cli.OpenLearnError("difficulty must be efficient, proficient, or deep")
    profile_path = cli.interview_profile_path(slug)
    profile_value: dict[str, object] | None = None
    profile_revision: int | None = None
    profile_fields: dict[str, object] = {}
    outline = changes.outline
    if profile_path.exists():
        profile_value = interview_prep.load_profile(profile_path)
        raw_profile = profile_value.get("profile")
        if not isinstance(raw_profile, dict):
            raise cli.OpenLearnError("interview-prep profile is malformed")
        profile_fields = dict(changes.interview_fields or {})
        allowed_interview_fields = set(interview_prep.PROFILE_FIELDS) | set(
            interview_prep.OUTLINE_CHANGE_FIELDS
        )
        unexpected = set(profile_fields) - allowed_interview_fields
        if unexpected:
            raise cli.OpenLearnError(
                "unsupported interview setting: " + ", ".join(sorted(unexpected))
            )
        current_weekly = int(raw_profile["weekly_minutes"])
        current_session = int(raw_profile["session_minutes"])
        profile_revision_raw = profile_value.get("profile_revision")
        profile_revision = (
            profile_revision_raw if isinstance(profile_revision_raw, int) else None
        )
    else:
        current_weekly = int(topic.metadata.get("weekly_minutes") or 120)
        current_session = int(topic.metadata.get("session_minutes") or 45)
        if changes.interview_fields:
            raise cli.OpenLearnError("interview settings apply only to interview courses")
    weekly = _bounded_minutes(changes.weekly_minutes, current_weekly, label="weekly minutes")
    session = _bounded_minutes(changes.session_minutes, current_session, label="session minutes")
    if session > weekly:
        raise cli.OpenLearnError("session minutes cannot exceed weekly minutes")
    if profile_value is not None:
        profile_fields.update({"weekly_minutes": weekly, "session_minutes": session})
        current_profile = profile_value.get("profile")
        assert isinstance(current_profile, dict)
        try:
            profile_updates = {
                key: value
                for key, value in profile_fields.items()
                if key in interview_prep.PROFILE_FIELDS
            }
            normalized_profile = interview_prep.normalize_profile_update(
                current_profile, profile_updates
            )
            profile_fields.update(
                {key: normalized_profile[key] for key in profile_updates}
            )
            route_updates = {
                key: value
                for key, value in profile_fields.items()
                if key in interview_prep.OUTLINE_CHANGE_FIELDS
            }
            if route_updates:
                canonical = state.get("interview_curriculum")
                bundle = (
                    interview_curriculum.load_pinned_bundle(
                        str(canonical["bundle_id"]), str(canonical["bundle_version"])
                    )
                    if isinstance(canonical, dict)
                    else interview_curriculum.load_default_bundle()
                )
                previewed_route = interview_prep.preview_curriculum_change(
                    profile_value,
                    changes=route_updates,
                    current_date=date.today(),
                    bundle=bundle,
                )
                if outline is not None:
                    expected_outline = str(previewed_route.get("outline") or "").strip()
                    if outline.strip() != expected_outline:
                        raise cli.OpenLearnError(
                            "free-form interview outline replacement is unsupported; "
                            "use the bounded outline controls"
                        )
        except ValueError as exc:
            raise cli.OpenLearnError(str(exc)) from exc
    if outline is not None:
        if outline != outline.strip() or not outline:
            raise cli.OpenLearnError("course outline must be non-empty without outer whitespace")
        if len(outline) > 20_000:
            raise cli.OpenLearnError("course outline must be at most 20000 characters")
        if profile_value is None and not cli.parse_course_units(outline):
            raise cli.OpenLearnError("course outline must contain numbered units")
    values = {
        "slug": slug,
        "topic_generation": generation,
        "expected_revision": _course_revision(state),
        "expected_profile_revision": profile_revision,
        "title": title,
        "goal": goal,
        "difficulty": difficulty,
        "weekly_minutes": weekly,
        "session_minutes": session,
        "outline": outline,
        "interview_fields": dict(sorted(profile_fields.items())),
    }
    payload_hash = interview_curriculum.canonical_fingerprint(values)
    return CourseSettingsPreview(
        slug=slug,
        topic_generation=generation,
        expected_revision=_course_revision(state),
        expected_profile_revision=profile_revision,
        title=title,
        goal=goal,
        difficulty=cast(Literal["efficient", "proficient", "deep"], difficulty),
        weekly_minutes=weekly,
        session_minutes=session,
        outline=outline,
        interview_fields=tuple(sorted(profile_fields.items())),
        payload_hash=payload_hash,
    )


def _reconcile_generated_course_body(
    body: str, *, old_title: str, old_goal: str, new_title: str, new_goal: str
) -> str:
    """Refresh generated title/goal text while preserving learner-authored prose."""
    updated = body
    heading = f"# {old_title}"
    if updated.startswith(heading + "\n") or updated == heading:
        updated = f"# {new_title}" + updated[len(heading) :]
    match = re.search(r"(?m)^## Current Goal\s*$", updated)
    if match is None:
        insertion = f"\n\n## Current Goal\n\n{new_goal}\n"
        first_break = updated.find("\n")
        return (
            updated[: first_break + 1] + insertion + updated[first_break + 1 :]
            if first_break >= 0
            else updated + insertion
        )
    next_section = re.search(r"(?m)^## ", updated[match.end() :])
    end = match.end() + next_section.start() if next_section else len(updated)
    content = updated[match.end() : end].strip()
    if content == old_goal.strip():
        replacement = f"## Current Goal\n\n{new_goal}\n\n"
    else:
        lines = content.splitlines()
        removed_old_goal = False
        preserved: list[str] = []
        for line in lines:
            if not removed_old_goal and line.strip() == old_goal.strip():
                removed_old_goal = True
                continue
            preserved.append(line)
        notes = "\n".join(preserved).strip()
        replacement = f"## Current Goal\n\n{new_goal}\n\n"
        if notes:
            replacement += f"## Goal Notes\n\n{notes}\n\n"
    return updated[: match.start()] + replacement + updated[end:].lstrip("\n")


def _settings_checkpoint(_stage: str) -> None:
    """Fault-injection seam for course-settings publication boundaries."""


def _settings_identity(value: dict[str, object]) -> dict[str, object]:
    return {key: item for key, item in value.items() if key != "journal_sha256"}


def _validated_settings_journal(slug: str, value: object) -> dict[str, object]:
    required = {
        "schema_version",
        "slug",
        "topic_generation",
        "submission_id",
        "payload_hash",
        "topic_before_sha256",
        "topic_after",
        "state_before_sha256",
        "state_after",
        "profile_before_sha256",
        "profile_after",
        "event",
        "receipt",
        "journal_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise _cli().OpenLearnError("saved course settings update is malformed")
    receipt = value.get("receipt")
    if (
        value.get("schema_version") != COURSE_SETTINGS_SCHEMA_VERSION
        or value.get("slug") != slug
        or not isinstance(value.get("topic_generation"), str)
        or not isinstance(value.get("submission_id"), str)
        or not isinstance(value.get("payload_hash"), str)
        or not isinstance(value.get("topic_after"), str)
        or not isinstance(value.get("state_after"), dict)
        or value.get("profile_after") is not None
        and not isinstance(value.get("profile_after"), dict)
        or not isinstance(value.get("event"), dict)
        or not isinstance(receipt, dict)
        or receipt.get("submission_id") != value.get("submission_id")
        or receipt.get("payload_hash") != value.get("payload_hash")
        or value.get("journal_sha256")
        != interview_curriculum.canonical_fingerprint(_settings_identity(value))
    ):
        raise _cli().OpenLearnError("saved course settings update has invalid identity")
    return copy.deepcopy(value)


def _json_text(value: dict[str, object]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _settings_locks(slug: str):
    """Acquire settings-related stores in one stable journal-to-data order."""
    import contextlib

    cli = _cli()
    stack = contextlib.ExitStack()
    stack.enter_context(cli.file_lock(cli.interview_route_journal_path(slug)))
    stack.enter_context(cli.file_lock(cli.topic_turn_journal_path(slug)))
    stack.enter_context(cli.topic_store_locks(slug))
    if cli.interview_profile_path(slug).exists():
        stack.enter_context(cli.file_lock(cli.interview_profile_path(slug)))
    return stack


def _apply_settings_journal_locked(slug: str, raw: dict[str, object]) -> None:
    cli = _cli()
    journal = _validated_settings_journal(slug, raw)
    if cli.current_topic_generation(slug) != journal["topic_generation"]:
        raise CourseSettingsConflictError("course generation changed during settings update")
    topic_path = cli.topic_path(slug)
    current_topic = topic_path.read_text(encoding="utf-8")
    topic_after = str(journal["topic_after"])
    current_topic_sha = interview_curriculum.canonical_fingerprint(current_topic)
    after_topic_sha = interview_curriculum.canonical_fingerprint(topic_after)
    if current_topic_sha not in {journal["topic_before_sha256"], after_topic_sha}:
        raise CourseSettingsConflictError("course content changed during settings update")
    state_path = cli.topic_state_path(slug)
    current_state = cli._load_state_unlocked(slug)
    state_after = cast(dict[str, object], journal["state_after"])
    current_state_sha = interview_curriculum.canonical_fingerprint(current_state)
    after_state_sha = interview_curriculum.canonical_fingerprint(state_after)
    if current_state_sha not in {journal["state_before_sha256"], after_state_sha}:
        raise CourseSettingsConflictError("course state changed during settings update")
    profile_path = cli.interview_profile_path(slug)
    profile_after = journal.get("profile_after")
    current_profile: dict[str, object] | None = None
    if profile_after is not None:
        current_profile = interview_prep.load_profile(profile_path)
        current_profile_sha = interview_curriculum.canonical_fingerprint(current_profile)
        after_profile_sha = interview_curriculum.canonical_fingerprint(profile_after)
        if current_profile_sha not in {
            journal["profile_before_sha256"],
            after_profile_sha,
        }:
            raise CourseSettingsConflictError(
                "interview profile changed during settings update"
            )
    if current_topic_sha != after_topic_sha:
        cli.write_text_atomic(topic_path, topic_after)
    _settings_checkpoint("after_topic")
    if current_state_sha != after_state_sha:
        cli.write_text_atomic(state_path, _json_text(state_after))
    _settings_checkpoint("after_state")
    if profile_after is not None:
        assert current_profile is not None
        if interview_curriculum.canonical_fingerprint(current_profile) != after_profile_sha:
            interview_prep._write(profile_path, cast(dict[str, object], profile_after))
    _settings_checkpoint("after_profile")
    event = cast(dict[str, object], journal["event"])
    events_path = cli.topic_events_path(slug)
    event_id = str(event["event_id"])
    _append_event_once(events_path, event_id, event)
    _settings_checkpoint("after_event")
    receipt_path = cli.course_settings_receipt_path(slug, str(journal["submission_id"]))
    receipt = cast(dict[str, object], journal["receipt"])
    if receipt_path.exists():
        saved = json.loads(receipt_path.read_text(encoding="utf-8"))
        if saved != receipt:
            raise CourseSettingsConflictError("course settings receipt conflicts")
    else:
        cli.write_text_atomic(receipt_path, _json_text(receipt))
    _settings_checkpoint("after_receipt")


def recover_course_settings(slug: str) -> bool:
    """Complete one validated pending settings transaction before course use."""
    cli = _cli()
    journal_path = cli.course_settings_journal_path(slug)
    with cli.file_lock(journal_path):
        if not journal_path.exists():
            return False
        try:
            raw = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise cli.OpenLearnError("saved course settings update is unreadable") from exc
        journal = _validated_settings_journal(slug, raw)
        with _settings_locks(slug):
            _apply_settings_journal_locked(slug, journal)
        cli.durable_unlink(journal_path)
        return True


def _settings_result(receipt: dict[str, object], *, replayed: bool) -> CourseSettingsResult:
    return CourseSettingsResult(
        slug=str(receipt["slug"]),
        revision=int(receipt["final_revision"]),
        receipt_id=str(receipt["submission_id"]),
        replayed=replayed,
    )


def _canonical_settings_submission_id(submission_id: str) -> UUID:
    cli = _cli()
    try:
        parsed = UUID(submission_id)
    except (ValueError, AttributeError) as exc:
        raise cli.OpenLearnError("submission ID must be a canonical UUID") from exc
    if str(parsed) != submission_id:
        raise cli.OpenLearnError("submission ID must be a canonical UUID")
    return parsed


def _validated_settings_receipt(
    slug: str, submission_id: str, value: object
) -> dict[str, object]:
    required = {
        "schema_version",
        "slug",
        "topic_generation",
        "submission_id",
        "payload_hash",
        "base_revision",
        "final_revision",
        "receipt_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise _cli().OpenLearnError("saved course settings receipt is malformed")
    unsigned = dict(value)
    receipt_hash = unsigned.pop("receipt_sha256", None)
    base_revision = value.get("base_revision")
    final_revision = value.get("final_revision")
    if (
        value.get("schema_version") != COURSE_SETTINGS_SCHEMA_VERSION
        or value.get("slug") != slug
        or not isinstance(value.get("topic_generation"), str)
        or value.get("submission_id") != submission_id
        or not isinstance(value.get("payload_hash"), str)
        or re.fullmatch(r"[a-f0-9]{64}", str(value["payload_hash"])) is None
        or isinstance(base_revision, bool)
        or not isinstance(base_revision, int)
        or isinstance(final_revision, bool)
        or not isinstance(final_revision, int)
        or final_revision != base_revision + 1
        or not isinstance(receipt_hash, str)
        or receipt_hash != interview_curriculum.canonical_fingerprint(unsigned)
    ):
        raise _cli().OpenLearnError("saved course settings receipt has invalid identity")
    return copy.deepcopy(value)


def replay_course_settings(
    slug: str, *, submission_id: str, expected_payload_hash: str
) -> CourseSettingsResult | None:
    """Replay an exact durable settings result before rebuilding live state."""
    cli = _cli()
    _canonical_settings_submission_id(submission_id)
    if re.fullmatch(r"[a-f0-9]{64}", expected_payload_hash) is None:
        raise cli.OpenLearnError("expected payload hash must be lowercase hexadecimal")
    journal_path = cli.course_settings_journal_path(slug)
    receipt_path = cli.course_settings_receipt_path(slug, submission_id)
    with cli.file_lock(journal_path):
        if receipt_path.exists():
            try:
                saved = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise cli.OpenLearnError("saved course settings receipt is unreadable") from exc
            receipt = _validated_settings_receipt(slug, submission_id, saved)
            if receipt["payload_hash"] != expected_payload_hash:
                raise CourseSettingsConflictError(
                    "submission ID was already used with different settings"
                )
            return _settings_result(receipt, replayed=True)
        if not journal_path.exists():
            return None
        try:
            raw_pending = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise cli.OpenLearnError("saved course settings update is unreadable") from exc
        pending = _validated_settings_journal(slug, raw_pending)
        if pending["submission_id"] != submission_id:
            return None
        if pending["payload_hash"] != expected_payload_hash:
            raise CourseSettingsConflictError(
                "submission ID was already used with different settings"
            )
        with _settings_locks(slug):
            _apply_settings_journal_locked(slug, pending)
        cli.durable_unlink(journal_path)
        try:
            saved = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise cli.OpenLearnError("saved course settings receipt is unreadable") from exc
        receipt = _validated_settings_receipt(slug, submission_id, saved)
        return _settings_result(receipt, replayed=True)


def confirm_course_settings(
    preview: CourseSettingsPreview, *, submission_id: str
) -> CourseSettingsResult:
    """Publish a preview through an idempotent recoverable settings transaction."""
    cli = _cli()
    parsed = _canonical_settings_submission_id(submission_id)
    if interview_curriculum.canonical_fingerprint(_settings_payload(preview)) != preview.payload_hash:
        raise cli.OpenLearnError("course settings preview has been modified")
    slug = preview.slug
    journal_path = cli.course_settings_journal_path(slug)
    receipt_path = cli.course_settings_receipt_path(slug, submission_id)
    with cli.file_lock(journal_path):
        if journal_path.exists():
            raw_pending = json.loads(journal_path.read_text(encoding="utf-8"))
            pending = _validated_settings_journal(slug, raw_pending)
            if pending["submission_id"] != submission_id:
                raise CourseSettingsConflictError("another course settings update is pending")
            if pending["payload_hash"] != preview.payload_hash:
                raise CourseSettingsConflictError(
                    "submission ID was already used with different settings"
                )
            with _settings_locks(slug):
                _apply_settings_journal_locked(slug, pending)
            cli.durable_unlink(journal_path)
        if receipt_path.exists():
            receipt = _validated_settings_receipt(
                slug,
                submission_id,
                json.loads(receipt_path.read_text(encoding="utf-8")),
            )
            if receipt["payload_hash"] != preview.payload_hash:
                raise CourseSettingsConflictError(
                    "submission ID was already used with different settings"
                )
            return _settings_result(receipt, replayed=True)
        with _settings_locks(slug):
            if cli.current_topic_generation(slug) != preview.topic_generation:
                raise CourseSettingsConflictError("course generation changed after preview")
            state = cli._load_state_unlocked(slug)
            if _course_revision(state) != preview.expected_revision:
                raise CourseSettingsConflictError("course revision changed after preview")
            if _course_has_active_operation(state):
                raise CourseSettingsConflictError(
                    "finish or cancel the active tutor operation before changing settings"
                )
            topic_text = cli.topic_path(slug).read_text(encoding="utf-8")
            metadata, body = cli.parse_topic(topic_text)
            metadata = dict(metadata)
            old_title = str(metadata.get("topic") or slug.replace("-", " ").title())
            old_goal = str(metadata.get("goal") or "")
            metadata.update(
                {
                    "topic": preview.title,
                    "goal": preview.goal,
                    "mastery_profile": preview.difficulty,
                    "weekly_minutes": preview.weekly_minutes,
                    "session_minutes": preview.session_minutes,
                }
            )
            state_after = copy.deepcopy(state)
            if preview.outline is not None and not cli.interview_profile_path(slug).exists():
                units = cli.parse_course_units(preview.outline)
                old_current = cli.course_unit_at(
                    cli.merge_topic_state(metadata, state),
                    int(state.get("current_unit") or 1),
                )
                old_current_title = (
                    str(old_current.get("title")) if isinstance(old_current, dict) else ""
                )
                metadata["course_units"] = units
                matched = next(
                    (
                        index
                        for index, unit in enumerate(units, start=1)
                        if str(unit.get("title")) == old_current_title
                    ),
                    1,
                )
                state_after["current_unit"] = matched
                state_after["current_slide"] = min(
                    int(state_after.get("current_slide") or 1),
                    int(units[matched - 1].get("slide_count") or 1),
                )
                state_after["current_focus"] = str(units[matched - 1].get("title") or "")
            body_after = _reconcile_generated_course_body(
                body,
                old_title=old_title,
                old_goal=old_goal,
                new_title=preview.title,
                new_goal=preview.goal,
            )
            internal_raw = state_after.get("_openlearn_internal")
            internal = copy.deepcopy(internal_raw) if isinstance(internal_raw, dict) else {}
            internal.setdefault("schema_version", 1)
            internal.setdefault("turn_results", {})
            internal["course_revision"] = preview.expected_revision + 1
            state_after["_openlearn_internal"] = internal
            profile_before_sha: str | None = None
            profile_after: dict[str, object] | None = None
            profile_path = cli.interview_profile_path(slug)
            if profile_path.exists():
                current_profile = interview_prep.load_profile(profile_path)
                if current_profile.get("profile_revision") != preview.expected_profile_revision:
                    raise CourseSettingsConflictError(
                        "interview profile revision changed after preview"
                    )
                profile_before_sha = interview_curriculum.canonical_fingerprint(current_profile)
                try:
                    moment = datetime.now(timezone.utc)
                    interview_updates = dict(preview.interview_fields)
                    route_updates = {
                        key: value
                        for key, value in interview_updates.items()
                        if key in interview_prep.OUTLINE_CHANGE_FIELDS
                    }
                    profile_only_updates = {
                        key: value
                        for key, value in interview_updates.items()
                        if key in interview_prep.PROFILE_FIELDS
                        and key not in interview_prep.OUTLINE_CHANGE_FIELDS
                    }
                    existing_canonical = state_after.get("interview_curriculum")
                    if isinstance(existing_canonical, dict) and (
                        route_updates or preview.outline is not None
                    ):
                        bundle = interview_curriculum.load_pinned_bundle(
                            str(existing_canonical["bundle_id"]),
                            str(existing_canonical["bundle_version"]),
                        )
                        profile_after, route = interview_prep.accepted_curriculum_profile(
                            current_profile,
                            action="change",
                            changes=route_updates,
                            outline=preview.outline or "",
                            now=moment,
                            bundle=bundle,
                        )
                        canonical_after, _cursor_decision = (
                            interview_curriculum.rematerialize_canonical_state(
                                existing_canonical,
                                route,
                                change_id=f"settings_{parsed.hex}",
                            )
                        )
                        state_after["interview_curriculum"] = canonical_after
                        metadata.update(
                            interview_curriculum.compatibility_projection(canonical_after)
                        )
                        metadata["course_started"] = True
                        if profile_only_updates:
                            profile_after = interview_prep.profile_edit_projection(
                                profile_after,
                                profile_only_updates,
                                now=lambda: moment,
                            )
                    elif preview.outline is not None:
                        raise cli.OpenLearnError(
                            "confirm or skip placement before changing the interview outline"
                        )
                    else:
                        profile_updates = {
                            key: value
                            for key, value in interview_updates.items()
                            if key in interview_prep.PROFILE_FIELDS
                        }
                        profile_after = interview_prep.profile_edit_projection(
                            current_profile,
                            profile_updates,
                            now=lambda: moment,
                        )
                except ValueError as exc:
                    raise cli.OpenLearnError(str(exc)) from exc
            topic_after = cli.format_topic(cli.stable_metadata_for_topic(metadata), body_after)
            receipt: dict[str, object] = {
                "schema_version": COURSE_SETTINGS_SCHEMA_VERSION,
                "slug": slug,
                "topic_generation": preview.topic_generation,
                "submission_id": submission_id,
                "payload_hash": preview.payload_hash,
                "base_revision": preview.expected_revision,
                "final_revision": preview.expected_revision + 1,
            }
            receipt["receipt_sha256"] = interview_curriculum.canonical_fingerprint(receipt)
            event = {
                "schema_version": cli.EVENT_SCHEMA_VERSION,
                "event_id": f"settings_{parsed.hex}:0",
                "ts": datetime.now(timezone.utc).isoformat(),
                "event_type": COURSE_SETTINGS_EVENT_TYPE,
                "slug": slug,
                "data": {
                    "submission_id": submission_id,
                    "base_revision": preview.expected_revision,
                    "final_revision": preview.expected_revision + 1,
                    "changed_fields": sorted(_settings_payload(preview)),
                },
            }
            journal: dict[str, object] = {
                "schema_version": COURSE_SETTINGS_SCHEMA_VERSION,
                "slug": slug,
                "topic_generation": preview.topic_generation,
                "submission_id": submission_id,
                "payload_hash": preview.payload_hash,
                "topic_before_sha256": interview_curriculum.canonical_fingerprint(topic_text),
                "topic_after": topic_after,
                "state_before_sha256": interview_curriculum.canonical_fingerprint(state),
                "state_after": state_after,
                "profile_before_sha256": profile_before_sha,
                "profile_after": profile_after,
                "event": event,
                "receipt": receipt,
            }
            journal["journal_sha256"] = interview_curriculum.canonical_fingerprint(journal)
            cli.write_text_atomic(journal_path, _json_text(journal))
            journal_path.chmod(0o600)
            _settings_checkpoint("after_journal")
            _apply_settings_journal_locked(slug, journal)
        cli.durable_unlink(journal_path)
        return _settings_result(receipt, replayed=False)


def template_catalog() -> TemplateCatalog:
    return TemplateCatalog(
        templates=tuple(
            TemplateSummary(
                template_id=template.slug,
                name=template.name,
                goal=template.goal,
                tags=template.tags,
                units=template.units,
                entry_mode=template.entry_mode,
            )
            for template in available_course_templates()
        )
    )


def available_course_slug(name: str) -> str:
    """Choose the stable ``slug``, ``slug-2``, ... sequence without overwriting."""
    cli = _cli()
    base = cli.slugify(name)
    candidate = base
    suffix = 2
    while (
        cli.topic_path(candidate).exists() or cli.topic_deletion_tombstone_path(candidate).exists()
    ):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _unit_progress(metadata: dict[str, object]) -> tuple[UnitProgress, ...]:
    return tuple(
        UnitProgress(
            unit=int(cast(int | str | float, row["unit"])),
            title=str(row["title"]),
            known=int(cast(int | str | float, row["known"])),
            total=int(cast(int | str | float, row["total"])),
            percent=int(cast(int | str | float, row["percent"])),
        )
        for row in stats_metrics.unit_mastery(metadata)
    )


def _review_progress(metadata: dict[str, object], today: date) -> ReviewProgress:
    values = stats_metrics.review_forecast(metadata, today)
    return ReviewProgress(
        due_today=values["due_today"],
        due_this_week=values["due_this_week"],
        due_later=values["due_later"],
    )


def _course_progress(metadata: dict[str, object], today: date) -> CourseProgress:
    units = _unit_progress(metadata)
    known = sum(unit.known for unit in units)
    total = sum(unit.total for unit in units)
    return CourseProgress(
        known=known,
        total=total,
        percent=round(known / total * 100) if total else 0,
        units=units,
        reviews=_review_progress(metadata, today),
    )


def _calibration_from_state(state: dict[str, object]) -> CalibrationContext | None:
    value = state.get(CALIBRATION_STATE_KEY)
    if not isinstance(value, dict):
        return None
    goal = value.get("goal")
    experience = value.get("experience")
    skipped = value.get("skipped")
    recorded_at = value.get("recorded_at")
    return CalibrationContext(
        goal=goal if isinstance(goal, str) else "",
        experience=experience if isinstance(experience, str) else "",
        skipped=skipped if isinstance(skipped, bool) else False,
        recorded_at=recorded_at if isinstance(recorded_at, str) else "",
    )


def _unit_title(value: object, fallback: str) -> str:
    title = str(value).strip() if isinstance(value, str) else ""
    if not title:
        return fallback
    match = re.match(r"^Unit\s+\d+\s*:\s*(.*?)(?:\s+-\s+.*)?$", title)
    return match.group(1).strip() if match else title


def _generic_path(metadata: dict[str, object]) -> tuple[CoursePathItem, ...]:
    raw_units = metadata.get("course_units")
    items: list[tuple[str, str, str]] = []
    if isinstance(raw_units, list) and raw_units:
        for index, value in enumerate(raw_units, start=1):
            if not isinstance(value, dict):
                continue
            number = value.get("unit") if isinstance(value.get("unit"), int) else index
            title = _unit_title(value.get("title"), f"Unit {number}")
            items.append((f"unit:{number}", title, f"Unit {number}"))
    else:
        template_units = metadata.get("template_units")
        if isinstance(template_units, list):
            for index, value in enumerate(template_units, start=1):
                if not isinstance(value, str) or not value.strip():
                    continue
                items.append(
                    (
                        f"unit:{index}",
                        _unit_title(value, f"Unit {index}"),
                        f"Unit {index}",
                    )
                )
    if not items:
        return ()
    completed = metadata.get("course_completed") is True
    current_raw = metadata.get("current_unit")
    current_index = (
        max(0, min(len(items) - 1, current_raw - 1))
        if isinstance(current_raw, int) and current_raw > 0
        else 0
    )
    return tuple(
        CoursePathItem(
            identity=identity,
            title=title,
            unit_title=unit_title,
            status=(
                "covered"
                if completed or index < current_index
                else "current"
                if index == current_index
                else "upcoming"
            ),
        )
        for index, (identity, title, unit_title) in enumerate(items)
    )


def _normalized_specialty_terms(values: tuple[str, ...]) -> set[str]:
    terms: set[str] = set()
    for value in values:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
        if normalized:
            terms.add(normalized)
        terms.update(re.findall(r"[a-z0-9]+", value.casefold()))
    return terms


def recommend_follow_up_template(
    source_template_id: str | None, *, weak_areas: tuple[str, ...]
) -> CourseRecommendation | None:
    """Rank bundled specialty templates locally and deterministically."""
    if source_template_id is None:
        return None
    templates = available_course_templates()
    weak_terms = _normalized_specialty_terms(weak_areas)

    def rank(template: CourseTemplate) -> tuple[int, int]:
        exact = source_template_id in template.specializes_template_ids
        overlap = len(weak_terms & set(template.specializes_tags))
        return (2 if exact else 1 if overlap else 0, overlap)

    candidates = [template for template in templates if template.slug != source_template_id]
    scored = [(rank(template), index, template) for index, template in enumerate(candidates)]
    eligible = [item for item in scored if item[0][0] > 0]
    if not eligible:
        return None
    _score, _index, selected = max(
        eligible,
        key=lambda item: (item[0][0], item[0][1], -item[1]),
    )
    return CourseRecommendation(
        kind="curated",
        title=selected.name,
        goal=selected.goal,
        template_id=selected.slug,
    )


def course_due_reviews(
    slug: str, *, today_value: str | None = None
) -> CourseReviewQueue:
    """Read the gradeable scheduled-review queue for exactly one course."""
    cli = _cli()
    topic = cli.read_topic_stats(slug)
    items = tuple(
        CourseReviewItem(
            concept=str(item["concept"]),
            due=str(item["due"]),
            difficulty=str(item.get("difficulty") or "hard"),
        )
        for item in cli.select_due_review_items(
            cli.due_review_items(topic.metadata, today_value=today_value)
        )
    )
    return CourseReviewQueue(slug=slug, items=items)


def _course_library_projection(
    metadata: dict[str, object],
    state: dict[str, object],
    *,
    progress: CourseProgress,
    template_id: str | None,
    is_interview_course: bool,
) -> tuple[CourseLibraryProjection, InterviewCardProjection | None]:
    canonical_raw = state.get("interview_curriculum")
    canonical = canonical_raw if isinstance(canonical_raw, dict) else None
    interview_card = (
        interview_learning_card_projection(state, metadata) if canonical is not None else None
    )
    if interview_card is not None and canonical is not None:
        path = interview_course_path(canonical)
        coverage = FirstPassCoverage(
            covered=interview_card.coverage.covered,
            total=interview_card.coverage.total,
            percent=interview_card.coverage.percent,
            summary=interview_card.coverage.summary,
        )
        weak_ids_raw = canonical.get("evidence")
        weak_ids = (
            {value for value in weak_ids_raw.get("weak", []) if isinstance(value, str)}
            if isinstance(weak_ids_raw, dict) and isinstance(weak_ids_raw.get("weak"), list)
            else set()
        )
        weak_areas = tuple(item.title for item in path if item.identity in weak_ids)
        review = ActionableReview(
            due=interview_card.readiness.due,
            next_retrieval=interview_card.readiness.next_retrieval,
            kind=("canonical" if interview_card.readiness.due else None),
        )
        readiness_summary = interview_card.readiness.summary
        blocker = None
    else:
        path = _generic_path(metadata)
        covered = sum(item.status == "covered" for item in path)
        coverage = FirstPassCoverage(
            covered=covered,
            total=len(path),
            percent=round(covered / len(path) * 100) if path else 0,
            summary=(
                f"{covered} of {len(path)} course topics covered once."
                if path
                else "The course path has not been built yet."
            ),
        )
        weak_raw = metadata.get("weak_spots")
        weak_areas = (
            tuple(value.strip() for value in weak_raw if isinstance(value, str) and value.strip())
            if isinstance(weak_raw, list)
            else ()
        )
        review = ActionableReview(
            due=progress.reviews.due_today,
            kind=("scheduled" if progress.reviews.due_today else None),
        )
        readiness_summary = (
            f"{review.due} review item{'s' if review.due != 1 else ''} due."
            if review.actionable
            else f"{len(weak_areas)} area{'s' if len(weak_areas) != 1 else ''} need reinforcement."
            if weak_areas
            else coverage.summary
        )
        blocker = (
            CourseContinuationBlocker(
                code="placement",
                message="Choose a starting route before the first lesson.",
                action="Open placement",
            )
            if is_interview_course
            else CourseContinuationBlocker(
                code="course-plan",
                message="Build this course's learning path before the first lesson.",
                action="Build course path",
            )
            if not path
            else None
        )
    current = next((item for item in path if item.status == "current"), None)
    current_index = path.index(current) if current is not None else len(path)
    upcoming = tuple(item for item in path[current_index + 1 :] if item.status == "upcoming")[:5]
    first_pass_complete = bool(path) and coverage.covered == coverage.total
    recommendation = (
        recommend_follow_up_template(template_id, weak_areas=weak_areas)
        if first_pass_complete
        else None
    )
    if first_pass_complete and recommendation is None:
        recommendation = CourseRecommendation(
            kind="generated-proposal",
            title="Create a focused follow-up",
            goal="Build a more advanced course around your next learning priority.",
        )
    return (
        CourseLibraryProjection(
            path=path,
            current=current,
            upcoming=upcoming,
            coverage=coverage,
            review=review,
            weak_areas=weak_areas,
            first_pass_complete=first_pass_complete,
            readiness_summary=readiness_summary,
            recommendation=recommendation,
            blocker=blocker,
        ),
        interview_card,
    )


def course_snapshot(slug: str, *, today: date | None = None) -> CourseSnapshot:
    cli = _cli()
    canonical = cli.slugify(slug)
    if canonical != slug:
        raise cli.OpenLearnError(f"invalid topic slug: {slug}")
    recover_course_settings(canonical)
    is_interview_course = cli.interview_profile_path(canonical).exists()
    interview_source = (
        interview_learning_source(canonical, include_body=False) if is_interview_course else None
    )
    if interview_source is None:
        topic = cli.read_topic_stats(canonical)
        metadata = topic.metadata
        state = cli.load_state(canonical)
        topic_path = topic.path
    else:
        source_metadata = cast(dict[str, object], interview_source["metadata"])
        state = cast(dict[str, object], interview_source["state"])
        metadata = cli.merge_topic_state(
            cli.normalize_topic_metadata(source_metadata, canonical), state
        )
        topic_path = cli.topic_path(canonical)
    modified = datetime.fromtimestamp(topic_path.stat().st_mtime, timezone.utc).isoformat()
    progress = _course_progress(metadata, today or date.today())
    template_id = (
        str(metadata["template_id"]) if isinstance(metadata.get("template_id"), str) else None
    )
    library, interview_card = _course_library_projection(
        metadata,
        state,
        progress=progress,
        template_id=template_id,
        is_interview_course=is_interview_course,
    )
    card = CourseCard(
        slug=canonical,
        title=str(metadata.get("topic") or canonical.replace("-", " ").title()),
        goal=str(metadata.get("goal") or ""),
        current_focus=str(metadata.get("current_focus") or ""),
        started=metadata.get("course_started") is True,
        completed=metadata.get("course_completed") is True,
        updated_at=modified,
        progress=progress,
        library=library,
        template_id=template_id,
        interview=interview_card,
    )
    return CourseSnapshot(
        card=card,
        calibration=_calibration_from_state(state),
        mastery_profile=str(metadata.get("mastery_profile") or "proficient"),
        model=str(metadata.get("model") or ""),
        created_at=str(metadata.get("created") or ""),
    )


def _metadata_without_transcript(path: Path) -> dict[str, object]:
    """Read only JSON frontmatter while the caller holds the topic-store locks."""
    with path.open("r", encoding="utf-8") as file:
        if file.readline() != "---\n":
            return {}
        metadata_lines: list[str] = []
        for line in file:
            if line == "---\n":
                break
            metadata_lines.append(line)
        else:
            raise _cli().OpenLearnError(
                f"invalid topic metadata: missing closing delimiter in {path}"
            )
    try:
        metadata = json.loads("".join(metadata_lines))
    except json.JSONDecodeError as exc:
        raise _cli().OpenLearnError(f"invalid topic metadata: {exc}") from exc
    if not isinstance(metadata, dict):
        raise _cli().OpenLearnError(f"invalid topic metadata: expected object in {path}")
    return metadata


def _recovery_fenced_course_source(
    slug: str, *, include_body: bool, require_interview: bool = False
) -> dict[str, object] | None:
    """Snapshot topic state and selected Markdown data behind both journals."""
    cli = _cli()
    recover_course_settings(slug)
    route_journal = cli.interview_route_journal_path(slug)
    turn_journal = cli.topic_turn_journal_path(slug)
    while True:
        recover_interview_route_acceptance(slug)
        cli.recover_turn_commit(slug)
        # Hold both journal creation locks through the store snapshot. Writers
        # use journal-before-topic order, so no new recovery authority can
        # appear between the recovery checks and these reads.
        with (
            cli.file_lock(route_journal),
            cli.file_lock(turn_journal),
            cli.topic_store_locks(slug),
        ):
            if route_journal.exists() or turn_journal.exists():
                continue
            cli.raise_if_topic_tombstoned(slug)
            state = cli._load_state_unlocked(slug)
            if require_interview and not isinstance(state.get("interview_curriculum"), dict):
                return None
            path = cli.topic_path(slug)
            if include_body:
                metadata, body = cli.parse_topic(path.read_text(encoding="utf-8"))
            else:
                metadata = _metadata_without_transcript(path)
                body = ""
            return {
                "slug": slug,
                "metadata": copy.deepcopy(metadata),
                "body": body,
                "state": copy.deepcopy(state),
            }


def interview_learning_source(slug: str, *, include_body: bool = True) -> dict[str, object] | None:
    """Read one recovery-fenced interview lesson generation for presentation."""
    cli = _cli()
    canonical_slug = cli.slugify(slug)
    if canonical_slug != slug:
        raise cli.OpenLearnError(f"invalid topic slug: {slug}")
    source = _recovery_fenced_course_source(slug, include_body=include_body, require_interview=True)
    if source is None:
        return None
    state = cast(dict[str, object], source["state"])
    assert isinstance(state.get("interview_curriculum"), dict)
    return source


def course_conversation_source(slug: str) -> dict[str, object]:
    """Read a transcript and both of its revision namespaces atomically."""
    cli = _cli()
    canonical_slug = cli.slugify(slug)
    if canonical_slug != slug:
        raise cli.OpenLearnError(f"invalid topic slug: {slug}")
    source = _recovery_fenced_course_source(slug, include_body=True)
    assert source is not None
    state = cast(dict[str, object], source["state"])
    internal = state.get("_openlearn_internal")
    internal = internal if isinstance(internal, dict) else {}

    def revision(key: str) -> int:
        value = internal.get(key, 0)
        return value if isinstance(value, int) and value >= 0 else 0

    return {
        "slug": slug,
        "body": source["body"],
        "course_revision": revision("course_revision"),
        "side_chat_revision": revision("side_chat_revision"),
    }


def list_course_snapshots(*, today: date | None = None) -> tuple[CourseSnapshot, ...]:
    cli = _cli()
    if not cli.topics_dir().exists():
        return ()
    paths = cli.recent_topic_paths()
    return tuple(course_snapshot(path.stem, today=today) for path in paths)


def dashboard_snapshot(
    *, now: datetime | None = None, selected_slug: str | None = None
) -> DashboardSnapshot:
    """Read dashboard data without activating a course or recording activity."""
    cli = _cli()
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    snapshots = list_course_snapshots(today=moment.date())
    cards = tuple(item.card for item in snapshots)
    active_slug = cli.get_active_topic()
    active = next((card for card in cards if card.slug == active_slug and card.incomplete), None)
    resume = active or next((card for card in cards if card.incomplete), None)
    if resume is None and cards:
        resume = cards[0]
    selected = next((card for card in cards if card.slug == selected_slug), None)
    selected = selected or active or resume or (cards[0] if cards else None)
    reviews = ReviewProgress(
        due_today=sum(card.progress.reviews.due_today for card in cards),
        due_this_week=sum(card.progress.reviews.due_this_week for card in cards),
        due_later=sum(card.progress.reviews.due_later for card in cards),
    )
    return DashboardSnapshot(
        courses=cards,
        resume=resume,
        selected=selected,
        selected_slug=selected.slug if selected is not None else None,
        active_slug=active_slug,
        reviews=reviews,
        generated_at=moment.astimezone(timezone.utc).isoformat(),
    )


def _reconciliation_checkpoint(_stage: str) -> None:
    """Fault-injection seam for the curriculum reconciliation publication stages."""


def _route_acceptance_checkpoint(_stage: str) -> None:
    """Fault-injection seam for route acceptance publication stages."""


def _event_text_has_id(existing: str, event_id: str) -> bool:
    for line in existing.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("event_id") == event_id:
            return True
    return False


def _append_event_once(
    path: Path, event_id: str, event: dict[str, object]
) -> None:
    cli = _cli()
    try:
        existing = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    if _event_text_has_id(existing, event_id):
        return
    if existing and not existing.endswith("\n"):
        existing += "\n"
    cli.write_text_atomic(path, existing + json.dumps(event, sort_keys=True) + "\n")


def _route_journal_identity(value: dict[str, object]) -> dict[str, object]:
    return {key: value[key] for key in value if key != "journal_sha256"}


def _validated_route_acceptance_journal(slug: str, value: object) -> dict[str, object]:
    required = {
        "schema_version",
        "slug",
        "topic_generation",
        "profile_before_fingerprint",
        "profile_after",
        "canonical_after",
        "metadata_projection",
        "receipt",
        "event",
        "journal_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise _cli().OpenLearnError("saved interview curriculum acceptance is malformed")
    receipt = value.get("receipt")
    event = value.get("event")
    if (
        value.get("schema_version") != ROUTE_ACCEPTANCE_SCHEMA_VERSION
        or value.get("slug") != slug
        or not isinstance(value.get("topic_generation"), str)
        or not isinstance(value.get("profile_before_fingerprint"), str)
        or not isinstance(value.get("profile_after"), dict)
        or not isinstance(value.get("canonical_after"), dict)
        or not isinstance(value.get("metadata_projection"), dict)
        or not isinstance(receipt, dict)
        or not isinstance(event, dict)
    ):
        raise _cli().OpenLearnError("saved interview curriculum acceptance is malformed")
    action_id = receipt.get("action_id")
    if (
        receipt.get("schema_version") != ROUTE_ACCEPTANCE_SCHEMA_VERSION
        or not isinstance(action_id, str)
        or not action_id.startswith("route_")
        or receipt.get("topic_generation") != value["topic_generation"]
        or event.get("event_id") != f"{action_id}:0"
        or event.get("slug") != slug
        or event.get("data") != receipt
        or value.get("journal_sha256")
        != interview_curriculum.canonical_fingerprint(_route_journal_identity(value))
    ):
        raise _cli().OpenLearnError("saved interview curriculum acceptance has invalid identity")
    return copy.deepcopy(value)


def _apply_route_acceptance_journal(slug: str, journal: dict[str, object]) -> None:
    cli = _cli()
    journal = _validated_route_acceptance_journal(slug, journal)
    generation = journal["topic_generation"]
    profile_after = journal.get("profile_after")
    canonical_after = journal.get("canonical_after")
    projection = journal.get("metadata_projection")
    receipt = journal.get("receipt")
    event = journal.get("event")
    if not all(
        isinstance(value, dict)
        for value in (profile_after, canonical_after, projection, receipt, event)
    ):
        raise cli.OpenLearnError("saved interview curriculum acceptance is malformed")
    profile_path = cli.interview_profile_path(slug)
    with cli.topic_store_locks(slug), cli.file_lock(profile_path):
        if generation != cli.current_topic_generation(slug):
            raise RouteAcceptanceConflictError(
                "topic changed during interview curriculum acceptance"
            )
        current_profile = interview_prep.load_profile(profile_path)
        current_profile_fingerprint = interview_curriculum.canonical_fingerprint(current_profile)
        before_fingerprint = journal.get("profile_before_fingerprint")
        after_fingerprint = interview_curriculum.canonical_fingerprint(profile_after)
        if current_profile_fingerprint not in {before_fingerprint, after_fingerprint}:
            raise RouteAcceptanceConflictError(
                "interview profile changed while course outline confirmation was pending"
            )
        state = cli._load_state_unlocked(slug)
        internal_raw = state.get("_openlearn_internal")
        internal = copy.deepcopy(internal_raw) if isinstance(internal_raw, dict) else {}
        revision = internal.get("course_revision", 0)
        base_revision = receipt.get("base_revision")
        final_revision = receipt.get("final_revision")
        if revision not in {base_revision, final_revision}:
            raise RouteAcceptanceConflictError(
                "course changed while course outline confirmation was pending"
            )
        active = internal.get("active_turn")
        if isinstance(active, dict) and revision != final_revision:
            raise RouteAcceptanceConflictError(
                "finish or cancel the active tutor operation before changing the course outline"
            )
        receipts_raw = state.get("_interview_route_receipts")
        receipts = copy.deepcopy(receipts_raw) if isinstance(receipts_raw, dict) else {}
        action_id = str(receipt["action_id"])
        existing = receipts.get(action_id)
        if revision == final_revision and (
            existing != receipt or state.get("interview_curriculum") != canonical_after
        ):
            raise RouteAcceptanceConflictError(
                "course changed while course outline confirmation was pending"
            )
        topic_text = cli.topic_path(slug).read_text(encoding="utf-8")
        metadata, body = cli.parse_topic(topic_text)

        # All optimistic-concurrency fences are checked before the first write.
        if current_profile_fingerprint != after_fingerprint:
            interview_prep._write(profile_path, profile_after)
        _route_acceptance_checkpoint("after_profile")
        if existing is not None and existing != receipt:
            raise RouteAcceptanceConflictError("interview curriculum acceptance receipt conflicts")
        receipts[action_id] = copy.deepcopy(receipt)
        state["_interview_route_receipts"] = receipts
        state["interview_curriculum"] = copy.deepcopy(canonical_after)
        retired_check = receipt.get("retired_check")
        if isinstance(retired_check, dict):
            pending = state.get("pending_question")
            pending_ref = pending.get("skill_ref") if isinstance(pending, dict) else None
            retired_ref = retired_check.get("skill_ref")
            if pending_ref == retired_ref or not isinstance(pending_ref, dict):
                state.pop("pending_question", None)
            retired_raw = state.get("_interview_retired_checks")
            retired = list(retired_raw) if isinstance(retired_raw, list) else []
            if not any(
                isinstance(item, dict) and item.get("action_id") == retired_check.get("action_id")
                for item in retired
            ):
                retired.append(copy.deepcopy(retired_check))
            state["_interview_retired_checks"] = retired[-20:]
        internal["schema_version"] = 1
        internal["course_revision"] = final_revision
        internal.setdefault("turn_results", {})
        state["_openlearn_internal"] = internal
        cli.write_text_atomic(
            cli.topic_state_path(slug),
            json.dumps(state, indent=2, sort_keys=True) + "\n",
        )
        _route_acceptance_checkpoint("after_state")

        metadata = dict(metadata)
        metadata.update(copy.deepcopy(projection))
        if isinstance(retired_check, dict):
            pending = metadata.get("pending_question")
            pending_ref = pending.get("skill_ref") if isinstance(pending, dict) else None
            if pending_ref == retired_check.get("skill_ref") or not isinstance(pending_ref, dict):
                metadata.pop("pending_question", None)
        metadata["course_started"] = True
        metadata["course_completed"] = False
        cli.write_text_atomic(cli.topic_path(slug), cli.format_topic(metadata, body))
        _route_acceptance_checkpoint("after_topic")

        events_path = cli.topic_events_path(slug)
        event_id = str(event["event_id"])
        _append_event_once(events_path, event_id, event)
        _route_acceptance_checkpoint("after_event")


def recover_interview_route_acceptance(slug: str) -> bool:
    """Finish one validated pending route transaction before interview state is used."""
    cli = _cli()
    journal_path = cli.interview_route_journal_path(slug)
    with cli.file_lock(journal_path):
        if not journal_path.exists():
            return False
        try:
            raw = json.loads(journal_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise cli.OpenLearnError("saved interview curriculum acceptance is unreadable") from exc
        journal = _validated_route_acceptance_journal(slug, raw)
        try:
            _apply_route_acceptance_journal(slug, journal)
        except RouteAcceptanceConflictError:
            cli.durable_unlink(journal_path)
            raise
        cli.durable_unlink(journal_path)
        return True


def accept_interview_curriculum(
    slug: str,
    *,
    action: str,
    changes: dict[str, object] | None = None,
    outline: str = "",
    submission_id: str | None = None,
    expected_revision: int | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Confirm, skip, or change one route through a recoverable shared transaction."""
    cli = _cli()
    if action not in {"confirm", "skip", "change"}:
        raise ValueError("interview curriculum acceptance action is invalid")
    moment = now or datetime.now(timezone.utc)
    canonical_slug = cli.slugify(slug)
    if canonical_slug != slug or not cli.interview_profile_path(slug).exists():
        raise cli.OpenLearnError(f"interview course not found: {slug}")
    payload_hash = interview_curriculum.canonical_fingerprint(
        {"action": action, "changes": changes or {}, "outline": outline.strip()}
    )
    if submission_id is not None:
        try:
            parsed = UUID(submission_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError("submission ID must be a canonical UUID") from exc
        if str(parsed) != submission_id:
            raise ValueError("submission ID must be a canonical UUID")
        action_id = f"route_{parsed.hex}"
    else:
        action_id = ""
    journal_path = cli.interview_route_journal_path(slug)
    with cli.file_lock(journal_path):
        if journal_path.exists():
            try:
                pending = json.loads(journal_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise cli.OpenLearnError(
                    "saved interview curriculum acceptance is unreadable"
                ) from exc
            if not isinstance(pending, dict):
                raise cli.OpenLearnError("saved interview curriculum acceptance is malformed")
            _apply_route_acceptance_journal(
                slug, _validated_route_acceptance_journal(slug, pending)
            )
            cli.durable_unlink(journal_path)

        with cli.topic_store_locks(slug), cli.file_lock(cli.interview_profile_path(slug)):
            cli.raise_if_topic_tombstoned(slug)
            state = cli._load_state_unlocked(slug)
            receipts_raw = state.get("_interview_route_receipts")
            receipts = receipts_raw if isinstance(receipts_raw, dict) else {}
            prior = receipts.get(action_id)
            if isinstance(prior, dict):
                if prior.get("payload_hash") != payload_hash:
                    raise RouteAcceptanceConflictError(
                        "submission ID was already used for another course outline change"
                    )
                canonical = state.get("interview_curriculum")
                if not isinstance(canonical, dict):
                    raise cli.OpenLearnError("saved curriculum acceptance lost its route")
                return {
                    "profile": interview_prep.load_profile(cli.interview_profile_path(slug)),
                    "canonical": canonical,
                    "receipt": prior,
                    "replayed": True,
                }
            internal_raw = state.get("_openlearn_internal")
            internal = internal_raw if isinstance(internal_raw, dict) else {}
            revision = internal.get("course_revision", 0)
            if not isinstance(revision, int) or revision < 0:
                revision = 0
            if submission_id is None:
                current_profile = interview_prep.load_profile(cli.interview_profile_path(slug))
                current_profile_fingerprint = interview_curriculum.canonical_fingerprint(
                    current_profile
                )
                prior = next(
                    (
                        value
                        for value in receipts.values()
                        if isinstance(value, dict)
                        and value.get("submission_mode") == "legacy"
                        and value.get("payload_hash") == payload_hash
                        and value.get("final_revision") == revision
                        and value.get("profile_fingerprint") == current_profile_fingerprint
                    ),
                    None,
                )
                if isinstance(prior, dict):
                    canonical = state.get("interview_curriculum")
                    if not isinstance(canonical, dict):
                        raise cli.OpenLearnError("saved curriculum acceptance lost its route")
                    return {
                        "profile": interview_prep.load_profile(cli.interview_profile_path(slug)),
                        "canonical": canonical,
                        "receipt": prior,
                        "replayed": True,
                    }
                legacy_identity = interview_curriculum.canonical_fingerprint(
                    {
                        "slug": slug,
                        "topic_generation": cli.current_topic_generation(slug),
                        "base_revision": revision,
                        "payload_hash": payload_hash,
                    }
                )
                action_id = f"route_legacy_{legacy_identity[:32]}"
            if expected_revision is not None and expected_revision != revision:
                raise RouteAcceptanceConflictError(
                    "course changed elsewhere; reload before confirming"
                )
            if isinstance(internal.get("active_turn"), dict):
                raise RouteAcceptanceConflictError(
                    "finish or cancel the active tutor operation before changing the course outline"
                )
            profile_before = interview_prep.load_profile(cli.interview_profile_path(slug))
            existing_canonical = state.get("interview_curriculum")
            bundle = (
                interview_curriculum.load_pinned_bundle(
                    str(existing_canonical["bundle_id"]),
                    str(existing_canonical["bundle_version"]),
                )
                if isinstance(existing_canonical, dict)
                else interview_curriculum.load_default_bundle()
            )
            profile_after, route = interview_prep.accepted_curriculum_profile(
                profile_before,
                action=action,
                changes=changes,
                outline=outline,
                now=moment,
                bundle=bundle,
            )
            if isinstance(existing_canonical, dict):
                old_check = existing_canonical.get("committed_check_target")
                canonical_after, cursor_decision = (
                    interview_curriculum.rematerialize_canonical_state(
                        existing_canonical, route, change_id=action_id
                    )
                )
                old_route_fingerprint = existing_canonical.get("route_fingerprint")
                old_cursor = copy.deepcopy(existing_canonical.get("cursor"))
            else:
                old_check = None
                canonical_after = interview_curriculum.canonical_state_from_route(
                    route, acceptance_id=action_id
                )
                cursor_decision = "first-technical-target"
                old_route_fingerprint = None
                old_cursor = None
            projection = interview_curriculum.compatibility_projection(canonical_after)
            final_revision = revision + 1
            receipt = {
                "schema_version": ROUTE_ACCEPTANCE_SCHEMA_VERSION,
                "action_id": action_id,
                "action": action,
                "submission_mode": ("explicit" if submission_id is not None else "legacy"),
                "payload_hash": payload_hash,
                "topic_generation": cli.current_topic_generation(slug),
                "base_revision": revision,
                "final_revision": final_revision,
                "old_route_fingerprint": old_route_fingerprint,
                "new_route_fingerprint": route["route_fingerprint"],
                "old_cursor": old_cursor,
                "new_cursor": copy.deepcopy(canonical_after["cursor"]),
                "cursor_decision": cursor_decision,
                "profile_fingerprint": interview_curriculum.canonical_fingerprint(profile_after),
                "created_at": moment.astimezone(timezone.utc).isoformat(),
            }
            if (
                isinstance(old_check, dict)
                and canonical_after.get("committed_check_target") != old_check
            ):
                receipt["retired_check"] = {
                    "action_id": action_id,
                    "retired_at": moment.astimezone(timezone.utc).isoformat(),
                    "reason": "target_removed_by_route_change",
                    "skill_ref": copy.deepcopy(old_check.get("skill_ref")),
                }
            event = {
                "schema_version": cli.EVENT_SCHEMA_VERSION,
                "event_id": f"{action_id}:0",
                "ts": moment.astimezone(timezone.utc).isoformat(),
                "event_type": "interview_curriculum_route_accepted",
                "slug": slug,
                "data": copy.deepcopy(receipt),
            }
            journal = {
                "schema_version": ROUTE_ACCEPTANCE_SCHEMA_VERSION,
                "slug": slug,
                "topic_generation": cli.current_topic_generation(slug),
                "profile_before_fingerprint": interview_curriculum.canonical_fingerprint(
                    profile_before
                ),
                "profile_after": profile_after,
                "canonical_after": canonical_after,
                "metadata_projection": projection,
                "receipt": receipt,
                "event": event,
            }
            journal["journal_sha256"] = interview_curriculum.canonical_fingerprint(
                _route_journal_identity(journal)
            )
            _validated_route_acceptance_journal(slug, journal)
            cli.write_text_atomic(
                journal_path, json.dumps(journal, indent=2, sort_keys=True) + "\n"
            )
            journal_path.chmod(0o600)
            _route_acceptance_checkpoint("after_journal")
        try:
            _apply_route_acceptance_journal(slug, journal)
        except RouteAcceptanceConflictError:
            cli.durable_unlink(journal_path)
            raise
        cli.durable_unlink(journal_path)
        return {
            "profile": profile_after,
            "canonical": canonical_after,
            "receipt": receipt,
            "replayed": False,
        }


def _route_for_profile(profile_value: dict[str, object]) -> dict[str, object]:
    allocation = profile_value.get("curriculum_allocation")
    if isinstance(allocation, dict) and isinstance(allocation.get("route"), dict):
        return copy.deepcopy(cast(dict[str, object], allocation["route"]))
    profile = profile_value.get("profile")
    placement = profile_value.get("placement")
    if not isinstance(profile, dict):
        raise ValueError("interview-prep profile is missing")
    survey = placement.get("survey") if isinstance(placement, dict) else None
    survey = survey if isinstance(survey, dict) else {}
    ratings = survey.get("ratings")
    ratings = ratings if isinstance(ratings, dict) else {}
    route = interview_curriculum.materialize_adaptive_route(
        interview_curriculum.load_default_bundle(),
        role_family=str(survey.get("role_family") or profile.get("role_family") or "general SWE"),
        target_level=str(survey.get("target_level") or profile.get("target_level") or "entry"),
        interview_focus=str(survey.get("interview_focus") or "coding"),
        interview_date=str(profile.get("interview_date") or ""),
        weekly_minutes=int(cast(int, profile.get("weekly_minutes") or 120)),
        session_minutes=int(cast(int, profile.get("session_minutes") or 45)),
        confidence_ratings={str(key): int(value) for key, value in ratings.items()},
        pacing_posture_override=None,
        current_date=date.today(),
    )
    return route.to_dict()


def _load_authoritative_state_unlocked(slug: str) -> dict[str, object]:
    """Read reconciliation input without the display layer's corrupt-as-empty fallback."""
    cli = _cli()
    path = cli.topic_state_path(slug)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise cli.OpenLearnError(
            "interview curriculum source state is unreadable; repair it before continuing"
        ) from exc
    if not isinstance(value, dict):
        raise cli.OpenLearnError(
            "interview curriculum source state is malformed; repair it before continuing"
        )
    return value


def _load_authoritative_events(path: Path) -> list[dict[str, object]]:
    """Read the complete append-only authority or fail without publishing."""
    cli = _cli()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise cli.OpenLearnError(
            "interview curriculum source events are unreadable; repair them before continuing"
        ) from exc
    events: list[dict[str, object]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise cli.OpenLearnError(
                "interview curriculum source events are malformed at "
                f"line {line_number}; repair them before continuing"
            ) from exc
        if not isinstance(value, dict):
            raise cli.OpenLearnError(
                "interview curriculum source events are malformed at "
                f"line {line_number}; repair them before continuing"
            )
        events.append(value)
    return events


_RECONCILIATION_METADATA_KEYS = (
    "template_id",
    "template_units",
    "course_units",
    "current_unit",
    "current_slide",
    "current_focus",
    "known",
    "weak_spots",
    "review_due",
    "placement_result",
    "slide_coverage",
)
_RECONCILIATION_STATE_KEYS = (
    "concept_attempts",
    "review_history",
    "assessment_history",
)


def _reconciliation_source_payload(
    slug: str,
    *,
    profile_value: dict[str, object],
    journal: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object], str]:
    """Capture mutable legacy authorities while normalizing our own partial writes."""
    cli = _cli()
    topic_text = cli.topic_path(slug).read_text(encoding="utf-8")
    raw_metadata, body = cli.parse_topic(topic_text)
    state = _load_authoritative_state_unlocked(slug)
    metadata = cli.merge_topic_state(cli.normalize_topic_metadata(raw_metadata, slug), state)
    events = _load_authoritative_events(cli.topic_events_path(slug))
    if journal is not None:
        source_before = journal.get("source_payload")
        projections = [
            value
            for value in (
                journal.get("metadata_projection"),
                journal.get("superseded_projection"),
            )
            if isinstance(value, dict)
        ]
        source_metadata = source_before.get("metadata") if isinstance(source_before, dict) else None
        if projections and isinstance(source_metadata, dict):
            metadata = dict(metadata)
            for key in ("course_units", "current_unit", "current_slide", "current_focus"):
                if any(raw_metadata.get(key) == projection.get(key) for projection in projections):
                    if key in source_metadata:
                        metadata[key] = copy.deepcopy(source_metadata[key])
                    else:
                        metadata.pop(key, None)
        reconciliation_ids = {
            value
            for value in (
                journal.get("reconciliation_id"),
                journal.get("superseded_reconciliation_id"),
            )
            if isinstance(value, str)
        }
        events = [
            event
            for event in events
            if not (
                isinstance(event.get("data"), dict)
                and event["data"].get("reconciliation_id") in reconciliation_ids
            )
        ]
    payload = {
        "topic_generation": cli.topic_generation_from_metadata(slug, raw_metadata),
        "metadata": {key: metadata.get(key) for key in _RECONCILIATION_METADATA_KEYS},
        "state": {key: state.get(key) for key in _RECONCILIATION_STATE_KEYS},
        "transcript_fingerprint": interview_curriculum.canonical_fingerprint(body),
        "profile_context": {
            "profile": profile_value.get("profile"),
            "placement": profile_value.get("placement"),
        },
        "events": events,
        "allocation": profile_value.get("curriculum_allocation"),
    }
    return payload, raw_metadata, state, body


def _event_has_reconciliation(events_path: Path, reconciliation_id: str) -> bool:
    for event in _load_authoritative_events(events_path):
        data = event.get("data") if isinstance(event, dict) else None
        if isinstance(data, dict) and data.get("reconciliation_id") == reconciliation_id:
            return True
    return False


def _reconciliation_journal_identity(value: dict[str, object]) -> dict[str, object]:
    return {key: item for key, item in value.items() if key != "journal_sha256"}


def _migrated_legacy_reconciliation_journal(
    slug: str, value: dict[str, object]
) -> dict[str, object]:
    required = {
        "schema_version",
        "slug",
        "topic_generation",
        "reconciliation_id",
        "source_fingerprint",
        "source_payload",
        "canonical_state",
        "metadata_projection",
        "event",
        "receipt",
    }
    optional = {"superseded_projection", "superseded_reconciliation_id"}
    if (
        value.get("schema_version") != LEGACY_RECONCILIATION_SCHEMA_VERSION
        or not required.issubset(value)
        or not set(value).issubset(required | optional)
        or (("superseded_projection" in value) != ("superseded_reconciliation_id" in value))
    ):
        raise _cli().OpenLearnError("interview curriculum reconciliation journal is malformed")
    source = value.get("source_payload")
    canonical = value.get("canonical_state")
    projection = value.get("metadata_projection")
    event = value.get("event")
    receipt = value.get("receipt")
    reconciliation_id = value.get("reconciliation_id")
    source_fingerprint = value.get("source_fingerprint")
    topic_generation = value.get("topic_generation")
    event_data = event.get("data") if isinstance(event, dict) else None
    if (
        value.get("slug") != slug
        or not all(
            isinstance(item, dict) for item in (source, canonical, projection, event, receipt)
        )
        or not isinstance(reconciliation_id, str)
        or not reconciliation_id.startswith("reconcile_")
        or not isinstance(source_fingerprint, str)
        or interview_curriculum.canonical_fingerprint(source) != source_fingerprint
        or not isinstance(topic_generation, str)
        or event.get("event_type") != "interview_curriculum_reconciled"
        or event.get("schema_version") != _cli().EVENT_SCHEMA_VERSION
        or event.get("slug") != slug
        or not isinstance(event_data, dict)
        or event_data.get("reconciliation_id") != reconciliation_id
        or event_data.get("source_fingerprint") != source_fingerprint
        or projection != interview_curriculum.compatibility_projection(canonical)
    ):
        raise _cli().OpenLearnError(
            "interview curriculum reconciliation journal has invalid identity"
        )
    migrated_receipt = _validated_reconciliation_receipt(slug, receipt)
    if (
        migrated_receipt.get("topic_generation") != topic_generation
        or migrated_receipt.get("reconciliation_id") != reconciliation_id
        or migrated_receipt.get("source_fingerprint") != source_fingerprint
        or migrated_receipt.get("canonical_state") != canonical
        or event_data.get("pinned_destination") != migrated_receipt.get("pinned_destination")
        or event_data.get("to_version") != migrated_receipt.get("to_version")
    ):
        raise _cli().OpenLearnError(
            "interview curriculum reconciliation journal has invalid identity"
        )
    migrated = copy.deepcopy(value)
    migrated["schema_version"] = RECONCILIATION_SCHEMA_VERSION
    migrated["receipt"] = migrated_receipt
    migrated["journal_sha256"] = interview_curriculum.canonical_fingerprint(
        _reconciliation_journal_identity(migrated)
    )
    return migrated


def _validated_reconciliation_journal(slug: str, value: object) -> dict[str, object]:
    if (
        isinstance(value, dict)
        and value.get("schema_version") == LEGACY_RECONCILIATION_SCHEMA_VERSION
        and "journal_sha256" not in value
    ):
        value = _migrated_legacy_reconciliation_journal(slug, value)
    required = {
        "schema_version",
        "slug",
        "topic_generation",
        "reconciliation_id",
        "source_fingerprint",
        "source_payload",
        "canonical_state",
        "metadata_projection",
        "event",
        "receipt",
        "journal_sha256",
    }
    optional = {"superseded_projection", "superseded_reconciliation_id"}
    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or not set(value).issubset(required | optional)
        or (("superseded_projection" in value) != ("superseded_reconciliation_id" in value))
    ):
        raise _cli().OpenLearnError("interview curriculum reconciliation journal is malformed")
    source = value.get("source_payload")
    canonical = value.get("canonical_state")
    projection = value.get("metadata_projection")
    event = value.get("event")
    receipt = value.get("receipt")
    reconciliation_id = value.get("reconciliation_id")
    source_fingerprint = value.get("source_fingerprint")
    topic_generation = value.get("topic_generation")
    event_data = event.get("data") if isinstance(event, dict) else None
    canonical_reconciliation = (
        canonical.get("reconciliation") if isinstance(canonical, dict) else None
    )
    canonical_route = canonical.get("route") if isinstance(canonical, dict) else None
    unsigned_receipt = dict(receipt) if isinstance(receipt, dict) else {}
    receipt_hash = unsigned_receipt.pop("receipt_sha256", None)
    if (
        value.get("schema_version") != RECONCILIATION_SCHEMA_VERSION
        or value.get("slug") != slug
        or not all(
            isinstance(item, dict) for item in (source, canonical, projection, event, receipt)
        )
        or not isinstance(reconciliation_id, str)
        or not reconciliation_id.startswith("reconcile_")
        or not isinstance(source_fingerprint, str)
        or interview_curriculum.canonical_fingerprint(source) != source_fingerprint
        or not isinstance(topic_generation, str)
        or receipt.get("schema_version") != RECONCILIATION_SCHEMA_VERSION
        or receipt.get("slug") != slug
        or receipt.get("topic_generation") != topic_generation
        or receipt.get("reconciliation_id") != reconciliation_id
        or receipt.get("source_fingerprint") != source_fingerprint
        or receipt.get("canonical_state") != canonical
        or not isinstance(receipt_hash, str)
        or receipt_hash != interview_curriculum.canonical_fingerprint(unsigned_receipt)
        or event.get("event_type") != "interview_curriculum_reconciled"
        or event.get("schema_version") != _cli().EVENT_SCHEMA_VERSION
        or event.get("slug") != slug
        or not isinstance(event_data, dict)
        or event_data.get("reconciliation_id") != reconciliation_id
        or event_data.get("source_fingerprint") != source_fingerprint
        or event_data.get("pinned_destination") != receipt.get("pinned_destination")
        or event_data.get("to_version") != receipt.get("to_version")
        or not isinstance(receipt.get("pinned_destination"), dict)
        or receipt["pinned_destination"].get("bundle_id") != canonical.get("bundle_id")
        or receipt["pinned_destination"].get("bundle_version") != canonical.get("bundle_version")
        or receipt["pinned_destination"].get("route_id")
        != (canonical_route.get("route_id") if isinstance(canonical_route, dict) else None)
        or not isinstance(canonical_reconciliation, dict)
        or canonical_reconciliation.get("reconciliation_id") != reconciliation_id
        or canonical_reconciliation.get("source_fingerprint") != source_fingerprint
        or projection != interview_curriculum.compatibility_projection(canonical)
        or value.get("journal_sha256")
        != interview_curriculum.canonical_fingerprint(_reconciliation_journal_identity(value))
    ):
        raise _cli().OpenLearnError(
            "interview curriculum reconciliation journal has invalid identity"
        )
    return copy.deepcopy(value)


def _validated_reconciliation_receipt(slug: str, value: object) -> dict[str, object]:
    legacy_required = {
        "schema_version",
        "slug",
        "topic_generation",
        "reconciliation_id",
        "source_fingerprint",
        "from_version",
        "to_version",
        "aliases_applied",
        "unmatched_references",
        "pinned_destination",
        "canonical_state",
    }
    required = {
        *legacy_required,
        "receipt_sha256",
    }
    if not isinstance(value, dict):
        raise _cli().OpenLearnError("interview curriculum reconciliation receipt is malformed")
    is_legacy = (
        value.get("schema_version") == LEGACY_RECONCILIATION_SCHEMA_VERSION
        and set(value) == legacy_required
    )
    if not is_legacy and set(value) != required:
        raise _cli().OpenLearnError("interview curriculum reconciliation receipt is malformed")
    unsigned = dict(value)
    digest = unsigned.pop("receipt_sha256", None)
    canonical = value.get("canonical_state")
    reconciliation = canonical.get("reconciliation") if isinstance(canonical, dict) else None
    route = canonical.get("route") if isinstance(canonical, dict) else None
    legacy_context = canonical.get("legacy_context") if isinstance(canonical, dict) else None
    destination = value.get("pinned_destination")
    if (
        (not is_legacy and value.get("schema_version") != RECONCILIATION_SCHEMA_VERSION)
        or value.get("slug") != slug
        or not isinstance(value.get("topic_generation"), str)
        or not isinstance(value.get("reconciliation_id"), str)
        or not str(value.get("reconciliation_id")).startswith("reconcile_")
        or not isinstance(value.get("source_fingerprint"), str)
        or not isinstance(canonical, dict)
        or not isinstance(reconciliation, dict)
        or reconciliation.get("reconciliation_id") != value.get("reconciliation_id")
        or reconciliation.get("source_fingerprint") != value.get("source_fingerprint")
        or not isinstance(destination, dict)
        or destination.get("bundle_id") != canonical.get("bundle_id")
        or destination.get("bundle_version") != canonical.get("bundle_version")
        or destination.get("route_id")
        != (route.get("route_id") if isinstance(route, dict) else None)
        or not isinstance(value.get("from_version"), str)
        or not isinstance(value.get("to_version"), str)
        or value.get("to_version") != canonical.get("bundle_version")
        or not isinstance(legacy_context, dict)
        or not isinstance(value.get("aliases_applied"), dict)
        or value.get("aliases_applied") != legacy_context.get("aliases_applied")
        or not isinstance(value.get("unmatched_references"), list)
        or value.get("unmatched_references") != legacy_context.get("unassessed")
        or (
            not is_legacy
            and (
                not isinstance(digest, str)
                or digest != interview_curriculum.canonical_fingerprint(unsigned)
            )
        )
    ):
        raise _cli().OpenLearnError(
            "interview curriculum reconciliation receipt has invalid identity"
        )
    migrated = copy.deepcopy(value)
    if is_legacy:
        migrated["schema_version"] = RECONCILIATION_SCHEMA_VERSION
        migrated["receipt_sha256"] = interview_curriculum.canonical_fingerprint(migrated)
    return migrated


def _projection_recovery_journal(
    slug: str,
    canonical: dict[str, object],
    *,
    topic_generation: str,
    receipt_sha256: str | None,
) -> dict[str, object]:
    journal: dict[str, object] = {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "journal_kind": "projection_recovery",
        "slug": slug,
        "topic_generation": topic_generation,
        "canonical_state": copy.deepcopy(canonical),
        "metadata_projection": interview_curriculum.compatibility_projection(canonical),
        "receipt_sha256": receipt_sha256,
    }
    journal["journal_sha256"] = interview_curriculum.canonical_fingerprint(
        _reconciliation_journal_identity(journal)
    )
    return journal


def _apply_projection_recovery_journal(slug: str, value: object) -> bool:
    cli = _cli()
    required = {
        "schema_version",
        "journal_kind",
        "slug",
        "topic_generation",
        "canonical_state",
        "metadata_projection",
        "receipt_sha256",
        "journal_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise cli.OpenLearnError("interview curriculum reconciliation journal is malformed")
    canonical = value.get("canonical_state")
    projection = value.get("metadata_projection")
    receipt_hash = value.get("receipt_sha256")
    if (
        value.get("schema_version") != RECONCILIATION_SCHEMA_VERSION
        or value.get("journal_kind") != "projection_recovery"
        or value.get("slug") != slug
        or not isinstance(value.get("topic_generation"), str)
        or not isinstance(canonical, dict)
        or not isinstance(projection, dict)
        or receipt_hash is not None
        and not isinstance(receipt_hash, str)
        or projection != interview_curriculum.compatibility_projection(canonical)
        or value.get("journal_sha256")
        != interview_curriculum.canonical_fingerprint(_reconciliation_journal_identity(value))
    ):
        raise cli.OpenLearnError("interview curriculum reconciliation journal has invalid identity")
    if isinstance(receipt_hash, str):
        receipt_path = cli.interview_reconciliation_receipt_path(slug)
        try:
            stored_receipt = _validated_reconciliation_receipt(
                slug, json.loads(receipt_path.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise cli.OpenLearnError(
                "interview curriculum reconciliation receipt is unreadable"
            ) from exc
        if (
            stored_receipt.get("receipt_sha256") != receipt_hash
            or stored_receipt.get("canonical_state") != canonical
        ):
            raise cli.OpenLearnError(
                "interview curriculum reconciliation journal has invalid receipt identity"
            )
    if cli.current_topic_generation(slug) != value["topic_generation"]:
        cli.durable_unlink(cli.interview_reconciliation_journal_path(slug))
        return False
    current_state = cli._load_state_unlocked(slug)
    if current_state.get("interview_curriculum") != canonical:
        current_state["interview_curriculum"] = copy.deepcopy(canonical)
        cli.write_text_atomic(
            cli.topic_state_path(slug),
            json.dumps(current_state, indent=2, sort_keys=True) + "\n",
        )
    _reconciliation_checkpoint("after_state")
    topic_path = cli.topic_path(slug)
    metadata, body = cli.parse_topic(topic_path.read_text(encoding="utf-8"))
    projection_keys = ("course_units", "current_unit", "current_slide", "current_focus")
    if any(metadata.get(key) != projection.get(key) for key in projection_keys):
        metadata = dict(metadata)
        for key in projection_keys:
            metadata[key] = copy.deepcopy(projection[key])
        cli.write_text_atomic(topic_path, cli.format_topic(metadata, body))
    _reconciliation_checkpoint("after_projection")
    cli.durable_unlink(cli.interview_reconciliation_journal_path(slug))
    return True


def _apply_reconciliation_journal(slug: str, journal: dict[str, object]) -> bool:
    cli = _cli()
    if journal.get("journal_kind") == "projection_recovery":
        return _apply_projection_recovery_journal(slug, journal)
    journal = _validated_reconciliation_journal(slug, journal)
    generation = cli.current_topic_generation(slug)
    if generation != journal.get("topic_generation"):
        cli.durable_unlink(cli.interview_reconciliation_journal_path(slug))
        return False
    canonical_state = journal.get("canonical_state")
    metadata_projection = journal.get("metadata_projection")
    event = journal.get("event")
    receipt = journal.get("receipt")
    if not all(
        isinstance(item, dict) for item in (canonical_state, metadata_projection, event, receipt)
    ):
        raise cli.OpenLearnError("interview curriculum reconciliation journal is malformed")
    source_payload = journal.get("source_payload")
    if not isinstance(source_payload, dict):
        raise cli.OpenLearnError("interview curriculum reconciliation journal is malformed")
    profile_value = interview_prep.load_profile(cli.interview_profile_path(slug))
    current_source, _raw_metadata, _state, _body = _reconciliation_source_payload(
        slug, profile_value=profile_value, journal=journal
    )
    if interview_curriculum.canonical_fingerprint(current_source) != journal.get(
        "source_fingerprint"
    ):
        cli.durable_unlink(cli.interview_reconciliation_journal_path(slug))
        return False
    reconciliation_id = str(journal["reconciliation_id"])
    state_path = cli.topic_state_path(slug)
    topic_path = cli.topic_path(slug)
    raw_metadata, body = cli.parse_topic(topic_path.read_text(encoding="utf-8"))
    current_state = cli._load_state_unlocked(slug)
    current_canonical = current_state.get("interview_curriculum")
    if current_canonical != canonical_state:
        merged_state = dict(current_state)
        merged_state["interview_curriculum"] = canonical_state
        cli.write_text_atomic(
            state_path,
            json.dumps(merged_state, indent=2, sort_keys=True) + "\n",
        )
    projection_keys = ("course_units", "current_unit", "current_slide", "current_focus")
    if any(raw_metadata.get(key) != metadata_projection.get(key) for key in projection_keys):
        merged_metadata = dict(raw_metadata)
        for key in projection_keys:
            merged_metadata[key] = metadata_projection[key]
        cli.write_text_atomic(topic_path, cli.format_topic(merged_metadata, body))
    _reconciliation_checkpoint("after_state")

    events_path = cli.topic_events_path(slug)
    if not _event_has_reconciliation(events_path, reconciliation_id):
        existing = events_path.read_text(encoding="utf-8") if events_path.exists() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        cli.write_text_atomic(events_path, existing + json.dumps(event, sort_keys=True) + "\n")
    _reconciliation_checkpoint("after_event")

    receipt_path = cli.interview_reconciliation_receipt_path(slug)
    expected_receipt = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if not receipt_path.exists() or receipt_path.read_text(encoding="utf-8") != expected_receipt:
        cli.write_text_atomic(receipt_path, expected_receipt)
        receipt_path.chmod(0o600)
    _reconciliation_checkpoint("after_receipt")
    cli.durable_unlink(cli.interview_reconciliation_journal_path(slug))
    return True


def _position_from_canonical(canonical: dict[str, object]) -> dict[str, object]:
    cursor = canonical.get("cursor")
    route = canonical.get("route")
    if not isinstance(cursor, dict) or not isinstance(route, dict):
        raise ValueError("canonical interview curriculum state is malformed")
    ref = cursor.get("skill_ref")
    if not isinstance(ref, dict) or not isinstance(ref.get("skill_id"), str):
        raise ValueError("canonical interview curriculum cursor is malformed")
    skill_id = str(ref["skill_id"])
    identity_keys = (
        "graph_id",
        "graph_version",
        "mastery_policy_version",
        "skill_id",
    )
    skills = route.get("skills")
    selected = (
        next(
            (
                item
                for item in skills
                if isinstance(item, dict)
                and isinstance(item.get("skill_ref"), dict)
                and all(item["skill_ref"].get(key) == ref.get(key) for key in identity_keys)
            ),
            None,
        )
        if isinstance(skills, list)
        else None
    )
    return {
        "unit_id": cursor["unit_id"],
        "section_id": cursor["section_id"],
        "skill_id": skill_id,
        "emphasis": selected.get("depth_mode", "learn") if isinstance(selected, dict) else "learn",
        "review_reason": (
            "legacy weak spot" if cursor.get("instruction_status") == "review" else None
        ),
    }


def prepare_interview_curriculum(slug: str, *, boundary: str = "resume") -> dict[str, object]:
    """Explicitly reconcile a legacy interview course into its pinned route."""
    if boundary not in {"preparation", "resume"}:
        raise ValueError("interview curriculum preparation boundary is invalid")
    cli = _cli()
    recover_interview_route_acceptance(slug)
    canonical_slug = cli.slugify(slug)
    if canonical_slug != slug or not cli.interview_profile_path(slug).exists():
        raise cli.OpenLearnError(f"interview course not found: {slug}")
    journal_path = cli.interview_reconciliation_journal_path(slug)
    profile_path = cli.interview_profile_path(slug)
    with (
        cli.file_lock(journal_path),
        cli.topic_store_locks(slug),
        cli.file_lock(profile_path),
    ):
        cli.raise_if_topic_tombstoned(slug)
        stale_journal: dict[str, object] | None = None
        if journal_path.exists():
            try:
                pending = json.loads(journal_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise cli.OpenLearnError(
                    "interview curriculum reconciliation journal is unreadable"
                ) from exc
            if not isinstance(pending, dict):
                raise cli.OpenLearnError("interview curriculum reconciliation journal is malformed")
            if not _apply_reconciliation_journal(slug, pending) and pending.get(
                "topic_generation"
            ) == cli.current_topic_generation(slug):
                stale_journal = pending

        profile_value = interview_prep.load_profile(profile_path)
        state = _load_authoritative_state_unlocked(slug)
        existing = state.get("interview_curriculum")
        stale_canonical = (
            stale_journal.get("canonical_state") if isinstance(stale_journal, dict) else None
        )
        if isinstance(existing, dict) and existing != stale_canonical:
            generation = cli.current_topic_generation(slug)
            if not isinstance(generation, str):
                raise cli.OpenLearnError("topic was deleted during curriculum recovery")
            projection = interview_curriculum.compatibility_projection(existing)
            current_metadata, _body = cli.parse_topic(
                cli.topic_path(slug).read_text(encoding="utf-8")
            )
            projection_keys = ("course_units", "current_unit", "current_slide", "current_focus")
            if any(current_metadata.get(key) != projection.get(key) for key in projection_keys):
                recovery = _projection_recovery_journal(
                    slug,
                    existing,
                    topic_generation=generation,
                    receipt_sha256=None,
                )
                cli.write_text_atomic(
                    journal_path, json.dumps(recovery, indent=2, sort_keys=True) + "\n"
                )
                journal_path.chmod(0o600)
                _apply_projection_recovery_journal(slug, recovery)
            return _position_from_canonical(existing)
        receipt_path = cli.interview_reconciliation_receipt_path(slug)
        if receipt_path.exists() and stale_journal is None:
            try:
                receipt_value = json.loads(receipt_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise cli.OpenLearnError(
                    "interview curriculum reconciliation receipt is unreadable"
                ) from exc
            receipt_value = _validated_reconciliation_receipt(slug, receipt_value)
            expected_receipt = json.dumps(receipt_value, indent=2, sort_keys=True) + "\n"
            if receipt_path.read_text(encoding="utf-8") != expected_receipt:
                cli.write_text_atomic(receipt_path, expected_receipt)
                receipt_path.chmod(0o600)
            receipt_canonical = (
                receipt_value.get("canonical_state")
                if receipt_value.get("topic_generation") == cli.current_topic_generation(slug)
                else None
            )
            if isinstance(receipt_canonical, dict):
                generation = cli.current_topic_generation(slug)
                if not isinstance(generation, str):
                    raise cli.OpenLearnError("topic was deleted during curriculum recovery")
                recovery = _projection_recovery_journal(
                    slug,
                    receipt_canonical,
                    topic_generation=generation,
                    receipt_sha256=str(receipt_value["receipt_sha256"]),
                )
                cli.write_text_atomic(
                    journal_path, json.dumps(recovery, indent=2, sort_keys=True) + "\n"
                )
                journal_path.chmod(0o600)
                _apply_projection_recovery_journal(slug, recovery)
                return _position_from_canonical(receipt_canonical)
        route = _route_for_profile(profile_value)
        source_payload, raw_metadata, state, body = _reconciliation_source_payload(
            slug,
            profile_value=profile_value,
            journal=stale_journal,
        )
        metadata = cli.merge_topic_state(cli.normalize_topic_metadata(raw_metadata, slug), state)
        legacy_events = cast(list[dict[str, object]], source_payload["events"])
        source_fingerprint = interview_curriculum.canonical_fingerprint(source_payload)
        generation = cli.topic_generation_from_metadata(slug, raw_metadata)
        reconciliation_id = "reconcile_" + interview_curriculum.canonical_fingerprint(
            {
                "generation": generation,
                "source_fingerprint": source_fingerprint,
                "bundle_id": route["bundle_id"],
                "bundle_version": route["bundle_version"],
            }
        )
        bundle = interview_curriculum.load_default_bundle()
        canonical = interview_curriculum.build_canonical_curriculum_state(
            bundle,
            route,
            metadata=metadata,
            dynamic_state={**state, "legacy_events": legacy_events},
            source_fingerprint=source_fingerprint,
            reconciliation_id=reconciliation_id,
        )
        projection = interview_curriculum.compatibility_projection(canonical)
        timestamp = datetime.now(timezone.utc).isoformat()
        event = {
            "schema_version": cli.EVENT_SCHEMA_VERSION,
            "ts": timestamp,
            "event_type": "interview_curriculum_reconciled",
            "slug": slug,
            "data": {
                "reconciliation_id": reconciliation_id,
                "source_fingerprint": source_fingerprint,
                "from_version": "legacy-unversioned",
                "to_version": route["bundle_version"],
                "aliases_applied": canonical["legacy_context"]["aliases_applied"],
                "unmatched_references": canonical["legacy_context"]["unassessed"],
                "pinned_destination": {
                    "bundle_id": route["bundle_id"],
                    "bundle_version": route["bundle_version"],
                    "route_id": route["route_id"],
                },
            },
        }
        receipt = {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "slug": slug,
            "topic_generation": generation,
            "reconciliation_id": reconciliation_id,
            "source_fingerprint": source_fingerprint,
            "from_version": "legacy-unversioned",
            "to_version": route["bundle_version"],
            "aliases_applied": canonical["legacy_context"]["aliases_applied"],
            "unmatched_references": canonical["legacy_context"]["unassessed"],
            "pinned_destination": event["data"]["pinned_destination"],
            "canonical_state": canonical,
        }
        receipt["receipt_sha256"] = interview_curriculum.canonical_fingerprint(receipt)
        journal = {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "slug": slug,
            "topic_generation": generation,
            "reconciliation_id": reconciliation_id,
            "source_fingerprint": source_fingerprint,
            "source_payload": source_payload,
            "canonical_state": canonical,
            "metadata_projection": projection,
            "event": event,
            "receipt": receipt,
        }
        if stale_journal is not None:
            journal["superseded_projection"] = stale_journal.get("metadata_projection")
            journal["superseded_reconciliation_id"] = stale_journal.get("reconciliation_id")
        journal["journal_sha256"] = interview_curriculum.canonical_fingerprint(
            _reconciliation_journal_identity(journal)
        )
        _validated_reconciliation_journal(slug, journal)
        cli.write_text_atomic(journal_path, json.dumps(journal, indent=2, sort_keys=True) + "\n")
        journal_path.chmod(0o600)
        _reconciliation_checkpoint("after_journal")
        _apply_reconciliation_journal(slug, journal)
        return _position_from_canonical(canonical)


def _validate_request(request: CourseCreationRequest) -> None:
    cli = _cli()
    if not request.name.strip():
        raise cli.OpenLearnError("course name is required")
    if request.name != request.name.strip():
        raise cli.OpenLearnError("course name must not start or end with whitespace")
    if request.template_id is None and not request.goal.strip():
        raise cli.OpenLearnError("a freeform course goal is required")
    if request.submission_id is not None:
        try:
            parsed = UUID(request.submission_id)
        except (ValueError, AttributeError) as exc:
            raise cli.OpenLearnError("submission ID must be a canonical UUID") from exc
        if str(parsed) != request.submission_id:
            raise cli.OpenLearnError("submission ID must be a canonical UUID")
    calibration = request.calibration
    if calibration is not None and (
        len(calibration.goal) > CALIBRATION_TEXT_LIMIT
        or len(calibration.experience) > CALIBRATION_TEXT_LIMIT
    ):
        raise cli.OpenLearnError("calibration text must be at most 4000 characters")


def _find_creation_submission(submission_id: str) -> str | None:
    cli = _cli()
    for path in cli.recent_topic_paths():
        state = cli.load_state(path.stem)
        if state.get(CREATION_SUBMISSION_STATE_KEY) == submission_id:
            return path.stem
        summary = cli.read_topic_summary(path)
        if summary.metadata.get(CREATION_SUBMISSION_METADATA_KEY) == submission_id:
            cli.update_state_atomic(
                path.stem,
                lambda current: current.__setitem__(CREATION_SUBMISSION_STATE_KEY, submission_id),
            )
            return path.stem
    return None


def _repair_creation_artifacts(slug: str, request: CourseCreationRequest) -> None:
    """Complete only missing creation artifacts after an interrupted first write."""
    cli = _cli()

    def repair_state(state: dict[str, object]) -> None:
        if request.submission_id is not None:
            state.setdefault(CREATION_SUBMISSION_STATE_KEY, request.submission_id)
        if request.calibration is not None and CALIBRATION_STATE_KEY not in state:
            calibration = request.calibration
            state[CALIBRATION_STATE_KEY] = {
                "goal": calibration.goal.strip(),
                "experience": calibration.experience.strip(),
                "skipped": calibration.skipped,
                "recorded_at": (calibration.recorded_at or datetime.now(timezone.utc).isoformat()),
            }

    cli.update_state_atomic(slug, repair_state)
    summary = cli.read_topic_summary(cli.topic_path(slug))
    template_id = summary.metadata.get("template_id")
    if not isinstance(template_id, str):
        return
    template = load_course_template(template_id)
    if template.entry_mode != "interview_prep":
        return
    profile_path = cli.interview_profile_path(slug)
    with cli.topic_store_locks(slug):
        if not profile_path.exists():
            interview_prep.create_profile(
                profile_path,
                cli.default_interview_profile_values(),
            )


def _course_body(title: str, goal: str) -> str:
    return f"""# {title}

## Current Goal

{goal}

## Notes

- Add class notes, links, questions, or source summaries here.

## Session Log

"""


def _creation_metadata(
    *,
    title: str,
    slug: str,
    goal: str,
    mastery_profile: str,
    template_id: str | None,
    template_units: tuple[str, ...],
    submission_id: str | None,
) -> dict[str, object]:
    cli = _cli()
    metadata: dict[str, object] = {
        "topic": title,
        "slug": slug,
        "topic_generation": f"topic_{uuid4().hex}",
        "mastery_profile": mastery_profile,
        "current_focus": "",
        "course_started": False,
        "course_completed": False,
        "level": "beginner",
        "model": cli.configured_model(),
        "created": date.today().isoformat(),
        "last_reviewed": "",
        "goal": goal,
        "known": [],
        "weak_spots": [],
        "review_due": [],
        "course_options": cli.default_course_options(),
        "last_answer_status": "",
        "consecutive_correct": 0,
        "consecutive_misses": 0,
        "last_video_focus": None,
        "quiz_history": [],
        "placement_result": {},
        "review_session_active": False,
    }
    if template_id is not None:
        metadata["template_id"] = template_id
        metadata["template_units"] = list(template_units)
    if submission_id is not None:
        metadata[CREATION_SUBMISSION_METADATA_KEY] = submission_id
    return metadata


def _save_creation_state(slug: str, request: CourseCreationRequest) -> None:
    cli = _cli()
    state = cli.load_state(slug)
    if request.submission_id is not None:
        state[CREATION_SUBMISSION_STATE_KEY] = request.submission_id
    if request.calibration is not None:
        calibration = request.calibration
        state[CALIBRATION_STATE_KEY] = {
            "goal": calibration.goal.strip(),
            "experience": calibration.experience.strip(),
            "skipped": calibration.skipped,
            "recorded_at": calibration.recorded_at or datetime.now(timezone.utc).isoformat(),
        }
    cli.save_state(slug, state)


def create_course(request: CourseCreationRequest) -> CourseCreationResult:
    """Create a durable course without invoking interactive CLI command handlers."""
    _validate_request(request)
    cli = _cli()
    cli.topics_dir().mkdir(parents=True, exist_ok=True)
    creation_lock = cli.topics_dir() / ".course-creation"
    with cli.file_lock(creation_lock):
        if request.submission_id is not None:
            existing = _find_creation_submission(request.submission_id)
            if existing is not None:
                _repair_creation_artifacts(existing, request)
                return CourseCreationResult(course=course_snapshot(existing), created=False)

        template = (
            load_course_template(request.template_id) if request.template_id is not None else None
        )
        slug = available_course_slug(request.name)
        title = request.name
        goal = request.goal.strip() or (template.goal if template is not None else "")
        metadata = _creation_metadata(
            title=title,
            slug=slug,
            goal=goal,
            mastery_profile=request.mastery_profile,
            template_id=template.slug if template is not None else None,
            template_units=template.units if template is not None else (),
            submission_id=request.submission_id,
        )
        path = cli.topic_path(slug)
        with cli.topic_store_locks(slug, include_journal=True):
            if path.exists():
                raise cli.OpenLearnError(f"topic already exists: {slug}")
            cli.write_topic(path, metadata, _course_body(title, goal))
            _save_creation_state(slug, request)
            if template is not None and template.entry_mode == "interview_prep":
                interview_prep.create_profile(
                    cli.interview_profile_path(slug),
                    cli.default_interview_profile_values(),
                )

        return CourseCreationResult(course=course_snapshot(slug), created=True)
