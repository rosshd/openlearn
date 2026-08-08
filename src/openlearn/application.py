"""Presentation-independent values exposed to openlearn interfaces.

The dataclasses in this module deliberately contain no Rich, HTTP, or argparse
types.  CLI and web adapters can render the same immutable application values
without parsing one another's output.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
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


def sync_interview_placement(slug: str) -> dict[str, object]:
    """Project durable placement evidence into the learner's local profile."""
    from openlearn import cli

    return cli.sync_interview_placement(slug)


def start_interview_placement(slug: str) -> dict[str, object]:
    """Start a reasoning-placement activity and bind it to the local profile."""
    from openlearn import cli, interview_prep

    activity = cli._begin_interview_activity(
        slug, lifecycle_version=interview_prep.PLACEMENT_V3
    )
    with cli.interview_profile_write_lock(slug):
        return interview_prep.start_placement(
            cli.interview_profile_path(slug),
            activity_id=str(activity["activity_id"]),
            lifecycle_version=interview_prep.PLACEMENT_V3,
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
