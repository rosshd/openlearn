from __future__ import annotations

import importlib.resources
import json
import re
from dataclasses import dataclass
from importlib.resources.abc import Traversable

TEMPLATE_ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
TEMPLATE_FIELDS = frozenset({"name", "slug", "goal", "tags", "units"})
OPTIONAL_TEMPLATE_FIELDS = frozenset({"entry_mode"})
TEMPLATE_ENTRY_MODES = frozenset({"interview_prep"})


class CourseTemplateError(ValueError):
    """A bundled course template is missing or invalid."""


class CourseTemplateNotFoundError(CourseTemplateError):
    """The requested bundled course template does not exist."""


@dataclass(frozen=True)
class CourseTemplate:
    name: str
    slug: str
    goal: str
    tags: tuple[str, ...]
    units: tuple[str, ...]
    entry_mode: str | None = None


def validate_template_id(template_id: str) -> str:
    if not isinstance(template_id, str) or not TEMPLATE_ID_PATTERN.fullmatch(template_id):
        raise CourseTemplateNotFoundError(
            f"invalid template ID '{template_id}'; use a slug such as 'python-basics'"
        )
    return template_id


def template_resources() -> Traversable:
    try:
        return importlib.resources.files("openlearn").joinpath("templates")
    except OSError as exc:
        raise CourseTemplateError("could not access bundled course templates") from exc


def available_course_templates() -> list[CourseTemplate]:
    try:
        root = template_resources()
        resources = sorted(
            (
                resource
                for resource in root.iterdir()
                if resource.is_file() and resource.name.endswith(".json")
            ),
            key=lambda resource: resource.name,
        )
    except CourseTemplateError:
        raise
    except OSError as exc:
        raise CourseTemplateError("could not access bundled course templates") from exc
    return [_read_course_template(resource) for resource in resources]


def load_course_template(template_id: str) -> CourseTemplate:
    template_id = validate_template_id(template_id)
    try:
        resource = template_resources().joinpath(f"{template_id}.json")
        exists = resource.is_file()
    except CourseTemplateError:
        raise
    except OSError as exc:
        raise CourseTemplateError(
            f"could not access course template '{template_id}'"
        ) from exc
    if not exists:
        raise CourseTemplateNotFoundError(f"template '{template_id}' not found")
    template = _read_course_template(resource)
    if template.slug != template_id:
        raise CourseTemplateError(
            f"invalid course template '{resource.name}': slug must match its filename"
        )
    return template


def _read_course_template(resource: Traversable) -> CourseTemplate:
    try:
        text = resource.read_text(encoding="utf-8")
    except OSError as exc:
        raise CourseTemplateError(
            f"could not access course template '{resource.name}'"
        ) from exc
    except UnicodeError as exc:
        raise CourseTemplateError(
            f"invalid course template '{resource.name}': expected UTF-8 JSON"
        ) from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CourseTemplateError(
            f"invalid course template '{resource.name}': expected UTF-8 JSON"
        ) from exc
    if not isinstance(raw, dict):
        raise CourseTemplateError(
            f"invalid course template '{resource.name}': expected a JSON object"
        )
    if not TEMPLATE_FIELDS.issubset(raw) or not set(raw).issubset(
        TEMPLATE_FIELDS | OPTIONAL_TEMPLATE_FIELDS
    ):
        missing = sorted(TEMPLATE_FIELDS - set(raw))
        unexpected = sorted(set(raw) - TEMPLATE_FIELDS - OPTIONAL_TEMPLATE_FIELDS)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected {', '.join(unexpected)}")
        raise CourseTemplateError(
            f"invalid course template '{resource.name}': {'; '.join(details)}"
        )

    name = _required_string(raw["name"], resource.name, "name")
    slug = _required_string(raw["slug"], resource.name, "slug")
    if not TEMPLATE_ID_PATTERN.fullmatch(slug):
        raise CourseTemplateError(
            f"invalid course template '{resource.name}': slug must be a lowercase slug"
        )
    if resource.name != f"{slug}.json":
        raise CourseTemplateError(
            f"invalid course template '{resource.name}': slug must match its filename"
        )
    goal = _required_string(raw["goal"], resource.name, "goal")
    tags = _required_string_list(raw["tags"], resource.name, "tags")
    units = _required_string_list(raw["units"], resource.name, "units")
    entry_mode = raw.get("entry_mode")
    if entry_mode is not None:
        entry_mode = _required_string(entry_mode, resource.name, "entry_mode")
        if entry_mode not in TEMPLATE_ENTRY_MODES:
            raise CourseTemplateError(
                f"invalid course template '{resource.name}': unsupported entry_mode "
                f"'{entry_mode}'"
            )
    return CourseTemplate(
        name=name,
        slug=slug,
        goal=goal,
        tags=tags,
        units=units,
        entry_mode=entry_mode,
    )


def _required_string(value: object, filename: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CourseTemplateError(
            f"invalid course template '{filename}': {field} must be a trimmed non-empty string"
        )
    return value


def _required_string_list(
    value: object, filename: str, field: str
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise CourseTemplateError(
            f"invalid course template '{filename}': {field} must be a non-empty string list"
        )
    items = tuple(_required_string(item, filename, field) for item in value)
    if len(set(items)) != len(items):
        raise CourseTemplateError(
            f"invalid course template '{filename}': {field} must not contain duplicates"
        )
    return items
