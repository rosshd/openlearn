"""Concrete application-service adapter for the local web interface."""

from __future__ import annotations

import os
import re
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
from openlearn.application import CalibrationContext, CourseCard, CourseCreationRequest
from openlearn.course_templates import CourseTemplateError
from openlearn.courses import (
    CREATION_SUBMISSION_METADATA_KEY,
    CREATION_SUBMISSION_STATE_KEY,
)

from .schemas import (
    CodeToolRequest,
    CourseCreateRequest,
    ProviderSetupRequest,
    PlacementRequest,
    ReviewGradeRequest,
    TutorSubmissionRequest,
    VideoToolRequest,
)


COURSE_INITIALIZATION_PROMPT = "Start my first lesson."


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


def _card(card: CourseCard) -> dict[str, object]:
    return {
        "slug": card.slug,
        "title": card.title,
        "summary": card.goal,
        "current_unit": card.current_focus or "Ready to learn",
        "next_move": card.current_focus,
        "progress": card.progress.percent,
    }


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
    return {
        "kind": move.kind.replace("_", " ").title(),
        "title": "Your next learning move",
        "content": move.content,
        "prompt": move.prompt,
        "position": f"Step {move.revision}",
    }


def _plain_text(value: str) -> str:
    return value.replace("**", "").replace("__", "").strip()


def _present_response(value: str) -> tuple[str, list[dict[str, object]]]:
    """Parse a small safe Markdown subset into explicit presentation blocks."""
    text = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
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


