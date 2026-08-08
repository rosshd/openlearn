"""HTTP-boundary validation helpers."""

from __future__ import annotations

import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

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


class TutorSubmissionRequest(BaseModel):
    intent: str
    text: str = Field(default="", max_length=32000)
    submission_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=0)

    @field_validator("intent")
    @classmethod
    def allowed_intent(cls, value: str) -> str:
        if value not in {"answer", "question", "stuck", "skip", "next"}:
            raise ValueError("Unsupported learner intent")
        return value

    @field_validator("submission_id")
    @classmethod
    def valid_submission_id(cls, value: str) -> str:
        return canonical_uuid(value)


class PlacementRequest(BaseModel):
    action: Literal[
        "start", "save_draft", "submit", "skip_stage", "skip", "defer", "restart"
    ]
    stage: str | None = Field(default=None, max_length=64)
    text: str = Field(default="", max_length=40000)
    submission_id: str | None = Field(default=None, max_length=64)
    expected_updated_at: str | None = Field(default=None, max_length=64)

    @field_validator("submission_id")
    @classmethod
    def valid_optional_submission_id(cls, value: str | None) -> str | None:
        return canonical_uuid(value) if value is not None else None


class ReviewGradeRequest(BaseModel):
    slug: str
    concept: str = Field(min_length=1, max_length=4000)
    due: str = Field(min_length=1, max_length=64)
    result: Literal["easy", "hard", "missed"]

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        return canonical_slug(value)


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
