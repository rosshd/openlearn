"""Presentation-independent values exposed to openlearn interfaces.

The dataclasses in this module deliberately contain no Rich, HTTP, or argparse
types.  CLI and web adapters can render the same immutable application values
without parsing one another's output.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from openlearn.data_management import HomeInventory


@dataclass(frozen=True)
class UnitProgress:
    unit: int
    title: str
    known: int
    total: int
    percent: int


@dataclass(frozen=True)
class ReviewProgress:
    due_today: int = 0
    due_this_week: int = 0
    due_later: int = 0

    @property
    def total(self) -> int:
        return self.due_today + self.due_this_week + self.due_later


@dataclass(frozen=True)
class CourseProgress:
    known: int
    total: int
    percent: int
    units: tuple[UnitProgress, ...] = ()
    reviews: ReviewProgress = ReviewProgress()


@dataclass(frozen=True)
class CalibrationContext:
    """Self-reported context that must never be projected as mastery evidence."""

    goal: str = ""
    experience: str = ""
    skipped: bool = False
    recorded_at: str = ""


@dataclass(frozen=True)
class CourseCard:
    slug: str
    title: str
    goal: str
    current_focus: str
    started: bool
    completed: bool
    updated_at: str
    progress: CourseProgress
    template_id: str | None = None
    interview: InterviewCardProjection | None = None

    @property
    def incomplete(self) -> bool:
        return not self.completed


@dataclass(frozen=True)
class CourseSnapshot:
    card: CourseCard
    calibration: CalibrationContext | None
    mastery_profile: str
    model: str
    created_at: str

    @property
    def slug(self) -> str:
        return self.card.slug


@dataclass(frozen=True)
class DashboardSnapshot:
    courses: tuple[CourseCard, ...]
    resume: CourseCard | None
    active_slug: str | None
    reviews: ReviewProgress
    generated_at: str


@dataclass(frozen=True)
class InterviewCurriculumPosition:
    unit_id: str
    section_id: str
    skill_id: str
    emphasis: str
    review_reason: str | None = None


@dataclass(frozen=True)
class InterviewConceptProjection:
    unit_id: str
    unit_label: str
    section_id: str
    section_label: str
    graph_id: str
    graph_version: str
    mastery_policy_version: str
    skill_id: str
    skill_label: str
    emphasis: str
    instruction_status: str
    requirement: str

    @property
    def label(self) -> str:
        return f"{self.unit_label} / {self.section_label} / {self.skill_label}"


@dataclass(frozen=True)
class InterviewCommittedLesson:
    lesson_id: str
    title: str
    content: str


@dataclass(frozen=True)
class InterviewCoverageProjection:
    covered: int
    total: int
    percent: int
    summary: str


@dataclass(frozen=True)
class InterviewReadinessProjection:
    due: int
    deferred: int
    verify: int
    weak: int
    total: int
    summary: str
    next_retrieval: str | None = None


InterviewOperationAction = Literal[
    "retry",
    "cancel",
    "provider-settings",
    "refresh",
    "adopt",
    "practice",
    "continue",
    "skip",
    "question",
]


@dataclass(frozen=True)
class InterviewOperationProjection:
    state: Literal[
        "reserved",
        "generating",
        "generated",
        "committed",
        "provider-error",
        "busy",
        "stale-conflict",
        "caught-up",
    ]
    submission_id: str | None
    message: str
    actions: tuple[InterviewOperationAction, ...]
    error_code: str | None = None


@dataclass(frozen=True)
class InterviewLearningProjection:
    slug: str
    title: str
    revision: int
    position: InterviewConceptProjection
    committed_lesson: InterviewCommittedLesson
    next_target: InterviewConceptProjection | None
    operation: InterviewOperationProjection
    coverage: InterviewCoverageProjection
    readiness: InterviewReadinessProjection
    pending_prompt: str = ""
    saved_response: str = ""
    deferred_skill: InterviewConceptProjection | None = None
    deferred_explanation: str | None = None


@dataclass(frozen=True)
class InterviewCardProjection:
    """Lightweight canonical progress used by course-list cards."""

    position: InterviewConceptProjection
    coverage: InterviewCoverageProjection
    readiness: InterviewReadinessProjection


def _concept_projection(
    canonical: Mapping[str, object], cursor_or_target: Mapping[str, object]
) -> InterviewConceptProjection:
    from openlearn import interview_curriculum

    route = canonical.get("route")
    skills = route.get("skills") if isinstance(route, Mapping) else None
    ref = cursor_or_target.get("skill_ref")
    skill_id = ref.get("skill_id") if isinstance(ref, Mapping) else None
    if not isinstance(skills, list) or not isinstance(skill_id, str):
        raise ValueError("interview curriculum position is malformed")
    identity_keys = (
        "graph_id",
        "graph_version",
        "mastery_policy_version",
        "skill_id",
    )

    def matches_ref(value: object) -> bool:
        if not isinstance(value, Mapping):
            return False
        value_ref = value.get("skill_ref")
        return isinstance(value_ref, Mapping) and all(
            value_ref.get(key) == ref.get(key) for key in identity_keys
        )

    item = next((value for value in skills if matches_ref(value)), None)
    if not isinstance(item, Mapping) or not isinstance(ref, Mapping):
        raise ValueError("interview curriculum position is absent from its route")
    bundle = interview_curriculum.load_pinned_bundle(
        str(canonical["bundle_id"]), str(canonical["bundle_version"])
    )
    graph = bundle.graph_registry.graph(
        str(ref["graph_id"]),
        str(ref["graph_version"]),
        str(ref["mastery_policy_version"]),
    )
    skill = graph.skill(skill_id)
    return InterviewConceptProjection(
        unit_id=str(item["unit_id"]),
        unit_label=str(item.get("unit_label") or item["unit_id"]),
        section_id=str(item["section_id"]),
        section_label=str(item.get("section_label") or item["section_id"]),
        graph_id=str(ref["graph_id"]),
        graph_version=str(ref["graph_version"]),
        mastery_policy_version=str(ref["mastery_policy_version"]),
        skill_id=skill_id,
        skill_label=skill.name,
        emphasis=str(
            cursor_or_target.get("depth_mode") or item.get("depth_mode") or "learn"
        ).title(),
        instruction_status=str(
            cursor_or_target.get("instruction_status")
            or item.get("instruction_status")
            or "uncovered"
        ),
        requirement=str(item.get("requirement") or "required"),
    )


def _learning_positions(
    canonical: Mapping[str, object],
) -> tuple[
    InterviewConceptProjection,
    InterviewConceptProjection | None,
    Mapping[str, object] | None,
]:
    active_raw = canonical.get("active_operation")
    active = active_raw if isinstance(active_raw, Mapping) else None
    cursor = canonical.get("cursor")
    if not isinstance(cursor, Mapping):
        raise ValueError("interview curriculum cursor is malformed")
    committed_target = canonical.get("committed_target")
    committed_cursor = (
        committed_target if isinstance(committed_target, Mapping) else cursor
    )
    if active is not None and not isinstance(committed_target, Mapping):
        rollback = active.get("rollback")
        rollback_cursor = rollback.get("cursor") if isinstance(rollback, Mapping) else None
        rollback_value = (
            rollback_cursor.get("value")
            if isinstance(rollback_cursor, Mapping)
            and rollback_cursor.get("present") is True
            else None
        )
        if isinstance(rollback_value, Mapping):
            committed_cursor = rollback_value
    position = _concept_projection(canonical, committed_cursor)
    target = active.get("target") if active is not None else None
    next_target = (
        _concept_projection(canonical, target) if isinstance(target, Mapping) else None
    )
    return position, next_target, active


def _committed_lesson(
    body: str, position: InterviewConceptProjection
) -> InterviewCommittedLesson:
    from openlearn import cli

    _context, session_log = cli.split_session_log(body)
    latest = cli.last_tutor_lesson_entry_from_entries(cli.session_entries(session_log))
    content = latest[1]["response"] if latest is not None else ""
    lesson_id = (
        cli.tutor_lesson_entry_id(latest[1])
        if latest is not None
        else f"lesson_{position.skill_id.replace('.', '_')}"
    )
    return InterviewCommittedLesson(
        lesson_id=lesson_id,
        title=position.skill_label,
        content=content,
    )


def _evidence_set(evidence: Mapping[str, object], key: str) -> set[str]:
    values = evidence.get(key)
    return (
        {value for value in values if isinstance(value, str)}
        if isinstance(values, list)
        else set()
    )


def _learning_progress(
    canonical: Mapping[str, object], metadata: Mapping[str, object]
) -> tuple[
    InterviewCoverageProjection,
    InterviewReadinessProjection,
    list[Mapping[str, object]],
    list[Mapping[str, object]],
]:
    route = canonical.get("route")
    route_skills_raw = route.get("skills") if isinstance(route, Mapping) else None
    route_skills = (
        [item for item in route_skills_raw if isinstance(item, Mapping)]
        if isinstance(route_skills_raw, list)
        else []
    )
    has_explicit_optional = (
        isinstance(route, Mapping) and "optional_skill_ids" in route
    )
    explicit_optional = route.get("optional_skill_ids") if isinstance(route, Mapping) else None
    accepted_optional = (
        {value for value in explicit_optional if isinstance(value, str)}
        if has_explicit_optional and isinstance(explicit_optional, list)
        else {
            str(item["skill_ref"]["skill_id"])
            for item in route_skills
            if item.get("requirement") == "optional"
            and isinstance(item.get("skill_ref"), Mapping)
        }
    )
    counted_ids = {
        str(item["skill_ref"]["skill_id"])
        for item in route_skills
        if isinstance(item.get("skill_ref"), Mapping)
        and (
            item.get("requirement") == "required"
            or item["skill_ref"].get("skill_id") in accepted_optional
        )
    }
    evidence_raw = canonical.get("evidence")
    evidence = evidence_raw if isinstance(evidence_raw, Mapping) else {}
    exposed = _evidence_set(evidence, "exposed")
    ready = _evidence_set(evidence, "ready")
    due_ids = _evidence_set(evidence, "due_review") & counted_ids
    weak_ids = _evidence_set(evidence, "weak") & counted_ids
    covered = len((exposed | ready) & counted_ids)
    total = len(counted_ids)
    coverage = InterviewCoverageProjection(
        covered=covered,
        total=total,
        percent=round(covered / total * 100) if total else 0,
        summary=(
            f"{covered} of {total} accepted route skills covered once."
            if total
            else "First-pass coverage starts with the first committed lesson."
        ),
    )
    deferred_raw = canonical.get("deferred")
    deferred_values = (
        [value for value in deferred_raw if isinstance(value, Mapping)]
        if isinstance(deferred_raw, list)
        else []
    )
    deferred_ids = {
        str(value["skill_id"])
        for value in deferred_values
        if isinstance(value.get("skill_id"), str) and value["skill_id"] in counted_ids
    }
    verify_ids = {
        str(item["skill_ref"]["skill_id"])
        for item in route_skills
        if isinstance(item.get("skill_ref"), Mapping)
        and item.get("depth_mode") == "verify"
        and item["skill_ref"].get("skill_id") in counted_ids - ready
    }
    work_ids = due_ids | deferred_ids | verify_ids | weak_ids
    due_labels = {
        _concept_projection(canonical, item).skill_label.casefold()
        for item in route_skills
        if isinstance(item.get("skill_ref"), Mapping)
        and item["skill_ref"].get("skill_id") in due_ids
    }
    review_due = metadata.get("review_due")
    due_dates = (
        sorted(
            str(item["due"])
            for item in review_due
            if isinstance(item, Mapping)
            and isinstance(item.get("due"), str)
            and isinstance(item.get("concept"), str)
            and (
                item["concept"] in due_ids
                or item["concept"].casefold() in due_labels
            )
        )
        if isinstance(review_due, list)
        else []
    )
    readiness = InterviewReadinessProjection(
        due=len(due_ids),
        deferred=len(deferred_ids),
        verify=len(verify_ids),
        weak=len(weak_ids),
        total=len(work_ids),
        summary=(
            f"{len(work_ids)} readiness item{'s' if len(work_ids) != 1 else ''}: "
            f"{len(due_ids)} due, {len(deferred_ids)} deferred, "
            f"{len(verify_ids)} to verify."
        ),
        next_retrieval=due_dates[0] if due_dates else None,
    )
    return coverage, readiness, route_skills, deferred_values


def _learning_operation(
    canonical: Mapping[str, object],
    state: Mapping[str, object],
    active: Mapping[str, object] | None,
    readiness: InterviewReadinessProjection,
) -> tuple[int, InterviewOperationProjection]:
    from openlearn import interview_curriculum

    internal_raw = state.get("_openlearn_internal")
    internal = internal_raw if isinstance(internal_raw, Mapping) else {}
    revision_raw = internal.get("course_revision")
    revision = revision_raw if isinstance(revision_raw, int) and revision_raw >= 0 else 0
    last_error_raw = internal.get("last_turn_error")
    last_error = last_error_raw if isinstance(last_error_raw, Mapping) else None
    active_internal_raw = internal.get("active_turn")
    active_internal = (
        active_internal_raw if isinstance(active_internal_raw, Mapping) else None
    )
    submission_id = (
        str(active.get("submission_id"))
        if active is not None and isinstance(active.get("submission_id"), str)
        else None
    )
    operation_state: Literal[
        "reserved",
        "generating",
        "generated",
        "committed",
        "provider-error",
        "busy",
        "stale-conflict",
        "caught-up",
    ]
    if active is not None:
        error_matches = (
            last_error is not None and last_error.get("submission_id") == submission_id
        )
        if error_matches:
            operation_state = "provider-error"
            message = "The next target is saved. Retry without advancing again."
            actions = ("retry", "cancel", "provider-settings")
        elif (
            active_internal is not None
            and isinstance(active_internal.get("owner_pid"), int)
            and active_internal.get("owner_pid") != os.getpid()
        ):
            operation_state = "busy"
            message = "Another interface is finishing the saved next target."
            actions = ("refresh", "adopt", "cancel")
        else:
            raw_status = str(
                active_internal.get("status")
                if active_internal is not None
                else active.get("status") or "reserved"
            )
            operation_state = (
                "generated"
                if raw_status == "generated"
                else "generating"
                if raw_status in {"judging", "generating", "validating"}
                else "reserved"
            )
            message = "Preparing the saved next curriculum target."
            actions = ("cancel",)
    elif last_error is not None and last_error.get("code") == "course_revision_changed":
        operation_state = "stale-conflict"
        message = "Progress changed elsewhere. Reload the canonical lesson position."
        actions = ("refresh",)
        submission_id = str(last_error.get("submission_id") or "") or None
    else:
        resolution = interview_curriculum.resolve_progression_target(
            canonical, intent="continue"
        )
        if resolution.caught_up:
            operation_state = "caught-up"
            message = (
                f"You are caught up. Next retrieval: {readiness.next_retrieval}."
                if readiness.next_retrieval
                else "You are caught up. No retrieval is scheduled yet."
            )
            actions = ("practice",)
        else:
            operation_state = "committed"
            message = "Current lesson committed locally."
            actions = ("continue", "skip", "question")
    return revision, InterviewOperationProjection(
        state=operation_state,
        submission_id=submission_id,
        message=message,
        actions=actions,
        error_code=(
            str(last_error.get("code"))
            if last_error is not None and isinstance(last_error.get("code"), str)
            else None
        ),
    )


def _deferred_projection(
    canonical: Mapping[str, object],
    route_skills: list[Mapping[str, object]],
    deferred_values: list[Mapping[str, object]],
) -> tuple[InterviewConceptProjection | None, str | None]:
    if not deferred_values:
        return None, None
    deferred_id = deferred_values[-1].get("skill_id")

    def is_deferred_item(item: Mapping[str, object]) -> bool:
        skill_ref = item.get("skill_ref")
        return isinstance(skill_ref, Mapping) and skill_ref.get("skill_id") == deferred_id

    deferred_item = next(
        (item for item in route_skills if is_deferred_item(item)),
        None,
    )
    if not isinstance(deferred_item, Mapping):
        return None, None
    return (
        _concept_projection(
            canonical,
            {
                "skill_ref": deferred_item["skill_ref"],
                "instruction_status": "deferred",
            },
        ),
        "Skipped for now without mastery credit. It returns after another "
        "curriculum target or in a new study session.",
    )


def interview_learning(slug: str) -> InterviewLearningProjection | None:
    """Return the one typed curriculum/lesson projection shared by CLI and web."""
    from openlearn import cli
    from openlearn.courses import interview_learning_source

    source = interview_learning_source(slug)
    if source is None:
        return None
    state = source["state"]
    metadata = source["metadata"]
    body = source["body"]
    assert isinstance(state, dict) and isinstance(metadata, dict) and isinstance(body, str)
    metadata = cli.merge_topic_state(
        cli.normalize_topic_metadata(metadata, slug), state
    )
    canonical = state["interview_curriculum"]
    assert isinstance(canonical, dict)
    position, next_target, active = _learning_positions(canonical)
    lesson = _committed_lesson(body, position)
    coverage, readiness, route_skills, deferred_values = _learning_progress(
        canonical, metadata
    )
    revision, operation = _learning_operation(canonical, state, active, readiness)
    deferred_skill, deferred_explanation = _deferred_projection(
        canonical, route_skills, deferred_values
    )
    return InterviewLearningProjection(
        slug=slug,
        title=str(metadata.get("topic") or slug.replace("-", " ").title()),
        revision=revision,
        position=position,
        committed_lesson=lesson,
        next_target=next_target,
        operation=operation,
        coverage=coverage,
        readiness=readiness,
        pending_prompt=(
            str(metadata["pending_question"].get("question") or "")
            if isinstance(metadata.get("pending_question"), Mapping)
            else ""
        ),
        saved_response=(
            str(state.get("pending_learner_prompt") or "")
            if isinstance(state.get("pending_learner_prompt"), str)
            else ""
        ),
        deferred_skill=deferred_skill,
        deferred_explanation=deferred_explanation,
    )


def interview_learning_card(slug: str) -> InterviewCardProjection | None:
    """Return canonical card metrics without parsing the lesson transcript."""
    from openlearn.courses import interview_learning_source

    source = interview_learning_source(slug, include_body=False)
    if source is None:
        return None
    return interview_learning_card_projection(source["state"], source["metadata"])


def interview_learning_card_projection(
    state_value: object, metadata_value: object
) -> InterviewCardProjection:
    """Project dashboard metrics from an already recovery-fenced course read."""
    if not isinstance(state_value, dict) or not isinstance(metadata_value, dict):
        raise ValueError("interview learning card source is malformed")
    state = state_value
    metadata = metadata_value
    canonical = state["interview_curriculum"]
    if not isinstance(canonical, dict):
        raise ValueError("canonical interview curriculum is malformed")
    position, _next_target, _active = _learning_positions(canonical)
    coverage, readiness, _route_skills, _deferred_values = _learning_progress(
        canonical, metadata
    )
    return InterviewCardProjection(
        position=position,
        coverage=coverage,
        readiness=readiness,
    )


def advance_interview_curriculum(
    slug: str,
    text: str,
    *,
    intent: Literal["continue", "skip", "practice"] = "continue",
    submission_id: str | None = None,
    expected_revision: int | None = None,
    model: str | None = None,
):
    """Run one shared deterministic interview-curriculum navigation turn."""
    from openlearn import tutor_service

    normalized = {
        "skip": "Skip for now and continue to the next curriculum concept.",
        "practice": "Practice now using a covered curriculum concept.",
    }.get(intent, text)
    return tutor_service.submit_turn(
        slug,
        normalized,
        intent="navigation",
        submission_id=submission_id,
        expected_revision=expected_revision,
        model=model,
        progression_intent=intent,
    )


def resume_interview_progression(slug: str, *, model: str | None = None):
    from openlearn import tutor_service

    return tutor_service.resume_interview_progression(slug, model=model)


def cancel_interview_progression(slug: str, submission_id: str) -> None:
    from openlearn import tutor_service

    tutor_service.cancel_interview_progression(slug, submission_id)


@dataclass(frozen=True)
class TemplateSummary:
    template_id: str
    name: str
    goal: str
    tags: tuple[str, ...]
    units: tuple[str, ...]
    entry_mode: str | None = None


@dataclass(frozen=True)
class TemplateCatalog:
    templates: tuple[TemplateSummary, ...]


@dataclass(frozen=True)
class CourseCreationRequest:
    """Validated input for starter-template or freeform course creation."""

    name: str
    goal: str = ""
    template_id: str | None = None
    calibration: CalibrationContext | None = None
    submission_id: str | None = None
    mastery_profile: Literal["efficient", "proficient", "deep"] = "proficient"


@dataclass(frozen=True)
class CourseCreationResult:
    course: CourseSnapshot
    created: bool


@dataclass(frozen=True)
class ProviderSnapshot:
    base_url: str
    model: str
    key_required: bool
    key_configured: bool
    verified: bool
    managed_fields: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.verified and (self.key_configured or not self.key_required)

    def as_dict(self) -> dict[str, object]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "key_required": self.key_required,
            "key_configured": self.key_configured,
            "verified": self.verified,
            "managed_fields": list(self.managed_fields),
            "ready": self.ready,
        }


def _provider_snapshot(status) -> ProviderSnapshot:
    return ProviderSnapshot(
        base_url=status.base_url,
        model=status.model,
        key_required=status.key_required,
        key_configured=status.key_configured,
        verified=status.verified,
        managed_fields=status.managed_fields,
    )


def provider_status(
    *, home: Path | None = None, environ: Mapping[str, str] | None = None
) -> ProviderSnapshot:
    from openlearn import providers

    return _provider_snapshot(providers.provider_status(home=home, environ=environ))


def set_provider_api_key(
    api_key: str, *, home: Path | None = None, environ: Mapping[str, str] | None = None
) -> ProviderSnapshot:
    from openlearn import providers

    return _provider_snapshot(
        providers.set_saved_api_key(api_key, home=home, environ=environ)
    )


def set_provider_model(
    model: str, *, home: Path | None = None, environ: Mapping[str, str] | None = None
) -> ProviderSnapshot:
    from openlearn import providers

    return _provider_snapshot(providers.set_saved_model(model, home=home, environ=environ))


def set_provider_base_url(
    base_url: str,
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ProviderSnapshot:
    from openlearn import providers

    return _provider_snapshot(
        providers.set_saved_base_url(base_url, home=home, environ=environ)
    )


def remove_provider_api_key(
    *, home: Path | None = None, environ: Mapping[str, str] | None = None
) -> ProviderSnapshot:
    from openlearn import providers

    return _provider_snapshot(providers.remove_saved_api_key(home=home, environ=environ))


def dashboard(*, now: datetime | None = None) -> DashboardSnapshot:
    """Return the side-effect-free dashboard query result."""
    from openlearn.courses import dashboard_snapshot

    return dashboard_snapshot(now=now)


def course(slug: str) -> CourseSnapshot:
    """Return one course without changing the active course or study metrics."""
    from openlearn.courses import course_snapshot

    return course_snapshot(slug)


def templates() -> TemplateCatalog:
    from openlearn.courses import template_catalog

    return template_catalog()


def create_course(request: CourseCreationRequest) -> CourseCreationResult:
    from openlearn.courses import create_course as create

    return create(request)


def prepare_interview_curriculum(
    slug: str, *, boundary: Literal["preparation", "resume"] = "resume"
) -> InterviewCurriculumPosition:
    """Explicitly prepare or resume one canonical interview curriculum."""
    from openlearn.courses import prepare_interview_curriculum as prepare

    value = prepare(slug, boundary=boundary)
    return InterviewCurriculumPosition(
        unit_id=str(value["unit_id"]),
        section_id=str(value["section_id"]),
        skill_id=str(value["skill_id"]),
        emphasis=str(value["emphasis"]),
        review_reason=(
            str(value["review_reason"]) if isinstance(value.get("review_reason"), str) else None
        ),
    )


def preview_interview_curriculum_change(
    slug: str, *, changes: Mapping[str, object] | None = None
) -> dict[str, object]:
    """Return a side-effect-free bounded course-outline preview."""
    from datetime import date

    from openlearn import cli, interview_curriculum, interview_prep

    profile = interview_prep.load_profile(cli.interview_profile_path(slug))
    canonical = cli.load_state(slug).get("interview_curriculum")
    bundle = (
        interview_curriculum.load_pinned_bundle(
            str(canonical["bundle_id"]), str(canonical["bundle_version"])
        )
        if isinstance(canonical, dict)
        else interview_curriculum.load_default_bundle()
    )
    return interview_prep.preview_curriculum_change(
        profile,
        changes=changes,
        current_date=date.today(),
        bundle=bundle,
    )


def accept_interview_curriculum(
    slug: str,
    *,
    action: Literal["confirm", "skip", "change"],
    changes: Mapping[str, object] | None = None,
    outline: str = "",
    submission_id: str | None = None,
    expected_revision: int | None = None,
) -> dict[str, object]:
    """Persist an accepted route through the shared recoverable coordinator."""
    from openlearn.courses import accept_interview_curriculum as accept

    return accept(
        slug,
        action=action,
        changes=dict(changes or {}),
        outline=outline,
        submission_id=submission_id,
        expected_revision=expected_revision,
    )


def sync_interview_placement(slug: str) -> dict[str, object]:
    """Project durable placement evidence into the learner's local profile."""
    from openlearn import cli

    return cli.sync_interview_placement(slug)