class OpenLearnWebServices:
    """Map interface-neutral openlearn services to browser view models."""

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

    @staticmethod
    def _provider_options() -> list[dict[str, object]]:
        return [
            {
                "id": preset.slug,
                "name": preset.name,
                "base_url": preset.base_url,
                "default_model": preset.default_model,
                "recommended": preset.slug == "openrouter",
            }
            for preset in providers.PROVIDER_PRESETS.values()
        ]

    def configure_provider(self, request: ProviderSetupRequest) -> dict[str, object]:
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
        return {
            "courses": [_card(card) for card in snapshot.courses],
            "active_course": _card(snapshot.resume) if snapshot.resume else None,
            "due_reviews": snapshot.reviews.due_today,
        }

    def course_templates(self) -> list[dict[str, object]]:
        return [
            {
                "id": template.template_id,
                "title": template.name,
                "description": template.goal,
                "tags": list(template.tags),
                "entry_mode": template.entry_mode,
            }
            for template in application.templates().templates
        ]

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
                    model=cli.configured_model(),
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
        return self._start_course_initialization(slug, initialization_id)

    def _start_course_initialization(
        self, slug: str, initialization_id: str | None = None
    ) -> dict[str, object]:
        initialization_id = initialization_id or _initialization_id_for_slug(slug)
        if initialization_id is None:
            return {"ok": False, "error": "Course initialization is unavailable."}
        existing_operation = tutor_service.operation_status(slug, initialization_id)
        if existing_operation is not None:
            return {
                "ok": True,
                "slug": slug,
                "operation_id": initialization_id,
                "state": existing_operation.status,
            }
        try:
            operation = tutor_service.start_turn(
                slug,
                COURSE_INITIALIZATION_PROMPT,
                intent="question",
                submission_id=initialization_id,
                expected_revision=0,
                model=config.configured_model(),
            )
        except tutor_service.TutorOperationError:
            operation = tutor_service.operation_status(slug, initialization_id)
        state = operation.status if operation is not None else "retryable_error"
        return {
            "ok": True,
            "slug": slug,
            "operation_id": initialization_id,
            "state": state,
        }

    def start_course_initialization(self, slug: str) -> dict[str, object]:
        return self._start_course_initialization(slug)

    @staticmethod
    def _placement_view(slug: str, value: dict[str, object]) -> dict[str, object]:
        placement = value["placement"]
        assert isinstance(placement, dict)
        draft = placement.get("draft")
        return {
            "slug": slug,
            "status": placement.get("status"),
            "next_stage": placement.get("next_stage"),
            "updated_at": placement.get("updated_at"),
            "attempt_id": placement.get("attempt_id"),
            "draft": draft if isinstance(draft, dict) else None,
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
            value = cli.sync_interview_placement(slug)
        except cli.OpenLearnError:
            value = interview_prep.load_profile(cli.interview_profile_path(slug))
        return {"title": snapshot.card.title, **self._placement_view(slug, value)}

    def update_placement(self, slug: str, request: PlacementRequest) -> dict[str, object]:
        if self._interview_course(slug) is None:
            return {"slug": slug, "missing": True}
        path = cli.interview_profile_path(slug)
        value = interview_prep.load_profile(path)
        placement = value["placement"]
        assert isinstance(placement, dict)
        if request.action == "start":
            value = self._start_reasoning_placement(slug, path)
        elif request.action == "restart":
            if placement.get("status") == "in_progress":
                # The CLI still owns the activity/profile reconciliation transaction.
                # Keep that compatibility call isolated here until it moves into application.py.
                cli._discard_interview_placement(slug, path)  # type: ignore[attr-defined]
            value = self._start_reasoning_placement(slug, path)
        elif request.action == "defer":
            if placement.get("status") in {"in_progress", "deferred"}:
                return self._placement_view(slug, value)
            queued: list[tuple[str, dict[str, object]]] = []
            with cli.interview_profile_write_lock(slug):
                value = interview_prep.defer_placement(
                    path, lambda event_type, data: queued.append((event_type, data))
                )
            for event_type, data in queued:
                cli.log_event(slug, event_type, data)
        else:
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
                activity = cli._current_interview_activity(slug)  # type: ignore[attr-defined]
                if activity is None:
                    return {"state": "conflict", "error": "Placement changed elsewhere. Reload to continue."}
                evidence_id = interview_prep.placement_evidence_id(placement, stage)
                cli.record_topic_activity_evidence(
                    slug,
                    activity,
                    "interview_observation",
                    {"stage": stage, "response": "\n".join(str(line) for line in lines)},
                    evidence_id=evidence_id,
                )
                value = cli.sync_interview_placement(slug)
            else:
                with cli.interview_profile_write_lock(slug):
                    value = interview_prep.skip_optional_placement_stage(path, stage)
                value = cli.sync_interview_placement(slug)
        return self._placement_view(slug, value)

    @staticmethod
    def _start_reasoning_placement(slug: str, path: Path) -> dict[str, object]:
        """Centralize the legacy CLI activity bridge used by both start and restart."""
        activity = cli._begin_interview_activity(  # type: ignore[attr-defined]
            slug, lifecycle_version=interview_prep.PLACEMENT_V3
        )
        with cli.interview_profile_write_lock(slug):
            return interview_prep.start_placement(
                path,
                activity_id=str(activity["activity_id"]),
                lifecycle_version=interview_prep.PLACEMENT_V3,
            )

    def skip_placement(self, slug: str) -> dict[str, object]:
        current = self.placement(slug)
        if current.get("status") == "provisional":
            return current
        if current.get("status") != "in_progress":
            current = self.update_placement(slug, PlacementRequest(action="start"))
        while current.get("status") == "in_progress":
            stage = str(current["next_stage"])
            current = self.update_placement(
                slug, PlacementRequest(action="skip_stage", stage=stage)
            )
        return current

    def progress(self) -> dict[str, object]:
        return {
            "courses": [
                {
                    **_card(card),
                    "known": card.progress.known,
                    "total": card.progress.total,
                    "units": [vars(unit) for unit in card.progress.units],
                    "due_reviews": card.progress.reviews.due_today,
                }
                for card in application.dashboard().courses
            ]
        }

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
            result = tutor_service.start_turn(
                slug,
                COURSE_INITIALIZATION_PROMPT,
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
            snapshot = application.course(slug)
            topic = cli.read_topic(slug)
        except (cli.OpenLearnError, OSError):
            return {"slug": slug, "missing": True}
        _context, log = cli.split_session_log(topic.body)
        entries = cli.session_entries(log)
        latest = entries[-1] if entries else None
        answer = latest["response"] if latest else "Your course is ready. Ask the tutor to begin."
        response_kind, blocks = _present_response(answer)
        pending = topic.metadata.get("pending_question")
        prompt = ""
        if isinstance(pending, dict) and isinstance(pending.get("question"), str):
            prompt = str(pending["question"])
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
        if saved_response == COURSE_INITIALIZATION_PROMPT:
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
                    }
        return {
            "slug": slug,
            "title": snapshot.card.title,
            "current_unit": snapshot.card.current_focus or "Current lesson",
            "revision": revision,
            "saved_state": "Saved locally",
            "move": {
                "kind": response_kind,
                "title": "Your next learning move",
                "blocks": blocks,
                "prompt": prompt,
                "position": f"Step {max(1, revision)}",
            },
            "progress": {
                "percent": snapshot.card.progress.percent,
                "summary": (
                    f"{snapshot.card.progress.known} of "
                    f"{snapshot.card.progress.total} tracked concepts are known."
                ),
            },
            "feedback": None,
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
        }[request.intent]
        text = request.text.strip()
        if request.intent == "skip":
            text = "Skip this for now and continue with a useful next move."
        elif request.intent == "next":
            text = "Continue to the next useful concept."
        try:
            result = tutor_service.start_turn(
                slug,
                text,
                intent=intent,  # type: ignore[arg-type]
                submission_id=request.submission_id,
                expected_revision=request.expected_revision,
                model=config.configured_model(),
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

    def operation_status(self, slug: str, operation_id: str) -> dict[str, object]:
        result = tutor_service.operation_status(slug, operation_id)
        if result is None:
            return {"state": "retryable_error", "error": "This operation is no longer available."}
        return {
            "state": result.status,
            "error": result.error_message or "",
            "move": _move(result.move),
        }

    def history(self, slug: str, *, page: int) -> dict[str, object]:
        try:
            topic = cli.read_topic(slug)
        except (cli.OpenLearnError, OSError):
            return {"items": [], "page": page, "has_more": False}
        _context, log = cli.split_session_log(topic.body)
        entries = list(reversed(cli.session_entries(log)))
        page_size = 10
        start = (page - 1) * page_size
        selected = entries[start : start + page_size]
        items = []
        for entry in selected:
            kind, blocks = _present_response(str(entry["response"]))
            prompt = str(entry["prompt"])
            title = "First lesson" if prompt == COURSE_INITIALIZATION_PROMPT else prompt[:100]
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
