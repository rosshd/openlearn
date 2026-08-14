"""Concrete application-service adapter for the local web interface."""

from __future__ import annotations

import os
import re
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from openlearn import (
    application,
    cli,
    code_workspace,
    config,
    interview_prep,
    providers,
    source_imports,
    tutor_service,
    video_tools,
)
from openlearn import data_management
from openlearn.application import (
    CalibrationContext,
    CourseCard,
    CourseCreationRequest,
    CourseProgress,
)
from openlearn.course_templates import CourseTemplateError
from openlearn.courses import (
    CREATION_SUBMISSION_METADATA_KEY,
    CREATION_SUBMISSION_STATE_KEY,
    RouteAcceptanceConflictError,
    course_conversation_source,
)

from .schemas import (
    CodeToolRequest,
    CourseCreateRequest,
    DataManagementRequest,
    ProviderSetupRequest,
    PlacementRequest,
    ProgressionActionRequest,
    ReviewGradeRequest,
    TutorSubmissionRequest,
    VideoToolRequest,
)


COURSE_INITIALIZATION_PROMPT = "Start my first lesson."


def _is_course_initialization_prompt(value: object) -> bool:
    return isinstance(value, str) and (
        value == COURSE_INITIALIZATION_PROMPT
        or value.startswith("Start teaching unit 1 from this accepted course plan.")
    )


def _course_initialization_id(creation_submission_id: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"openlearn:course-initialization:{creation_submission_id}",
        )
    )


def _initialization_id_for_slug(slug: str) -> str | None:
    try:
        creation_submission_id = cli.load_state(slug).get(CREATION_SUBMISSION_STATE_KEY)
        if not isinstance(creation_submission_id, str):
            creation_submission_id = cli.read_topic(slug).metadata.get(
                CREATION_SUBMISSION_METADATA_KEY
            )
    except (cli.OpenLearnError, OSError):
        return None
    if not isinstance(creation_submission_id, str):
        return None
    return _course_initialization_id(creation_submission_id)


def _course_initialization_prompt(slug: str) -> str:
    """Build a real first-lesson prompt when an accepted course plan exists."""
    try:
        topic = cli.read_topic(slug)
    except cli.OpenLearnError:
        return COURSE_INITIALIZATION_PROMPT
    if cli.interview_profile_path(slug).exists():
        return COURSE_INITIALIZATION_PROMPT
    if topic.metadata.get("course_started") is not True:
        return COURSE_INITIALIZATION_PROMPT
    _context, log = cli.split_session_log(topic.body)
    plans = [entry for entry in cli.session_entries(log) if entry.get("kind") == "course_plan"]
    if not plans:
        return COURSE_INITIALIZATION_PROMPT
    return cli.first_lesson_prompt(str(plans[-1]["response"]))


def _card(card: CourseCard) -> dict[str, object]:
    interview = card.interview
    if interview is not None:
        return {
            "slug": card.slug,
            "title": card.title,
            "summary": card.goal,
            "current_unit": interview.position.unit_label,
            "next_move": interview.position.skill_label,
            "progress": interview.coverage.percent,
            "is_interview": True,
            "coverage": vars(interview.coverage),
            "readiness": vars(interview.readiness),
        }
    return {
        "slug": card.slug,
        "title": card.title,
        "summary": card.goal,
        "current_unit": card.current_focus or "Ready to learn",
        "next_move": card.current_focus,
        "progress": card.progress.percent,
    }


def _focus_progress(progress: CourseProgress) -> dict[str, object]:
    """Project mastery into a stable learner-facing Focus Bench shape."""
    total = max(0, progress.total)
    percent = max(0, min(100, progress.percent))
    if total == 0:
        return {
            "percent": 0,
            "summary": "Progress will appear after your first learning check.",
            "has_concepts": False,
        }
    known = max(0, min(total, progress.known))
    return {
        "percent": percent,
        "summary": f"{known} of {total} tracked concepts are known.",
        "has_concepts": True,
    }


def _interview_focus_projection(
    projection: application.InterviewLearningProjection,
) -> dict[str, object]:
    prompt = _plain_text(projection.pending_prompt) if projection.pending_prompt else ""
    answer = projection.committed_lesson.content
    presented = _without_check_section(answer) if prompt else answer
    response_kind, blocks = _present_response(presented)
    position = projection.position
    operation = projection.operation
    next_target = projection.next_target
    return {
        "slug": projection.slug,
        "title": projection.title,
        "current_unit": position.unit_label,
        "revision": projection.revision,
        "saved_state": "Saved locally",
        "is_interview": True,
        "lesson_id": projection.committed_lesson.lesson_id,
        "curriculum": {
            "position": vars(position),
            "next_target": vars(next_target) if next_target is not None else None,
            "deferred_skill": (
                vars(projection.deferred_skill)
                if projection.deferred_skill is not None
                else None
            ),
            "deferred_explanation": projection.deferred_explanation or "",
        },
        "move": {
            "kind": "Current lesson" if response_kind == "Lesson" else response_kind,
            "title": position.skill_label,
            "blocks": blocks,
            "content": (
                "Your first technical lesson is ready to begin."
                if not answer
                else ""
            ),
            "prompt": prompt,
            "position": (
                f"{position.unit_label} · {position.section_label} · "
                f"{position.emphasis}"
            ),
        },
        "progress": {
            "percent": projection.coverage.percent,
            "summary": projection.coverage.summary,
            "has_concepts": projection.coverage.total > 0,
            "coverage": vars(projection.coverage),
            "readiness": vars(projection.readiness),
        },
        "feedback": None,
        "requires_response": bool(prompt),
        "operation": (
            {
                "id": operation.submission_id,
                "state": operation.state,
                "message": operation.message,
                "actions": list(operation.actions),
                "error": operation.message if operation.state == "provider-error" else "",
                "error_code": operation.error_code or "",
                "show_provider_recovery": "provider-settings" in operation.actions,
            }
            if operation.state != "committed"
            else None
        ),
        "caught_up": operation.state == "caught-up",
        "saved_response": projection.saved_response,
        "initialization": None,
    }


def _lesson_focus_title(
    topic: cli.Topic,
    response: str,
) -> str:
    response_focus = cli.tutor_response_focus_title(response)
    if response_focus:
        return response_focus
    saved_focus = topic.metadata.get("current_focus")
    if isinstance(saved_focus, str) and saved_focus.strip():
        return saved_focus.strip()
    current_unit = topic.metadata.get("current_unit")
    if isinstance(current_unit, int):
        unit = cli.course_unit_at(topic.metadata, current_unit)
        title = unit.get("title") if isinstance(unit, dict) else None
        if isinstance(title, str) and title.strip():
            return title.strip()
    template_units = topic.metadata.get("template_units")
    if isinstance(template_units, list):
        first = next(
            (item.strip() for item in template_units if isinstance(item, str) and item.strip()),
            "",
        )
        match = re.match(r"(?i)^Unit\s+\d+\s*:\s*(.*?)(?:\s+-\s+.*)?$", first)
        if match and match.group(1).strip():
            return match.group(1).strip()
    return str(topic.metadata.get("topic") or "Learning focus").strip()


