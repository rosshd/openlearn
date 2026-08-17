"""HTTP-boundary validation helpers."""

from __future__ import annotations

import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from openlearn.interview_prep import DRAFT_MAX_LENGTH, confidence_topics_for_focus
from openlearn.data_management import (
    CREDENTIAL_CONFIRMATION,
    DELETE_CONFIRMATION,
    MOVE_CONFIRMATION,
    RESET_CONFIRMATION,
)

SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


def canonical_slug(value: str) -> str:
    if not SLUG_PATTERN.fullmatch(value):
        raise ValueError("Invalid course identifier")
    return value


def canonical_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError("Expected a canonical UUID") from error
    if str(parsed) != value:
        raise ValueError("Expected a canonical UUID")
    return value


class ProviderSetupRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    api_key: str = Field(default="", max_length=4096)
    model: str = Field(min_length=1, max_length=256)
    base_url: str = Field(default="", max_length=2048)
    save_unverified: bool = False


class CourseCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    goal: str = Field(min_length=1, max_length=4000)
    experience: str = Field(default="", max_length=4000)
    template_id: str | None = Field(default=None, max_length=128)
    submission_id: str = Field(min_length=1, max_length=64)

    @field_validator("submission_id")
    @classmethod
    def valid_submission_id(cls, value: str) -> str:
        return canonical_uuid(value)


class CourseSettingsRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=4000)
    difficulty: Literal["efficient", "proficient", "deep"]
    weekly_minutes: int = Field(ge=15, le=10_080)
    session_minutes: int = Field(ge=15, le=10_080)
    outline: str = Field(default="", max_length=20_000)
    role_family: str = Field(default="", max_length=64)
    target_level: str = Field(default="", max_length=64)
    interview_date: str = Field(default="", max_length=64)
    interview_focus: str = Field(default="", max_length=64)

    @model_validator(mode="after")
    def valid_pace(self) -> CourseSettingsRequest:
        if self.session_minutes > self.weekly_minutes:
            raise ValueError("Session minutes cannot exceed weekly minutes")
        return self


class CourseSettingsConfirmationRequest(CourseSettingsRequest):
    expected_payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    submission_id: str = Field(min_length=1, max_length=64)

    @field_validator("submission_id")
    @classmethod
    def valid_submission_id(cls, value: str) -> str:
        return canonical_uuid(value)


class CourseDeletionRequest(BaseModel):
    confirmation_slug: str = Field(min_length=1, max_length=64)
    confirmation_title: str = Field(min_length=1, max_length=200)
    topic_generation: str = Field(min_length=1, max_length=128)

    @field_validator("confirmation_slug")
    @classmethod
    def valid_confirmation_slug(cls, value: str) -> str:
        return canonical_slug(value)


class CourseGrowthRequest(BaseModel):
    action: Literal["practice", "deepen"]
    submission_id: str = Field(min_length=1, max_length=64)

    @field_validator("submission_id")
    @classmethod
    def valid_submission_id(cls, value: str) -> str:
        return canonical_uuid(value)


class FollowUpProposalRequest(BaseModel):
    action: Literal["generate", "retry", "confirm", "status"] = "generate"
    interests: str = Field(default="", max_length=2000)
    submission_id: str = Field(min_length=1, max_length=64)

    @field_validator("submission_id")
    @classmethod
    def valid_submission_id(cls, value: str) -> str:
        return canonical_uuid(value)


class TutorSubmissionRequest(BaseModel):
    intent: str
    text: str = Field(default="", max_length=32000)
    submission_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=0)
    source_lesson_id: str | None = Field(default=None, min_length=1, max_length=96)
    source_lesson_title: str | None = Field(default=None, min_length=1, max_length=160)
    source_lesson_revision: int | None = Field(default=None, ge=0)

    @field_validator("intent")
    @classmethod
    def allowed_intent(cls, value: str) -> str:
        if value not in {"answer", "question", "stuck", "skip", "next", "practice"}:
            raise ValueError("Unsupported learner intent")
        return value

    @field_validator("submission_id")
    @classmethod
    def valid_submission_id(cls, value: str) -> str:
        return canonical_uuid(value)


