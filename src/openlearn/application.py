"""Presentation-independent values exposed to openlearn interfaces.

The dataclasses in this module deliberately contain no Rich, HTTP, or argparse
types.  CLI and web adapters can render the same immutable application values
without parsing one another's output.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


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