def _draft(snapshot: code_workspace.DraftSnapshot) -> dict[str, object]:
    return {
        "language": snapshot.language,
        "source": snapshot.source,
        "revision": snapshot.revision,
    }


def _execution(result: code_workspace.ExecutionResult) -> dict[str, object]:
    return {
        "status": result.status,
        "kind": result.status,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timed_out": result.timed_out,
        "exit_code": result.exit_code,
        "signal": result.signal,
        "duration_seconds": result.duration_seconds,
        "limit_reason": result.limit_reason,
        "isolation": result.isolation,
        "runtime": result.runtime,
        "protections": list(result.protections),
        "draft_revision": result.draft_revision,
    }


def _source_result(result: source_imports.CourseSourceImportResult) -> dict[str, object]:
    def source_item(source: source_imports.CourseSource) -> dict[str, object]:
        return {
            "id": source.source_id,
            "kind": source.kind,
            "label": source.label,
            "status": "available locally",
        }

    def details(values: tuple[source_imports.ImportDetail, ...]) -> list[dict[str, object]]:
        return [
            {
                "status": value.status,
                "label": value.label,
                "context_file": value.context_file,
                "message": value.message or "",
            }
            for value in values
        ]

    imported = details(result.imported)
    skipped = details(result.skipped)
    failed = details(result.failed)
    return {
        "ok": not failed,
        "kind": result.kind,
        "sources": [source_item(source) for source in result.sources],
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "message": (
            f"Imported {len(imported)}, skipped {len(skipped)}, failed {len(failed)}."
        ),
    }


def _move(move: tutor_service.TutorMove | None) -> dict[str, object]:
    if move is None:
        return {}
    kind, blocks = _present_response(move.content)
    return {
        "kind": kind or move.kind.replace("_", " ").title(),
        "title": "Your next learning move",
        "content": move.content,
        "blocks": blocks,
        "prompt": move.prompt,
        "position": f"Step {move.revision}",
    }


def _operation_preview(value: str | None) -> str:
    if not value:
        return ""
    text = cli.sanitize_stream_preview(value)
    text = re.sub(
        r"^\s*(?:\*\*)?(?:Lesson|Feedback|Example|Check|Hint|Next)\s*:(?:\*\*)?\s*",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    return _plain_text(text)


def _show_provider_recovery(error_code: str | None) -> bool:
    return error_code in {
        "judge_invalid_output",
        "provider_credentials",
        "provider_rate_limited",
        "provider_unavailable",
    }


def _plain_text(value: str) -> str:
    text = value.replace("**", "").replace("__", "")
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"(?<!\w)([*_])([^\n]+?)\1(?!\w)", r"\2", text)
    return text.strip()


