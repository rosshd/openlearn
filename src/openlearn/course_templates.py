from __future__ import annotations

import importlib.resources
import json
import re
from dataclasses import dataclass
from importlib.resources.abc import Traversable

TEMPLATE_ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
TEMPLATE_FIELDS = frozenset({"name", "slug", "goal", "tags"})
OPTIONAL_TEMPLATE_FIELDS = frozenset(
    {
        "entry_mode",
        "units",
        "curriculum_bundle",
        "specializes_template_ids",
        "specializes_tags",
    }
)
TEMPLATE_ENTRY_MODES = frozenset({"interview_prep"})


class CourseTemplateError(ValueError):
    """A bundled course template is missing or invalid."""


class CourseTemplateNotFoundError(CourseTemplateError):
    """The requested bundled course template does not exist."""


@dataclass(frozen=True)
class CurriculumBundleReference:
    bundle_id: str
    bundle_version: str


@dataclass(frozen=True)
class CourseTemplate:
    name: str
    slug: str
    goal: str
    tags: tuple[str, ...]
    units: tuple[str, ...]
    entry_mode: str | None = None
    curriculum_bundle: CurriculumBundleReference | None = None
    specializes_template_ids: tuple[str, ...] = ()
    specializes_tags: tuple[str, ...] = ()


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
    templates = [_read_course_template(resource) for resource in resources]
    known_ids = {template.slug for template in templates}
    for template in templates:
        missing = sorted(set(template.specializes_template_ids) - known_ids)
        if missing:
            raise CourseTemplateError(
                f"invalid course template '{template.slug}.json': unknown specialized "
                f"template IDs {', '.join(missing)}"
            )
        if template.slug in template.specializes_template_ids:
            raise CourseTemplateError(
                f"invalid course template '{template.slug}.json': a template cannot "
                "specialize itself"
            )
    return templates


def load_course_template(template_id: str) -> CourseTemplate:
    template_id = validate_template_id(template_id)
    try:
        resource = template_resources().joinpath(f"{template_id}.json")
        exists = resource.is_file()
    except CourseTemplateError:
        raise
    except OSError as exc:
        raise CourseTemplateError(f"could not access course template '{template_id}'") from exc
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
        raise CourseTemplateError(f"could not access course template '{resource.name}'") from exc
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
    bundle_value = raw.get("curriculum_bundle")
    curriculum_bundle: CurriculumBundleReference | None = None
    if bundle_value is not None:
        if "units" in raw:
            raise CourseTemplateError(
                f"invalid course template '{resource.name}': curriculum_bundle "
                "cannot be combined with independent units"
            )
        if not isinstance(bundle_value, dict) or set(bundle_value) != {
            "bundle_id",
            "bundle_version",
        }:
            raise CourseTemplateError(
                f"invalid course template '{resource.name}': curriculum_bundle fields are invalid"
            )
        curriculum_bundle = CurriculumBundleReference(
            bundle_id=_required_string(
                bundle_value.get("bundle_id"), resource.name, "curriculum_bundle id"
            ),
            bundle_version=_required_string(
                bundle_value.get("bundle_version"),
                resource.name,
                "curriculum_bundle version",
            ),
        )
        from openlearn import interview_curriculum

        try:
            bundle = interview_curriculum.load_default_bundle()
        except interview_curriculum.CurriculumBundleError as exc:
            raise CourseTemplateError(
                f"invalid course template '{resource.name}': curriculum bundle is invalid"
            ) from exc
        if (
            bundle.bundle_id != curriculum_bundle.bundle_id
            or bundle.bundle_version != curriculum_bundle.bundle_version
        ):
            raise CourseTemplateError(
                f"invalid course template '{resource.name}': curriculum bundle is unavailable"
            )
        units = bundle.display_units("balanced")
    elif "units" in raw:
        units = _required_string_list(raw["units"], resource.name, "units")
    else:
        raise CourseTemplateError(
            f"invalid course template '{resource.name}': missing units or curriculum_bundle"
        )
    entry_mode = raw.get("entry_mode")
    if entry_mode is not None:
        entry_mode = _required_string(entry_mode, resource.name, "entry_mode")
        if entry_mode not in TEMPLATE_ENTRY_MODES:
            raise CourseTemplateError(
                f"invalid course template '{resource.name}': unsupported entry_mode '{entry_mode}'"
            )
    specializes_template_ids = _optional_string_list(
        raw.get("specializes_template_ids"),
        resource.name,
        "specializes_template_ids",
    )
    for template_id in specializes_template_ids:
        if not TEMPLATE_ID_PATTERN.fullmatch(template_id):
            raise CourseTemplateError(
                f"invalid course template '{resource.name}': specializes_template_ids "
                "must contain lowercase slugs"
            )
    specializes_tags = _optional_string_list(
        raw.get("specializes_tags"), resource.name, "specializes_tags"
    )
    if any(not TEMPLATE_ID_PATTERN.fullmatch(tag) for tag in specializes_tags):
        raise CourseTemplateError(
            f"invalid course template '{resource.name}': specializes_tags must "
            "contain unique lowercase slugs"
        )
    return CourseTemplate(
        name=name,
        slug=slug,
        goal=goal,
        tags=tags,
        units=units,
        entry_mode=entry_mode,
        curriculum_bundle=curriculum_bundle,
        specializes_template_ids=specializes_template_ids,
        specializes_tags=specializes_tags,
    )


def _required_string(value: object, filename: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CourseTemplateError(
            f"invalid course template '{filename}': {field} must be a trimmed non-empty string"
        )
    return value


def _required_string_list(value: object, filename: str, field: str) -> tuple[str, ...]:
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


def _optional_string_list(value: object, filename: str, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    return _required_string_list(value, filename, field)