class ProgressionActionRequest(BaseModel):
    action: Literal["resume", "cancel"]
    operation_id: str

    @field_validator("operation_id")
    @classmethod
    def valid_operation_id(cls, value: str) -> str:
        return canonical_uuid(value)


class PlacementRequest(BaseModel):
    action: Literal[
        "start",
        "save_confidence",
        "confirm_outline",
        "preview_outline",
        "change_outline",
        "save_draft",
        "submit",
        "skip_stage",
        "skip",
        "restart",
    ]
    stage: str | None = Field(default=None, max_length=64)
    text: str = Field(default="", max_length=DRAFT_MAX_LENGTH)
    submission_id: str | None = Field(default=None, max_length=64)
    expected_updated_at: str | None = Field(default=None, max_length=64)
    role_family: str = Field(default="", max_length=64)
    target_level: str = Field(default="", max_length=64)
    interview_focus: str = Field(default="", max_length=64)
    ratings: dict[str, int] = Field(default_factory=dict)
    outline: str = Field(default="", max_length=12_000)
    expected_revision: int | None = Field(default=None, ge=0)
    interview_date: str | None = Field(default=None, max_length=64)
    weekly_minutes: int | None = Field(default=None, ge=1, le=10_080)
    session_minutes: int | None = Field(default=None, ge=1, le=10_080)
    pacing_posture_override: Literal["standard"] | None = None
    optional_skill_ids: list[str] | None = Field(default=None, max_length=128)

    @field_validator("submission_id")
    @classmethod
    def valid_optional_submission_id(cls, value: str | None) -> str | None:
        return canonical_uuid(value) if value is not None else None

    @model_validator(mode="after")
    def valid_confidence_survey(self) -> PlacementRequest:
        if self.action != "save_confidence":
            return self
        expected = {
            topic_id
            for topic_id, _label in confidence_topics_for_focus(self.interview_focus)
        }
        if set(self.ratings) != expected or any(
            isinstance(rating, bool) or rating not in range(1, 6)
            for rating in self.ratings.values()
        ):
            raise ValueError("Expected one 1-5 rating for every selected interview topic")
        return self


class ReviewGradeRequest(BaseModel):
    slug: str
    concept: str = Field(min_length=1, max_length=4000)
    due: str = Field(min_length=1, max_length=64)
    result: Literal["easy", "hard", "missed"]

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        return canonical_slug(value)


class DataManagementRequest(BaseModel):
    action: Literal["backup", "export", "restore", "move", "reset", "delete"]
    archive: str = Field(default="", max_length=4096)
    destination: str = Field(default="", max_length=4096)
    confirmation: str = Field(default="", max_length=128)
    include_credentials: bool = False
    credential_confirmation: str = Field(default="", max_length=128)

    @model_validator(mode="after")
    def valid_data_scope(self) -> DataManagementRequest:
        if not self.archive.strip():
            raise ValueError("A backup archive path is required")
        if self.action in {"restore", "move"} and not self.destination.strip():
            raise ValueError("A destination path is required")
        confirmations = {
            "move": MOVE_CONFIRMATION,
            "reset": RESET_CONFIRMATION,
            "delete": DELETE_CONFIRMATION,
        }
        required_confirmation = confirmations.get(self.action)
        if required_confirmation is not None and self.confirmation != required_confirmation:
            raise ValueError("The exact confirmation phrase is required")
        if self.include_credentials and self.credential_confirmation != CREDENTIAL_CONFIRMATION:
            raise ValueError("The exact credential confirmation phrase is required")
        return self


class VideoToolRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class CodeToolRequest(BaseModel):
    action: Literal["save", "run", "reset"]
    source: str = Field(default="", max_length=65536)
    expected_revision: str | None = Field(default=None, max_length=64)

    @field_validator("expected_revision")
    @classmethod
    def valid_revision(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("Invalid draft revision")
        return value


class FolderSourceRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class GitHubSourceRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


def public_mapping(value: Any) -> dict[str, Any]:
    """Convert an application DTO to a template-safe mapping."""

    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not key.startswith("_")}
    raise TypeError(f"Expected a structured application result, got {type(value).__name__}")