_CHECK_SECTION = re.compile(
    r"^\s*(?:\*\*)?Check\s*:(?:\*\*)?\s*.*?"
    r"(?=^\s*(?:\*\*)?(?:Lesson|Feedback|Example|Hint|Next)\s*:(?:\*\*)?|\Z)",
    flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def _without_check_section(value: str) -> str:
    """Remove the response's Check section when the pending prompt owns it."""
    return _CHECK_SECTION.sub("", value).strip()


def _present_response(value: str) -> tuple[str, list[dict[str, object]]]:
    """Parse a small safe Markdown subset into explicit presentation blocks."""
    text = cli.strip_tutor_enter_advance_cue(cli.sanitize_model_output(value))
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    lines = text.splitlines()
    blocks: list[dict[str, object]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        fence = re.match(r"^\s*```([A-Za-z0-9_+.-]*)\s*$", line)
        if fence:
            index += 1
            code: list[str] = []
            while index < len(lines) and not re.match(r"^\s*```\s*$", lines[index]):
                code.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.append(
                {
                    "kind": "code",
                    "text": "\n".join(code),
                    "language": fence.group(1),
                }
            )
            continue
        unordered = re.match(r"^\s*[-*+]\s+(.+)$", line)
        ordered = re.match(r"^\s*\d+[.)]\s+(.+)$", line)
        if unordered or ordered:
            kind = "unordered_list" if unordered else "ordered_list"
            pattern = r"^\s*[-*+]\s+(.+)$" if unordered else r"^\s*\d+[.)]\s+(.+)$"
            items: list[str] = []
            while index < len(lines):
                item = re.match(pattern, lines[index])
                if item is None:
                    break
                items.append(_plain_text(item.group(1)))
                index += 1
            blocks.append({"kind": kind, "items": items})
            continue
        paragraph: list[str] = []
        while index < len(lines):
            current = lines[index]
            if not current.strip():
                break
            if paragraph and (
                re.match(r"^\s*```", current)
                or re.match(r"^\s*[-*+]\s+", current)
                or re.match(r"^\s*\d+[.)]\s+", current)
            ):
                break
            paragraph.append(re.sub(r"^#{1,6}\s+", "", current.strip()))
            index += 1
        blocks.append({"kind": "paragraph", "text": _plain_text("\n".join(paragraph))})

    first = blocks[0].get("text", "") if blocks and blocks[0]["kind"] == "paragraph" else ""
    label = "Lesson"
    match = re.match(r"^([A-Za-z][A-Za-z ]{1,30}):\s*(.*)$", str(first), flags=re.DOTALL)
    if match:
        label = match.group(1).strip().title()
        remainder = match.group(2).strip()
        if remainder:
            blocks[0]["text"] = remainder
        else:
            blocks.pop(0)
    return label, blocks


def _side_conversation(entries: list[dict[str, str]]) -> list[dict[str, object]]:
    selected: list[dict[str, str]] = []
    for entry in reversed(entries):
        if entry.get("kind") != cli.SIDE_CHAT_SESSION_KIND:
            continue
        selected.append(entry)
        if len(selected) == 20:
            break
    conversation: list[dict[str, object]] = []
    for entry in reversed(selected):
        _kind, blocks = _present_response(str(entry.get("response") or ""))
        conversation.append(
            {
                "question": _plain_text(str(entry.get("prompt") or "")),
                "blocks": blocks,
                "source_lesson_id": str(entry.get("source_lesson_id") or ""),
                "source_lesson_title": _plain_text(
                    str(entry.get("source_lesson_title") or "Saved lesson")
                ),
            }
        )
    return conversation


class OpenLearnWebServices:
    """Map interface-neutral openlearn services to browser view models."""

    def __init__(self) -> None:
        self._validated_provider_fingerprint = ""

    def provider_status(self) -> dict[str, object]:
        try:
            status = application.provider_status()
            saved_config = config.read_config()
        except config.ConfigError:
            selected = providers.PROVIDER_PRESETS["openrouter"]
            return {
                "base_url": selected.base_url or "",
                "model": selected.default_model or "",
                "key_required": True,
                "key_configured": False,
                "verified": False,
                "managed_fields": [],
                "ready": False,
                "managed": False,
                "reason": "The saved provider settings are unreadable. Replace them below.",
                "selected_provider": selected.slug,
                "form_base_url": selected.base_url or "",
                "form_model": selected.default_model or "",
                "providers": self._provider_options(),
            }
        mock_ready = os.environ.get("OPENLEARN_MOCK") in {"1", "true", "yes"}
        ready = mock_ready or status.ready
        reason = ""
        if not ready:
            if status.key_required and not status.key_configured:
                reason = "Add an API key, then test the connection."
            elif not status.verified:
                reason = "Test this provider before starting a lesson."
        fresh_setup = not status.managed_fields and not saved_config
        if fresh_setup:
            selected = providers.PROVIDER_PRESETS["openrouter"]
            selected_provider = selected.slug
            form_base_url = selected.base_url or ""
            form_model = selected.default_model or ""
        else:
            selected = providers.preset_for_base_url(status.base_url)
            selected_provider = (
                selected.slug
                if selected.slug in providers.PROVIDER_PRESETS
                else "custom"
            )
            form_base_url = status.base_url
            form_model = status.model
        return {
            **status.as_dict(),
            "ready": ready,
            "managed": bool(status.managed_fields),
            "reason": reason,
            "selected_provider": selected_provider,
            "form_base_url": form_base_url,
            "form_model": form_model,
            "providers": self._provider_options(),
        }

    def ensure_provider_ready(self) -> dict[str, object]:
        """Validate complete saved credentials before asking the learner to re-enter them."""
        status = self.provider_status()
        if status.get("ready") or not status.get("key_configured"):
            return status
        try:
            credentials = config.effective_provider_credentials()
        except config.ConfigError:
            return status
        fingerprint = sha256(
            "\0".join(
                (
                    credentials.base_url,
                    credentials.model,
                    credentials.api_key or "",
                )
            ).encode("utf-8")
        ).hexdigest()
        if status.get("managed") and fingerprint == self._validated_provider_fingerprint:
            return {**status, "ready": True, "verified": True, "reason": ""}
        validation = providers.validate_provider(credentials.base_url, credentials.api_key)
        if validation.status is providers.ValidationStatus.VALID:
            validation = providers.validate_provider_model(
                credentials.base_url,
                credentials.api_key,
                credentials.model,
            )
        if validation.status is not providers.ValidationStatus.VALID:
            return status
        if status.get("managed"):
            self._validated_provider_fingerprint = fingerprint
            return {**status, "ready": True, "verified": True, "reason": ""}
        try:
            providers.persist_validation_result(
                base_url=credentials.base_url,
                model=credentials.model,
                api_key=credentials.api_key,
                validation=validation,
            )
        except (providers.ProviderConfigurationError, config.ConfigError):
            return status
        cli.clear_config_cache()
        return self.provider_status()

    @staticmethod
    def _provider_options() -> list[dict[str, object]]:
        explanations = {
            "openrouter": "One key for many models. Recommended for an inexpensive, flexible start.",
            "openai": "Use an OpenAI API account and direct OpenAI billing.",
            "anthropic-compatible": "Use a gateway that exposes an Anthropic-compatible model through an OpenAI-style endpoint.",
            "ollama": "Run a model locally. No API key is needed.",
            "custom": "Connect another OpenAI-compatible endpoint.",
        }
        return [
            {
                "id": preset.slug,
                "name": preset.name,
                "base_url": preset.base_url,
                "default_model": preset.default_model,
                "recommended": preset.slug == "openrouter",
                "key_required": preset.key_required,
                "setup_url": preset.setup_url or "",
                "explanation": explanations[preset.slug],
            }
            for preset in providers.PROVIDER_PRESETS.values()
        ]

    def configure_provider(self, request: ProviderSetupRequest) -> dict[str, object]:
        self._validated_provider_fingerprint = ""
        preset = providers.PROVIDER_PRESETS.get(request.provider)
        if preset is None:
            return {"ok": False, "error": "Choose a supported model provider."}
        base_url = request.base_url.strip() or preset.base_url or ""
        model = request.model.strip() or preset.default_model or ""
        api_key = request.api_key.strip() or None
        if not base_url or not model:
            return {"ok": False, "error": "Add a base URL and model for this provider."}
        if api_key is None:
            try:
                current = config.effective_provider_credentials()
            except config.ConfigError:
                current = None
            if current is not None and current.base_url == base_url.rstrip("/"):
                api_key = current.api_key
        validation = (
            providers.ValidationResult(providers.ValidationStatus.VALID)
            if os.environ.get("OPENLEARN_MOCK") in {"1", "true", "yes"}
            else providers.validate_provider(base_url, api_key)
        )
        if (
            validation.status is providers.ValidationStatus.VALID
            and os.environ.get("OPENLEARN_MOCK") not in {"1", "true", "yes"}
        ):
            validation = providers.validate_provider_model(base_url, api_key, model)
        try:
            providers.persist_validation_result(
                base_url=base_url,
                model=model,
                api_key=api_key,
                validation=validation,
                allow_unverified=request.save_unverified,
            )
        except (providers.ProviderConfigurationError, config.ConfigError) as error:
            if validation.detail == "model_unavailable":
                return {
                    "ok": False,
                    "retain_secret": False,
                    "error": "That model is not available from this provider.",
                }
            messages = {
                "rejected_provider_credentials": "That API key was rejected. Check it and try again.",
                "unverified_provider_requires_explicit_confirmation": (
                    "The provider could not be reached. Retry, or explicitly save it unverified."
                ),
                "provider_validation_failed": "The provider returned an unexpected response.",
            }
            return {
                "ok": False,
                "retain_secret": validation.status
                is providers.ValidationStatus.NETWORK_ERROR,
                "error": messages.get(
                    str(error), "The provider configuration could not be saved."
                ),
            }
        cli.clear_config_cache()
        status = self.provider_status()
        if not status["ready"]:
            return {
                "ok": True,
                **status,
                "requires_validation": True,
                "message": (
                    "Saved locally, but teaching stays locked until the provider "
                    "connection is validated. Retry the connection test when it is available."
                ),
            }
        return {"ok": True, **status}

    def dashboard(self) -> dict[str, object]:
        snapshot = application.dashboard()
        courses = [_card(card) for card in snapshot.courses]
        courses_by_slug = {str(card["slug"]): card for card in courses}
        return {
            "courses": courses,
            "active_course": (
                courses_by_slug.get(snapshot.resume.slug) if snapshot.resume else None
            ),
            "due_reviews": snapshot.reviews.due_today,
        }

    def course_templates(self) -> list[dict[str, object]]:
        templates = [
            {
                "id": template.template_id,
                "title": template.name,
                "description": template.goal,
                "tags": list(template.tags),
                "entry_mode": template.entry_mode,
            }
            for template in application.templates().templates
        ]
        priority = {
            "technical-interview-prep": 0,
            "networking": 1,
            "vim": 2,
        }
        return sorted(
            templates,
            key=lambda template: (
                priority.get(str(template["id"]), len(priority)),
                str(template["title"]).casefold(),
            ),
        )

    def course_entry_mode(self, template_id: str | None) -> str | None:
        if template_id is None:
            return None
        return next(
            (
                template.entry_mode
                for template in application.templates().templates
                if template.template_id == template_id
            ),
            None,
        )

    def prepare_video(self, slug: str, request: VideoToolRequest) -> dict[str, object]:
        try:
            cli.read_topic(slug)
        except (cli.OpenLearnError, OSError):
            return {"ok": False, "missing": True, "error": "Course not found."}
        try:
            descriptor = video_tools.describe_youtube_video(request.url)
        except video_tools.VideoURLValidationError as error:
            return {"ok": False, "error": str(error)}
        return {
            "ok": True,
            "provider": descriptor.provider,
            "video_id": descriptor.video_id,
            "canonical_url": descriptor.canonical_url,
            "embed_url": descriptor.embed_url,
            "requires_consent": descriptor.requires_consent,
            "label": "YouTube video ready to load",
        }

    @staticmethod
    def _workspace() -> code_workspace.CodeWorkspace:
        return code_workspace.CodeWorkspace(cli.topics_dir())

    def code_state(self, slug: str) -> dict[str, object]:
        try:
            workspace = self._workspace()
            state = workspace.state(slug)
            return {
                "ok": True,
                **_draft(state.draft),
                **({"result": _execution(state.result)} if state.result is not None else {}),
            }
        except code_workspace.CodeWorkspaceNotFoundError:
            return {"ok": False, "missing": True, "error": "Code workspace unavailable."}
        except code_workspace.CodeWorkspaceError:
            return {"ok": False, "unavailable": True, "error": "Code workspace unavailable."}

    def update_code(self, slug: str, request: CodeToolRequest) -> dict[str, object]:
        workspace = self._workspace()
        try:
            if request.action == "reset":
                snapshot = workspace.reset(
                    slug,
                    expected_revision=request.expected_revision,
                )
                return {"ok": True, **_draft(snapshot), "message": "Draft reset."}
            snapshot = workspace.save(
                slug,
                request.source,
                expected_revision=request.expected_revision,
            )
            if request.action == "save":
                return {"ok": True, **_draft(snapshot), "message": "Draft saved locally."}
            result = workspace.run(slug, expected_revision=snapshot.revision)
            return {
                "ok": True,
                **_draft(snapshot),
                "result": _execution(result),
                "message": (
                    "Secure runner setup is required before execution."
                    if result.status == "runner_unavailable"
                    else "Run complete."
                ),
            }
        except code_workspace.CodeWorkspaceConflictError as error:
            return {"ok": False, "conflict": True, "error": str(error)}
        except code_workspace.CodeWorkspaceValidationError as error:
            return {"ok": False, "invalid": True, "error": str(error)}
        except code_workspace.CodeWorkspaceNotFoundError:
            return {"ok": False, "missing": True, "error": "Code workspace unavailable."}
        except code_workspace.CodeWorkspaceError:
            return {"ok": False, "unavailable": True, "error": "Code workspace unavailable."}

    def course_sources(self, slug: str) -> dict[str, object]:
        try:
            sources = source_imports.list_course_sources(slug)
        except (cli.OpenLearnError, OSError):
            return {"ok": False, "missing": True, "error": "Course not found."}
        return {
            "ok": True,
            "sources": [
                {
                    "id": source.source_id,
                    "kind": source.kind,
                    "label": source.label,
                    "status": "available locally",
                }
                for source in sources
            ],
        }

    def import_file_source(
        self, slug: str, path: Path, filename: str
    ) -> dict[str, object]:
        return self._import_source(
            slug,
            source_imports.LocalFileSource(path=path, filename=filename),
        )

    def import_folder_source(self, slug: str, path: str) -> dict[str, object]:
        return self._import_source(slug, source_imports.LocalFolderSource(Path(path)))

    def import_github_source(self, slug: str, url: str) -> dict[str, object]:
        return self._import_source(slug, source_imports.PublicGitHubSource(url))

    @staticmethod
    def _import_source(
        slug: str, source: source_imports.CourseSourceInput
    ) -> dict[str, object]:
        try:
            result = source_imports.import_course_source(
                source_imports.CourseSourceImportRequest(
                    course_slug=slug,
                    source=source,
                    model=None,
                )
            )
        except (cli.OpenLearnError, OSError):
            return {"ok": False, "missing": True, "error": "Course not found."}
        return _source_result(result)

    def create_course(self, request: CourseCreateRequest) -> dict[str, object]:
        calibration = CalibrationContext(
            goal=request.goal,
            experience=request.experience,
            skipped=not bool(request.experience.strip()),
            recorded_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            result = application.create_course(
                CourseCreationRequest(
                    name=request.title,
                    goal=request.goal,
                    template_id=request.template_id or None,
                    calibration=calibration,
                    submission_id=request.submission_id,
                )
            )
        except (cli.OpenLearnError, CourseTemplateError) as error:
            return {"ok": False, "error": str(error)}
        slug = result.course.slug
        initialization_id = _course_initialization_id(request.submission_id)
        if self.course_entry_mode(result.course.card.template_id) == "interview_prep":
            return {
                "ok": True,
                "slug": slug,
                "created": result.created,
                "state": "placement_recommended",
            }
        return self._start_course_initialization(
            slug, initialization_id, created=result.created
        )

    def _start_course_initialization(
        self,
        slug: str,
        initialization_id: str | None = None,
        *,
        created: bool | None = None,
    ) -> dict[str, object]:
        initialization_id = initialization_id or _initialization_id_for_slug(slug)
        if initialization_id is None:
            return {"ok": False, "error": "Course initialization is unavailable."}
        interview_course = cli.interview_profile_path(slug).exists()
        if interview_course:
            try:
                application.prepare_interview_curriculum(
                    slug, boundary="preparation"
                )
            except (cli.OpenLearnError, ValueError) as error:
                return {"ok": False, "error": str(error)}
        existing_operation = tutor_service.operation_status(slug, initialization_id)
        if existing_operation is not None:
            result: dict[str, object] = {
                "ok": True,
                "slug": slug,
                "operation_id": initialization_id,
                "state": existing_operation.status,
            }
            if created is not None:
                result["created"] = created
            return result
        try:
            operation = tutor_service.start_turn(
                slug,
                _course_initialization_prompt(slug),
                intent=("navigation" if interview_course else "question"),
                submission_id=initialization_id,
                expected_revision=tutor_service.course_revision(slug),
                model=config.configured_model(),
                progression_intent=("continue" if interview_course else None),
            )
        except tutor_service.TutorOperationError:
            operation = tutor_service.operation_status(slug, initialization_id)
        state = operation.status if operation is not None else "retryable_error"
        result = {
            "ok": True,
            "slug": slug,
            "operation_id": initialization_id,
            "state": state,
        }
        if created is not None:
            result["created"] = created
        return result

    def start_course_initialization(self, slug: str) -> dict[str, object]:
        return self._start_course_initialization(slug)

    @staticmethod
    def _placement_view(slug: str, value: dict[str, object]) -> dict[str, object]:
        placement = value["placement"]
        assert isinstance(placement, dict)
        draft = placement.get("draft")
        lifecycle = placement.get("lifecycle_version")
        if lifecycle == interview_prep.PLACEMENT_V4:
            survey = placement.get("survey")
            survey_value = survey if isinstance(survey, dict) else None
            route_preview = (
                interview_prep.preview_curriculum_change(
                    value,
                    current_date=datetime.now(timezone.utc).date(),
                )
                if survey_value is not None
                else None
            )
            return {
                "slug": slug,
                "status": placement.get("status"),
                "lifecycle_version": lifecycle,
                "next_stage": placement.get("next_stage"),
                "updated_at": placement.get("updated_at"),
                "attempt_id": placement.get("attempt_id"),
                "survey": survey_value,
                "topics": [
                    {
                        "id": topic_id,
                        "label": label,
                        "track": "coding",
                        "rating": int(survey_value.get("ratings", {}).get(topic_id, 1))
                        if isinstance(survey_value, dict)
                        and isinstance(survey_value.get("ratings"), dict)
                        else 1,
                    }
                    for topic_id, label in interview_prep.CONFIDENCE_PATTERNS
                ]
                + [
                    {
                        "id": topic_id,
                        "label": label,
                        "track": "system_design",
                        "rating": int(survey_value.get("ratings", {}).get(topic_id, 1))
                        if isinstance(survey_value, dict)
                        and isinstance(survey_value.get("ratings"), dict)
                        else 1,
                    }
                    for topic_id, label in interview_prep.SYSTEM_DESIGN_TOPICS
                ],
                "scale": [
                    {"value": value, "label": label}
                    for value, label in interview_prep.CONFIDENCE_SCALE
                ],
                "roles": [
                    {"value": value, "label": label}
                    for value, label in interview_prep.CONFIDENCE_ROLES
                ],
                "levels": [
                    {"value": value, "label": label}
                    for value, label in interview_prep.CONFIDENCE_LEVELS
                ],
                "focuses": [
                    {"value": value, "label": label}
                    for value, label in interview_prep.CONFIDENCE_FOCUSES
                ],
                "outline": (
                    str(route_preview.get("outline") or "")
                    if route_preview is not None
                    else ""
                ),
                "outline_items": (
                    route_preview["outline_items"]
                    if route_preview is not None
                    else []
                ),
                "locked_prerequisites": (
                    route_preview["locked_prerequisites"]
                    if route_preview is not None
                    else []
                ),
                "confidence_topics": (
                    route_preview["confidence_topics"]
                    if route_preview is not None
                    else []
                ),
                "optional_choices": (
                    route_preview["optional_choices"]
                    if route_preview is not None
                    else []
                ),
                "profile": value.get("profile") if isinstance(value.get("profile"), dict) else {},
                "pacing_posture_override": (
                    value.get("curriculum_allocation", {}).get("pacing_posture_override")
                    if isinstance(value.get("curriculum_allocation"), dict)
                    else None
                ),
                "feedback": None,
                "course_revision": tutor_service.course_revision(slug),
            }
        problem = interview_prep.PLACEMENT_PROBLEM
        examples = problem["examples"]
        assert isinstance(examples, list)
        stages = interview_prep.placement_stages(placement)
        next_stage = placement.get("next_stage")
        return {
            "slug": slug,
            "status": placement.get("status"),
            "next_stage": next_stage,
            "updated_at": placement.get("updated_at"),
            "attempt_id": placement.get("attempt_id"),
            "draft": draft if isinstance(draft, dict) else None,
            "step": stages.index(next_stage) + 1 if next_stage in stages else len(stages),
            "step_count": len(stages),
            "problem": {
                "title": problem["title"],
                "prompt": problem["prompt"],
                "examples": examples,
            },
            "contract": list(interview_prep.PLACEMENT_CONTRACT),
            "feedback": interview_prep.placement_feedback(placement),
        }

    def _interview_course(self, slug: str) -> application.CourseSnapshot | None:
        try:
            snapshot = application.course(slug)
        except (cli.OpenLearnError, OSError):
            return None
        if self.course_entry_mode(snapshot.card.template_id) != "interview_prep":
            return None
        return snapshot

    def placement(self, slug: str) -> dict[str, object]:
        snapshot = self._interview_course(slug)
        if snapshot is None:
            return {"slug": slug, "missing": True}
        try:
            saved = interview_prep.load_profile(cli.interview_profile_path(slug))
            saved_placement = saved.get("placement")
            if (
                isinstance(saved_placement, dict)
                and saved_placement.get("lifecycle_version") == interview_prep.PLACEMENT_V4
            ):
                value = saved
            else:
                value = application.sync_interview_placement(slug)
        except cli.OpenLearnError:
            value = interview_prep.load_profile(cli.interview_profile_path(slug))
        return {"title": snapshot.card.title, **self._placement_view(slug, value)}

    def interview_placement_exists(self, slug: str) -> bool:
        return self._interview_course(slug) is not None

    def update_placement(self, slug: str, request: PlacementRequest) -> dict[str, object]:
        if self._interview_course(slug) is None:
            return {"slug": slug, "missing": True}
        path = cli.interview_profile_path(slug)
        value = interview_prep.load_profile(path)
        placement = value["placement"]
        assert isinstance(placement, dict)
        if request.action == "start":
            if (
                placement.get("status") == "in_progress"
                and placement.get("lifecycle_version") != interview_prep.PLACEMENT_V4
            ):
                application.discard_interview_placement(slug)
            with cli.interview_profile_write_lock(slug):
                value = interview_prep.start_confidence_placement(path)
            cli.log_event(slug, "interview_confidence_placement_started", {})
        elif request.action == "restart":
            if (
                placement.get("status") == "in_progress"
                and placement.get("lifecycle_version") != interview_prep.PLACEMENT_V4
            ):
                application.discard_interview_placement(slug)
            with cli.interview_profile_write_lock(slug):
                value = interview_prep.start_confidence_placement(path, restart=True)
            cli.log_event(slug, "interview_confidence_placement_restarted", {})
        elif request.action == "save_confidence":
            try:
                with cli.interview_profile_write_lock(slug):
                    value = interview_prep.save_confidence_survey(
                        path,
                        role_family=request.role_family,
                        target_level=request.target_level,
                        interview_focus=request.interview_focus,
                        ratings=request.ratings,
                    )
            except ValueError as error:
                return {"invalid": True, "error": str(error)}
            cli.log_event(
                slug,
                "interview_confidence_profile_saved",
                {
                    "role_family": request.role_family,
                    "target_level": request.target_level,
                    "interview_focus": request.interview_focus,
                },
            )
        elif request.action in {"preview_outline", "change_outline"}:
            changes: dict[str, object] = {}
            for field in ("role_family", "target_level", "interview_focus"):
                field_value = getattr(request, field)
                if field_value:
                    changes[field] = field_value
            if request.interview_date is not None:
                changes["interview_date"] = request.interview_date
            if request.weekly_minutes is not None:
                changes["weekly_minutes"] = request.weekly_minutes
            if request.session_minutes is not None:
                changes["session_minutes"] = request.session_minutes
            if request.ratings:
                changes["confidence_ratings"] = request.ratings
            if "pacing_posture_override" in request.model_fields_set:
                changes["pacing_posture_override"] = request.pacing_posture_override
            if request.optional_skill_ids is not None:
                changes["optional_skill_ids"] = request.optional_skill_ids
            try:
                if request.action == "preview_outline":
                    return {
                        "state": "preview",
                        **application.preview_interview_curriculum_change(
                            slug, changes=changes
                        ),
                    }
                accepted = application.accept_interview_curriculum(
                    slug,
                    action="change",
                    changes=changes,
                    submission_id=request.submission_id,
                    expected_revision=request.expected_revision,
                )
                return {
                    "state": "changed",
                    "receipt": accepted["receipt"],
                    **self._placement_view(slug, accepted["profile"]),
                }
            except RouteAcceptanceConflictError as error:
                return {"state": "conflict", "error": str(error)}
            except (ValueError, cli.OpenLearnError) as error:
                return {"invalid": True, "error": str(error)}
        elif request.action == "confirm_outline":
            outline = request.outline.strip()
            try:
                changes: dict[str, object] = {}
                if request.role_family:
                    changes["role_family"] = request.role_family
                if request.target_level:
                    changes["target_level"] = request.target_level
                if request.interview_focus:
                    changes["interview_focus"] = request.interview_focus
                if request.interview_date is not None:
                    changes["interview_date"] = request.interview_date
                if request.weekly_minutes is not None:
                    changes["weekly_minutes"] = request.weekly_minutes
                if request.session_minutes is not None:
                    changes["session_minutes"] = request.session_minutes
                if request.ratings:
                    changes["confidence_ratings"] = request.ratings
                if "pacing_posture_override" in request.model_fields_set:
                    changes["pacing_posture_override"] = request.pacing_posture_override
                if request.optional_skill_ids is not None:
                    changes["optional_skill_ids"] = request.optional_skill_ids
                accepted = application.accept_interview_curriculum(
                    slug,
                    action="confirm",
                    changes=changes,
                    outline=outline,
                    submission_id=request.submission_id,
                    expected_revision=request.expected_revision,
                )
                value = accepted["profile"]
            except RouteAcceptanceConflictError as error:
                return {"state": "conflict", "error": str(error)}
            except (ValueError, cli.OpenLearnError) as error:
                return {"invalid": True, "error": str(error)}
        else:
            if placement.get("lifecycle_version") != interview_prep.PLACEMENT_V3:
                return {
                    "invalid": True,
                    "error": "This placement uses the quick confidence format.",
                }
            stage = request.stage
            if request.action == "submit" and isinstance(stage, str):
                try:
                    prior_evidence_id = interview_prep.placement_evidence_id(placement, stage)
                except ValueError:
                    prior_evidence_id = ""
                references = placement.get("evidence_refs")
                if isinstance(references, list) and any(
                    isinstance(reference, dict)
                    and reference.get("evidence_id") == prior_evidence_id
                    for reference in references
                ):
                    return self._placement_view(slug, value)
            if not isinstance(stage, str) or stage != placement.get("next_stage"):
                return {"state": "conflict", "error": "Placement changed elsewhere. Reload to continue."}
            if request.action == "save_draft":
                lines = [line.strip() for line in request.text.splitlines() if line.strip()]
                if not lines:
                    return {"invalid": True, "error": "Add at least one line before saving."}
                with cli.interview_profile_write_lock(slug):
                    locked = interview_prep.load_profile(path)["placement"]
                    assert isinstance(locked, dict)
                    if (
                        request.expected_updated_at != locked.get("updated_at")
                        or stage != locked.get("next_stage")
                    ):
                        return {
                            "state": "conflict",
                            "error": "Placement changed elsewhere. Reload to continue.",
                        }
                    value = interview_prep.replace_placement_draft_lines(path, stage, lines)
            elif request.action == "submit":
                draft = placement.get("draft")
                lines = draft.get("lines") if isinstance(draft, dict) else None
                if not isinstance(lines, list) or not lines:
                    return {"invalid": True, "error": "Save at least one draft line before submitting."}
                evidence_id = interview_prep.placement_evidence_id(placement, stage)
                value = application.record_interview_placement_response(
                    slug,
                    stage=stage,
                    response="\n".join(str(line) for line in lines),
                    evidence_id=evidence_id,
                )
                if value is None:
                    return {"state": "conflict", "error": "Placement changed elsewhere. Reload to continue."}
            else:
                with cli.interview_profile_write_lock(slug):
                    value = interview_prep.skip_optional_placement_stage(path, stage)
                value = application.sync_interview_placement(slug)
        return self._placement_view(slug, value)

    def skip_placement(
        self, slug: str, request: PlacementRequest | None = None
    ) -> dict[str, object]:
        if self._interview_course(slug) is None:
            return {"slug": slug, "missing": True}
        path = cli.interview_profile_path(slug)
        current = interview_prep.load_profile(path)
        placement = current["placement"]
        assert isinstance(placement, dict)
        result = placement.get("result")
        already_skipped = (
            placement.get("lifecycle_version") == interview_prep.PLACEMENT_V4
            and placement.get("status") == "provisional"
            and isinstance(result, dict)
            and result.get("starting_level") == "learner-selected-baseline"
        )
        if not already_skipped:
            if (
                placement.get("status") == "in_progress"
                and placement.get("lifecycle_version") != interview_prep.PLACEMENT_V4
            ):
                application.discard_interview_placement(slug)
        try:
            accepted = application.accept_interview_curriculum(
                slug,
                action="skip",
                submission_id=request.submission_id if request is not None else None,
                expected_revision=request.expected_revision if request is not None else None,
            )
            value = accepted["profile"]
        except RouteAcceptanceConflictError as error:
            return {"state": "conflict", "error": str(error)}
        except (ValueError, cli.OpenLearnError) as error:
            return {"invalid": True, "error": str(error)}
        return self._placement_view(slug, value)

    def progress(self) -> dict[str, object]:
        courses: list[dict[str, object]] = []
        for card in application.dashboard().courses:
            projected = _card(card)
            if projected.get("is_interview"):
                coverage = projected["coverage"]
                readiness = projected["readiness"]
                assert isinstance(coverage, dict) and isinstance(readiness, dict)
                courses.append(
                    {
                        **projected,
                        "known": coverage["covered"],
                        "total": coverage["total"],
                        "units": [],
                        "due_reviews": readiness["due"],
                    }
                )
            else:
                courses.append(
                    {
                        **projected,
                        "known": card.progress.known,
                        "total": card.progress.total,
                        "units": [vars(unit) for unit in card.progress.units],
                        "due_reviews": card.progress.reviews.due_today,
                    }
                )
        return {"courses": courses}

    def due_reviews(self) -> dict[str, object]:
        items: list[dict[str, object]] = []
        for card in application.dashboard().courses:
            topic = cli.read_topic_stats(card.slug)
            items.extend({"slug": card.slug, "course": card.title, **item} for item in cli.due_review_items(topic.metadata))
        return {"items": items, "count": len(items)}

    def grade_review(self, request: ReviewGradeRequest) -> dict[str, object]:
        topic = cli.read_topic_stats(request.slug)
        due = next(
            (
                item
                for item in cli.due_review_items(topic.metadata)
                if item.get("concept") == request.concept and item.get("due") == request.due
            ),
            None,
        )
        if due is None:
            return {"state": "conflict", "error": "This review changed elsewhere. Reload to continue."}
        cli.schedule_review_outcomes(request.slug, [(due, request.result)])
        return {"ok": True}

    def data_summary(self) -> dict[str, object]:
        inventory = application.data_inventory()
        return {
            **inventory.summary(),
            "confirmations": data_management.confirmation_phrases(),
        }

    def manage_data(self, request: DataManagementRequest) -> dict[str, object]:
        action = request.action
        archive = Path(request.archive)
        destination = Path(request.destination)
        include_credentials = request.include_credentials
        credential_confirmation = request.credential_confirmation
        confirmation = request.confirmation
        home = cli.project_home()
        try:
            if action in {"backup", "export"}:
                result = data_management.create_backup(
                    home,
                    archive,
                    include_credentials=include_credentials,
                    credential_confirmation=credential_confirmation,
                )
                return {
                    "ok": True,
                    "archive": str(result.archive),
                    "summary": result.inventory.summary(),
                }
            if action == "restore":
                result = data_management.restore_backup(archive, destination)
                return {"ok": True, "home": str(result.home)}
            if action == "move":
                result = data_management.move_home(
                    home,
                    destination,
                    archive,
                    confirmation=confirmation,
                    include_credentials=include_credentials,
                    credential_confirmation=credential_confirmation,
                )
                return {
                    "ok": True,
                    "home": str(result.destination),
                    "source": str(result.source),
                    "cleanup_required": result.cleanup_required,
                    "source_retained": result.source_retained,
                    "message": (
                        f"Data was copied and verified at {result.destination}. "
                        f"The original remains at {result.source}. Set OPENLEARN_HOME "
                        "to the new path, verify it, then explicitly delete the old home."
                    ),
                }
            if action == "reset":
                result = data_management.reset_home(
                    home,
                    archive,
                    confirmation=confirmation,
                    include_credentials=include_credentials,
                    credential_confirmation=credential_confirmation,
                )
                return {"ok": True, "summary": result.summary()}
            if action == "delete":
                data_management.delete_home(
                    home,
                    archive,
                    confirmation=confirmation,
                    include_credentials=include_credentials,
                    credential_confirmation=credential_confirmation,
                )
                return {"ok": True}
            return {"ok": False, "error": "Unsupported data operation."}
        except data_management.DataManagementError as error:
            return {"ok": False, "error": str(error)}
        except OSError:
            return {"ok": False, "error": "The local data operation could not be completed safely."}

    def course_initialization(
        self, slug: str, operation_id: str
    ) -> dict[str, object]:
        try:
            snapshot = application.course(slug)
        except (cli.OpenLearnError, OSError):
            return {"slug": slug, "operation_id": operation_id, "missing": True}
        expected_id = _initialization_id_for_slug(slug)
        if expected_id is None or operation_id != expected_id:
            return {"slug": slug, "operation_id": operation_id, "missing": True}
        result = tutor_service.operation_status(slug, operation_id)
        if result is None:
            return {
                "slug": slug,
                "title": snapshot.card.title,
                "operation_id": operation_id,
                "state": "retryable_error",
                "error": "Your course is saved. Retry preparing the first lesson.",
            }
        return {
            "slug": slug,
            "title": snapshot.card.title,
            "operation_id": operation_id,
            "state": result.status,
            "error": result.error_message or "",
        }

    def retry_course_initialization(
        self, slug: str, operation_id: str
    ) -> dict[str, object]:
        expected_id = _initialization_id_for_slug(slug)
        if expected_id is None or operation_id != expected_id:
            return {"state": "missing", "error": "Course initialization was not found."}
        try:
            projection = application.interview_learning(slug)
        except (cli.OpenLearnError, OSError, ValueError):
            projection = None
        try:
            if projection is not None:
                operation = projection.operation
                if operation.submission_id != operation_id:
                    return {
                        "state": "conflict",
                        "error": "The saved curriculum target changed. Reload the course.",
                    }
                result = application.resume_interview_progression(
                    slug, model=config.configured_model()
                )
            else:
                result = tutor_service.start_turn(
                    slug,
                    _course_initialization_prompt(slug),
                    intent="question",
                    submission_id=operation_id,
                    expected_revision=0,
                    model=config.configured_model(),
                )
        except tutor_service.TutorConflictError as error:
            return {"state": "conflict", "error": str(error)}
        except tutor_service.TutorOperationError:
            result = tutor_service.operation_status(slug, operation_id)
            if result is None:
                return {
                    "state": "retryable_error",
                    "operation_id": operation_id,
                    "error": "Your course is saved. Retry preparing the first lesson.",
                }
        return {
            "state": result.status,
            "operation_id": operation_id,
            "error": result.error_message or "",
        }

    def focus(self, slug: str) -> dict[str, object]:
        try:
            interview_projection = application.interview_learning(slug)
        except (cli.OpenLearnError, OSError, ValueError):
            interview_projection = None
        if interview_projection is not None:
            return _interview_focus_projection(interview_projection)
        try:
            snapshot = application.course(slug)
            topic = cli.read_topic(slug)
        except (cli.OpenLearnError, OSError):
            return {"slug": slug, "missing": True}
        _context, log = cli.split_session_log(topic.body)
        entries = cli.session_entries(log)
        latest_lesson = cli.last_tutor_lesson_entry_from_entries(entries)
        latest = latest_lesson[1] if latest_lesson else None
        answer = latest["response"] if latest else "Your course is ready. Ask the tutor to begin."
        move_title = _lesson_focus_title(topic, answer)
        pending = topic.metadata.get("pending_question")
        prompt = ""
        if isinstance(pending, dict) and isinstance(pending.get("question"), str):
            prompt = _plain_text(str(pending["question"]))
        requires_response = bool(prompt)
        presented_answer = _without_check_section(answer) if prompt else answer
        response_kind, blocks = _present_response(presented_answer)
        if prompt and not blocks:
            response_kind = "Check"
        move_kind = "Current lesson" if response_kind == "Lesson" else response_kind
        state = cli.load_state(slug)
        saved_response = state.get("pending_learner_prompt")
        saved_response = saved_response if isinstance(saved_response, str) else ""
        initialization_id = _initialization_id_for_slug(slug)
        revision = tutor_service.course_revision(slug)
        initialization: dict[str, object] | None = None
        if initialization_id is not None and revision == 0:
            initialization_result = tutor_service.operation_status(slug, initialization_id)
            if initialization_result is None or initialization_result.status != "committed":
                initialization = {
                    "id": initialization_id,
                    "state": (
                        initialization_result.status
                        if initialization_result is not None
                        else "retryable_error"
                    ),
                }
        if _is_course_initialization_prompt(saved_response):
            saved_response = ""
        operation: dict[str, object] | None = None
        internal = state.get("_openlearn_internal")
        if isinstance(internal, dict):
            active = internal.get("active_turn")
            operation_id = active.get("submission_id") if isinstance(active, dict) else None
            if not isinstance(operation_id, str):
                last_error = internal.get("last_turn_error")
                operation_id = (
                    last_error.get("submission_id")
                    if isinstance(last_error, dict) and saved_response
                    else None
                )
            if isinstance(operation_id, str):
                result = tutor_service.operation_status(slug, operation_id)
                if result is not None and result.status != "committed":
                    operation = {
                        "id": operation_id,
                        "state": result.status,
                        "error": result.error_message or "",
                        "error_code": result.error_code or "",
                        "show_provider_recovery": _show_provider_recovery(
                            result.error_code
                        ),
                        "preview_text": _operation_preview(result.preview),
                    }
        return {
            "slug": slug,
            "title": snapshot.card.title,
            "current_unit": move_title,
            "revision": revision,
            "saved_state": "Saved locally",
            "move": {
                "kind": move_kind,
                "title": move_title,
                "blocks": blocks,
                "prompt": prompt,
                "position": f"Step {max(1, revision)}",
            },
            "progress": _focus_progress(snapshot.card.progress),
            "feedback": None,
            "requires_response": requires_response,
            "operation": operation,
            "initialization": initialization,
            "saved_response": saved_response,
        }

    def submit_turn(self, slug: str, request: TutorSubmissionRequest) -> dict[str, object]:
        intent = {
            "answer": "answer",
            "question": "question",
            "stuck": "confusion",
            "skip": "navigation",
            "next": "navigation",
            "practice": "navigation",
        }[request.intent]
        text = request.text.strip()
        if request.intent == "skip":
            text = "Skip this for now and continue with a useful next move."
        elif request.intent == "next":
            text = "Continue to the next useful concept."
        elif request.intent == "practice":
            text = "Practice now using a covered curriculum concept."
        source_fields = (
            request.source_lesson_id,
            request.source_lesson_title,
            request.source_lesson_revision,
        )
        if request.intent in {"question", "stuck"}:
            projection = application.interview_learning(slug)
            supplied_source_fields = tuple(value is not None for value in source_fields)
            if projection is not None and any(supplied_source_fields) and not all(
                supplied_source_fields
            ):
                return {
                    "state": "conflict",
                    "error": (
                        "The visible lesson reference is incomplete. Refresh before asking."
                    ),
                }
            if projection is None:
                source_fields = (None, None, None)
        else:
            source_fields = (None, None, None)
        progression_intent: tutor_service.ProgressionIntent | None = None
        if request.intent in {"skip", "practice"}:
            progression_intent = request.intent
        elif request.intent == "next":
            progression_intent = "continue"
        try:
            result = tutor_service.start_turn(
                slug,
                text,
                intent=intent,  # type: ignore[arg-type]
                submission_id=request.submission_id,
                expected_revision=request.expected_revision,
                model=config.configured_model(),
                session_kind=(
                    cli.SIDE_CHAT_SESSION_KIND
                    if request.intent in {"question", "stuck"}
                    else "chat"
                ),
                progression_intent=progression_intent,
                source_lesson_id=source_fields[0],
                source_lesson_title=source_fields[1],
                source_lesson_revision=source_fields[2],
            )
        except tutor_service.TutorConflictError as error:
            return {"state": "conflict", "error": str(error)}
        except tutor_service.TutorOperationError as error:
            return {"state": "retryable_error", "error": str(error)}
        return {
            "state": result.status,
            "submission_id": result.submission_id,
            "operation_id": result.submission_id,
            "move": _move(result.move),
        }

    def chat(self, slug: str) -> dict[str, object]:
        try:
            source = course_conversation_source(slug)
        except (cli.OpenLearnError, OSError):
            return {"slug": slug, "missing": True}
        body = source["body"]
        assert isinstance(body, str)
        _context, log = cli.split_session_log(body)
        course_revision = int(source["course_revision"])
        return {
            "conversation": _side_conversation(cli.session_entries(log)),
            # ``revision`` remains the course namespace for older clients.
            "revision": course_revision,
            "course_revision": course_revision,
            "chat_revision": int(source["side_chat_revision"]),
        }

    def operation_status(self, slug: str, operation_id: str) -> dict[str, object]:
        result = tutor_service.operation_status(slug, operation_id)
        if result is None:
            return {"state": "retryable_error", "error": "This operation is no longer available."}
        return {
            "state": result.status,
            "error": result.error_message or "",
            "error_code": result.error_code or "",
            "show_provider_recovery": _show_provider_recovery(result.error_code),
            "preview_text": _operation_preview(
                result.preview or (result.move.content if result.move is not None else None)
            ),
        }

    def progression_action(
        self, slug: str, request: ProgressionActionRequest
    ) -> dict[str, object]:
        projection = application.interview_learning(slug)
        if projection is None:
            return {"state": "missing", "error": "Interview curriculum is not prepared."}
        active_id = projection.operation.submission_id
        if active_id != request.operation_id:
            return {
                "state": "stale-conflict",
                "error": "The saved curriculum operation changed. Refresh the lesson.",
            }
        try:
            if request.action == "cancel":
                application.cancel_interview_progression(slug, request.operation_id)
                return {"state": "cancelled"}
            result = application.resume_interview_progression(
                slug, model=config.configured_model()
            )
        except tutor_service.TutorConflictError as error:
            return {"state": "busy", "error": str(error)}
        except tutor_service.TutorOperationError as error:
            return {"state": "provider-error", "error": str(error)}
        return {
            "state": result.status,
            "operation_id": result.submission_id,
            "move": _move(result.move),
        }

    def history(self, slug: str, *, page: int) -> dict[str, object]:
        try:
            topic = cli.read_topic(slug)
        except (cli.OpenLearnError, OSError):
            return {"items": [], "page": page, "has_more": False}
        _context, log = cli.split_session_log(topic.body)
        entries = [
            entry
            for entry in reversed(cli.session_entries(log))
            if entry.get("kind") != cli.SIDE_CHAT_SESSION_KIND
        ]
        page_size = 10
        start = (page - 1) * page_size
        selected = entries[start : start + page_size]
        items = []
        for entry in selected:
            kind, blocks = _present_response(str(entry["response"]))
            prompt = str(entry["prompt"])
            title = "First lesson" if _is_course_initialization_prompt(prompt) else prompt[:100]
            items.append(
                {
                    "kind": kind,
                    "title": title,
                    "blocks": blocks,
                    "content": "\n\n".join(
                        str(block.get("text", ""))
                        if block["kind"] in {"paragraph", "code"}
                        else "\n".join(str(item) for item in block.get("items", []))
                        for block in blocks
                    ),
                }
            )
        return {
            "items": items,
            "page": page,
            "has_more": start + page_size < len(entries),
        }