def start_interview_placement(slug: str) -> dict[str, object]:
    """Start the current rapid confidence placement without presentation code."""
    from openlearn import cli, interview_prep

    with cli.interview_profile_write_lock(slug):
        return interview_prep.start_confidence_placement(
            cli.interview_profile_path(slug),
        )


def discard_interview_placement(slug: str) -> dict[str, object]:
    """Discard the active placement attempt while preserving published evidence."""
    from openlearn import cli

    return cli._discard_interview_placement(slug, cli.interview_profile_path(slug))


def record_interview_placement_response(
    slug: str, *, stage: str, response: str, evidence_id: str
) -> dict[str, object] | None:
    """Record one reasoning response and return the reconciled profile.

    ``None`` means the durable activity is no longer available, allowing a
    presentation adapter to report an optimistic-concurrency conflict.
    """
    from openlearn import cli

    activity = cli._current_interview_activity(slug)
    if activity is None:
        return None
    cli.record_topic_activity_evidence(
        slug,
        activity,
        "interview_observation",
        {"stage": stage, "response": response},
        evidence_id=evidence_id,
    )
    return cli.sync_interview_placement(slug)


def data_inventory() -> HomeInventory:
    """Return the presentation-neutral inventory for the configured Openlearn home."""
    from openlearn.config import project_home
    from openlearn.data_management import inventory_home

    return inventory_home(project_home())
