"""Shared, output-free course queries and creation operations."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import cast
from uuid import UUID, uuid4

from openlearn import interview_prep
from openlearn import stats as stats_metrics
from openlearn.application import (
    CalibrationContext,
    CourseCard,
    CourseCreationRequest,
    CourseCreationResult,
    CourseProgress,
    CourseSnapshot,
    DashboardSnapshot,
    ReviewProgress,
    TemplateCatalog,
    TemplateSummary,
    UnitProgress,
)
from openlearn.course_templates import available_course_templates, load_course_template

CALIBRATION_STATE_KEY = "progressive_calibration"
CREATION_SUBMISSION_STATE_KEY = "course_creation_submission_id"
CREATION_SUBMISSION_METADATA_KEY = "course_creation_submission_id"
CALIBRATION_TEXT_LIMIT = 4_000


def _cli():
    """Import the legacy storage boundary lazily while it is being extracted."""
    from openlearn import cli

    return cli


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
        cli.topic_path(candidate).exists()
        or cli.topic_deletion_tombstone_path(candidate).exists()
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


def course_snapshot(slug: str, *, today: date | None = None) -> CourseSnapshot:
    cli = _cli()
    canonical = cli.slugify(slug)
    if canonical != slug:
        raise cli.OpenLearnError(f"invalid topic slug: {slug}")
    topic = cli.read_topic_stats(canonical)
    metadata = topic.metadata
    state = cli.load_state(canonical)
    modified = datetime.fromtimestamp(topic.path.stat().st_mtime, timezone.utc).isoformat()
    card = CourseCard(
        slug=canonical,
        title=str(metadata.get("topic") or canonical.replace("-", " ").title()),
        goal=str(metadata.get("goal") or ""),
        current_focus=str(metadata.get("current_focus") or ""),
        started=metadata.get("course_started") is True,
        completed=metadata.get("course_completed") is True,
        updated_at=modified,
        progress=_course_progress(metadata, today or date.today()),
        template_id=(
            str(metadata["template_id"]) if isinstance(metadata.get("template_id"), str) else None
        ),
    )
    return CourseSnapshot(
        card=card,
        calibration=_calibration_from_state(state),
        mastery_profile=str(metadata.get("mastery_profile") or "proficient"),
        model=str(metadata.get("model") or ""),
        created_at=str(metadata.get("created") or ""),
    )


def list_course_snapshots(*, today: date | None = None) -> tuple[CourseSnapshot, ...]:
    cli = _cli()
    if not cli.topics_dir().exists():
        return ()
    paths = cli.recent_topic_paths()
    return tuple(course_snapshot(path.stem, today=today) for path in paths)


def dashboard_snapshot(*, now: datetime | None = None) -> DashboardSnapshot:
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
    reviews = ReviewProgress(
        due_today=sum(card.progress.reviews.due_today for card in cards),
        due_this_week=sum(card.progress.reviews.due_this_week for card in cards),
        due_later=sum(card.progress.reviews.due_later for card in cards),
    )
    return DashboardSnapshot(
        courses=cards,
        resume=resume,
        active_slug=active_slug,
        reviews=reviews,
        generated_at=moment.astimezone(timezone.utc).isoformat(),
    )


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
                lambda current: current.__setitem__(
                    CREATION_SUBMISSION_STATE_KEY, submission_id
                ),
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
                "recorded_at": (
                    calibration.recorded_at
                    or datetime.now(timezone.utc).isoformat()
                ),
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
