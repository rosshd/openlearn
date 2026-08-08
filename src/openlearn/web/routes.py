"""Thin HTTP adapter for openlearn application services."""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from openlearn.constants import QUICK_LEARN_MAX_FILE_BYTES

from .schemas import (
    CodeToolRequest,
    CourseCreateRequest,
    DataManagementRequest,
    FolderSourceRequest,
    GitHubSourceRequest,
    PlacementRequest,
    ProviderSetupRequest,
    ReviewGradeRequest,
    TutorSubmissionRequest,
    VideoToolRequest,
    canonical_slug,
    canonical_uuid,
    public_mapping,
)

router = APIRouter()


async def _call(request: Request, method: str, *args: Any, **kwargs: Any) -> Any:
    operation = getattr(request.app.state.services, method, None)
    if operation is None:
        raise HTTPException(status_code=503, detail="This application operation is unavailable.")
    if inspect.iscoroutinefunction(operation):
        return await operation(*args, **kwargs)
    return await run_in_threadpool(operation, *args, **kwargs)


def _templates(request: Request) -> Any:
    return request.app.state.templates


def _context(request: Request, **values: Any) -> dict[str, Any]:
    return {
        "request": request,
        "csrf_token": request.app.state.security.csrf_token,
        "app_root": request.scope.get("root_path", ""),
        **values,
    }


def _json_error(message: str, status: int = 400, **extra: Any) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message, **extra}, status_code=status)


async def _provider_ready(request: Request) -> bool:
    operation = getattr(request.app.state.services, "ensure_provider_ready", None)
    method = "ensure_provider_ready" if callable(operation) else "provider_status"
    status = public_mapping(await _call(request, method))
    return bool(status.get("ready"))


def _setup_redirect(request: Request) -> RedirectResponse:
    destination = request.url.path
    if request.url.query:
        destination = f"{destination}?{request.url.query}"
    target = request.url_for("setup").include_query_params(next=destination)
    return RedirectResponse(target, status_code=303)


def _safe_setup_destination(request: Request) -> str:
    destination = request.query_params.get("next", "")
    if (
        destination.startswith("/")
        and not destination.startswith("//")
        and "\\" not in destination
        and len(destination) <= 2048
    ):
        return destination
    return request.url_for("dashboard").path


def _setup_required(request: Request, *, next_path: str | None = None) -> JSONResponse:
    setup_url = request.url_for("setup")
    if next_path:
        setup_url = setup_url.include_query_params(next=next_path)
    return _json_error(
        "Validate a model provider before starting model-backed teaching.",
        428,
        state="setup_required",
        setup_url=str(setup_url),
    )


@router.get("/health", response_class=JSONResponse)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> Any:
    return await _dashboard_response(request)


@router.get("/setup", response_class=HTMLResponse, name="setup")
async def setup(request: Request) -> Any:
    status = public_mapping(await _call(request, "provider_status"))
    response = _templates(request).TemplateResponse(
        request,
        "setup.html",
        _context(
            request,
            provider=status,
            setup_destination=_safe_setup_destination(request),
            page_title="Set up openlearn",
        ),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/api/setup", response_class=JSONResponse)
async def save_setup(request: Request) -> JSONResponse:
    try:
        payload = ProviderSetupRequest.model_validate(await request.json())
    except (ValidationError, ValueError):
        return _json_error("Check the highlighted provider details.")
    result = public_mapping(await _call(request, "configure_provider", payload))
    status = 200 if result.get("ok", result.get("ready", False)) else 422
    response = JSONResponse(result, status_code=status)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/dashboard", response_class=HTMLResponse, name="dashboard")
async def dashboard(request: Request) -> Any:
    return await _dashboard_response(request)


async def _dashboard_response(request: Request) -> Any:
    snapshot = public_mapping(await _call(request, "dashboard"))
    return _templates(request).TemplateResponse(
        request,
        "dashboard.html",
        _context(request, dashboard=snapshot, page_title="Your workbench"),
    )


@router.get("/courses/new", response_class=HTMLResponse, name="new_course")
async def new_course(request: Request) -> Any:
    templates = await _call(request, "course_templates")
    return _templates(request).TemplateResponse(
        request,
        "course_create.html",
        _context(request, course_templates=templates, page_title="Start a course"),
    )


@router.post("/api/courses", response_class=JSONResponse)
async def create_course(request: Request) -> JSONResponse:
    try:
        payload = CourseCreateRequest.model_validate(await request.json())
    except (ValidationError, ValueError) as error:
        return _json_error("Add a title and a clear learning goal.", errors=str(error))
    entry_mode_operation = getattr(request.app.state.services, "course_entry_mode", None)
    entry_mode = (
        await _call(request, "course_entry_mode", payload.template_id)
        if callable(entry_mode_operation)
        else None
    )
    if entry_mode != "interview_prep" and not await _provider_ready(request):
        return _setup_required(request)
    result = public_mapping(await _call(request, "create_course", payload))
    if not result.get("ok", False):
        return _json_error(str(result.get("error") or "Course creation failed."), 422)
    if result.get("slug"):
        if result.get("state") == "placement_recommended":
            result["placement_url"] = str(
                request.url_for("placement", slug=result["slug"])
            )
            if not await _provider_ready(request):
                result["setup_url"] = str(
                    request.url_for("setup").include_query_params(
                        next=request.url_for("placement", slug=result["slug"]).path
                    )
                )
            return JSONResponse(result)
        result.setdefault(
            "initialization_url",
            str(
                request.url_for(
                    "course_initializing",
                    slug=result["slug"],
                    operation_id=result["operation_id"],
                )
            ),
        )
    return JSONResponse(result, status_code=202 if result.get("operation_id") else 200)


@router.get(
    "/courses/{slug}/initializing/{operation_id}",
    response_class=HTMLResponse,
    name="course_initializing",
)
async def course_initializing(request: Request, slug: str, operation_id: str) -> Any:
    if not await _provider_ready(request):
        return _setup_redirect(request)
    try:
        slug = canonical_slug(slug)
        operation_id = canonical_uuid(operation_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Course initialization not found") from error
    snapshot = public_mapping(await _call(request, "course_initialization", slug, operation_id))
    if snapshot.get("missing"):
        raise HTTPException(status_code=404, detail="Course initialization not found")
    if snapshot.get("state") == "committed":
        return RedirectResponse(request.url_for("focus", slug=slug), status_code=303)
    response = _templates(request).TemplateResponse(
        request,
        "course_initializing.html",
        _context(
            request,
            initialization=snapshot,
            status_url=str(
                request.url_for("operation_status", slug=slug, operation_id=operation_id)
            ),
            retry_url=str(
                request.url_for(
                    "retry_course_initialization",
                    slug=slug,
                    operation_id=operation_id,
                )
            ),
            focus_url=str(request.url_for("focus", slug=slug)),
            page_title="Preparing your first lesson",
        ),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post(
    "/api/courses/{slug}/initialization/{operation_id}/retry",
    response_class=JSONResponse,
    name="retry_course_initialization",
)
async def retry_course_initialization(
    request: Request, slug: str, operation_id: str
) -> JSONResponse:
    if not await _provider_ready(request):
        return _setup_required(request)
    try:
        slug = canonical_slug(slug)
        operation_id = canonical_uuid(operation_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Course initialization not found") from error
    result = public_mapping(await _call(request, "retry_course_initialization", slug, operation_id))
    if result.get("state") == "missing":
        raise HTTPException(status_code=404, detail="Course initialization not found")
    status = 409 if result.get("state") == "conflict" else 202
    return JSONResponse(result, status_code=status)


@router.get("/courses/{slug}", response_class=HTMLResponse, name="focus")
async def focus(request: Request, slug: str) -> Any:
    try:
        slug = canonical_slug(slug)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Course not found") from error
    placement_operation = getattr(request.app.state.services, "placement", None)
    placement_snapshot = (
        public_mapping(await _call(request, "placement", slug))
        if placement_operation is not None
        else {"missing": True}
    )
    if not placement_snapshot.get("missing") and placement_snapshot.get("status") in {
        "not_started",
        "in_progress",
    }:
        return RedirectResponse(request.url_for("placement", slug=slug), status_code=303)
    if not await _provider_ready(request):
        return _setup_redirect(request)
    if not placement_snapshot.get("missing") and placement_snapshot.get("status") in {
        "deferred",
        "provisional",
    }:
        initialized = public_mapping(await _call(request, "start_course_initialization", slug))
        if initialized.get("operation_id") and initialized.get("state") != "committed":
            return RedirectResponse(
                request.url_for(
                    "course_initializing",
                    slug=slug,
                    operation_id=initialized["operation_id"],
                ),
                status_code=303,
            )
    snapshot = public_mapping(await _call(request, "focus", slug))
    if snapshot.get("missing"):
        raise HTTPException(status_code=404, detail="Course not found")
    initialization = snapshot.get("initialization")
    if isinstance(initialization, dict) and initialization.get("id"):
        return RedirectResponse(
            request.url_for(
                "course_initializing",
                slug=slug,
                operation_id=initialization["id"],
            ),
            status_code=303,
        )
    return _templates(request).TemplateResponse(
        request,
        "focus.html",
        _context(request, course=snapshot, page_title=snapshot.get("title", "Focus Bench")),
    )


@router.get("/courses/{slug}/placement", response_class=HTMLResponse, name="placement")
async def placement(request: Request, slug: str) -> Any:
    try:
        slug = canonical_slug(slug)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Course not found") from error
    snapshot = public_mapping(await _call(request, "placement", slug))
    if snapshot.get("missing"):
        raise HTTPException(status_code=404, detail="Placement not found")
    if snapshot.get("status") == "not_started" and not await _provider_ready(request):
        return _setup_redirect(request)
    response = _templates(request).TemplateResponse(
        request,
        "placement.html",
        _context(request, placement=snapshot, page_title="Interview placement"),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/api/courses/{slug}/placement", response_class=JSONResponse)
async def update_placement(request: Request, slug: str) -> JSONResponse:
    try:
        slug = canonical_slug(slug)
        payload = PlacementRequest.model_validate(await request.json())
    except (ValidationError, ValueError):
        return _json_error("Check the placement action and try again.")
    if payload.action == "start" and not await _provider_ready(request):
        return _setup_required(
            request,
            next_path=request.url_for("placement", slug=slug).path,
        )
    if payload.action == "skip":
        result = public_mapping(await _call(request, "skip_placement", slug))
    else:
        result = public_mapping(await _call(request, "update_placement", slug, payload))
    if result.get("state") == "conflict":
        return JSONResponse(result, status_code=409)
    if result.get("missing"):
        raise HTTPException(status_code=404, detail="Placement not found")
    if result.get("invalid"):
        return _json_error(str(result.get("error")), 422)
    if result.get("status") == "provisional":
        if payload.action == "submit":
            return JSONResponse(result)
        if not await _provider_ready(request):
            result["setup_url"] = str(
                request.url_for("setup").include_query_params(
                    next=request.url_for("focus", slug=slug).path
                )
            )
            return JSONResponse(result)
        initialized = public_mapping(await _call(request, "start_course_initialization", slug))
        result.update(
            {
                "operation_id": initialized.get("operation_id"),
                "initialization_url": str(
                    request.url_for(
                        "course_initializing",
                        slug=slug,
                        operation_id=initialized["operation_id"],
                    )
                ),
            }
        )
        return JSONResponse(result, status_code=202)
    return JSONResponse(result)


@router.get("/quick-learn", response_class=HTMLResponse, name="quick_learn")
async def quick_learn(request: Request) -> Any:
    templates = await _call(request, "course_templates")
    return _templates(request).TemplateResponse(
        request,
        "course_create.html",
        _context(
            request,
            course_templates=templates,
            quick_learn=True,
            page_title="Quick Learn",
        ),
    )


@router.get("/progress", response_class=HTMLResponse, name="progress")
async def progress(request: Request) -> Any:
    snapshot = public_mapping(await _call(request, "progress"))
    return _templates(request).TemplateResponse(
        request,
        "progress.html",
        _context(request, progress=snapshot, page_title="Learning progress"),
    )


@router.get("/review", response_class=HTMLResponse, name="review")
async def review(request: Request) -> Any:
    snapshot = public_mapping(await _call(request, "due_reviews"))
    return _templates(request).TemplateResponse(
        request,
        "review.html",
        _context(request, review=snapshot, page_title="Due review"),
    )


@router.post("/api/review", response_class=JSONResponse)
async def grade_review(request: Request) -> JSONResponse:
    try:
        payload = ReviewGradeRequest.model_validate(await request.json())
    except (ValidationError, ValueError):
        return _json_error("Choose a review result and try again.")
    result = public_mapping(await _call(request, "grade_review", payload))
    return JSONResponse(result, status_code=409 if result.get("state") == "conflict" else 200)


@router.get("/data", response_class=HTMLResponse, name="data")
async def data_entry(request: Request) -> Any:
    summary = public_mapping(await _call(request, "data_summary"))
    return _templates(request).TemplateResponse(
        request,
        "data.html",
        _context(request, data=summary, page_title="Local data"),
    )


@router.post("/api/data", response_class=JSONResponse)
async def manage_data(request: Request) -> JSONResponse:
    try:
        payload = DataManagementRequest.model_validate(await request.json())
    except (ValidationError, ValueError):
        return _json_error("Check the data operation and confirmation.")
    result = public_mapping(await _call(request, "manage_data", payload))
    return JSONResponse(result, status_code=422 if not result.get("ok", False) else 200)


@router.post("/api/courses/{slug}/turns", response_class=JSONResponse)
async def submit_turn(request: Request, slug: str) -> JSONResponse:
    if not await _provider_ready(request):
        return _setup_required(request)
    try:
        slug = canonical_slug(slug)
        payload = TutorSubmissionRequest.model_validate(await request.json())
    except (ValidationError, ValueError) as error:
        return _json_error("Check your response and try again.", errors=str(error))
    result = public_mapping(await _call(request, "submit_turn", slug, payload))
    status = 409 if result.get("state") == "conflict" else 202
    return JSONResponse(result, status_code=status)


@router.post("/api/courses/{slug}/tools/video", response_class=JSONResponse)
async def prepare_video(request: Request, slug: str) -> JSONResponse:
    try:
        slug = canonical_slug(slug)
        payload = VideoToolRequest.model_validate(await request.json())
    except (ValidationError, ValueError):
        return _json_error("Paste a valid YouTube video URL.")
    result = public_mapping(await _call(request, "prepare_video", slug, payload))
    if result.get("missing"):
        raise HTTPException(status_code=404, detail="Course not found")
    if not result.get("ok"):
        return _json_error(str(result.get("error") or "That video URL is not supported."), 422)
    return JSONResponse(result)


@router.get("/api/courses/{slug}/tools/code", response_class=JSONResponse)
async def code_state(request: Request, slug: str) -> JSONResponse:
    try:
        slug = canonical_slug(slug)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Course not found") from error
    result = public_mapping(await _call(request, "code_state", slug))
    if result.get("missing"):
        raise HTTPException(status_code=404, detail="Course not found")
    if not result.get("ok"):
        return _json_error(str(result.get("error") or "Code workspace unavailable."), 503)
    return JSONResponse(result)


@router.post("/api/courses/{slug}/tools/code", response_class=JSONResponse)
async def update_code(request: Request, slug: str) -> JSONResponse:
    try:
        slug = canonical_slug(slug)
        payload = CodeToolRequest.model_validate(await request.json())
    except (ValidationError, ValueError):
        return _json_error("Check the Python draft and retry.")
    result = public_mapping(await _call(request, "update_code", slug, payload))
    if result.get("missing"):
        raise HTTPException(status_code=404, detail="Course not found")
    if result.get("conflict"):
        return _json_error(str(result.get("error") or "Draft changed elsewhere."), 409)
    if result.get("invalid"):
        return _json_error(str(result.get("error") or "The Python draft is invalid."), 422)
    if not result.get("ok"):
        return _json_error(str(result.get("error") or "Code workspace unavailable."), 503)
    return JSONResponse(result)


@router.get("/api/courses/{slug}/tools/sources", response_class=JSONResponse)
async def course_sources(request: Request, slug: str) -> JSONResponse:
    try:
        slug = canonical_slug(slug)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Course not found") from error
    result = public_mapping(await _call(request, "course_sources", slug))
    if result.get("missing"):
        raise HTTPException(status_code=404, detail="Course not found")
    return JSONResponse(result)


@router.post("/api/courses/{slug}/tools/sources/file", response_class=JSONResponse)
async def import_file_source(
    request: Request,
    slug: str,
    file: UploadFile = File(...),
) -> JSONResponse:
    try:
        slug = canonical_slug(slug)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Course not found") from error
    filename = file.filename or "selected-file"
    content = await file.read(QUICK_LEARN_MAX_FILE_BYTES + 1)
    await file.close()
    if len(content) > QUICK_LEARN_MAX_FILE_BYTES:
        return _json_error("The selected file exceeds the import size limit.", 413)
    suffix = Path(filename).suffix[:16]
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="openlearn-upload-", suffix=suffix, delete=False) as handle:
            handle.write(content)
            temporary_path = Path(handle.name)
        result = public_mapping(
            await _call(request, "import_file_source", slug, temporary_path, filename)
        )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    if result.get("missing"):
        raise HTTPException(status_code=404, detail="Course not found")
    return JSONResponse(result)


@router.post("/api/courses/{slug}/tools/sources/folder", response_class=JSONResponse)
async def import_folder_source(request: Request, slug: str) -> JSONResponse:
    try:
        slug = canonical_slug(slug)
        payload = FolderSourceRequest.model_validate(await request.json())
    except (ValidationError, ValueError):
        return _json_error("Enter a local folder path.")
    result = public_mapping(await _call(request, "import_folder_source", slug, payload.path))
    if result.get("missing"):
        raise HTTPException(status_code=404, detail="Course not found")
    return JSONResponse(result)


@router.post("/api/courses/{slug}/tools/sources/github", response_class=JSONResponse)
async def import_github_source(request: Request, slug: str) -> JSONResponse:
    try:
        slug = canonical_slug(slug)
        payload = GitHubSourceRequest.model_validate(await request.json())
    except (ValidationError, ValueError):
        return _json_error("Enter a public GitHub repository URL.")
    result = public_mapping(await _call(request, "import_github_source", slug, payload.url))
    if result.get("missing"):
        raise HTTPException(status_code=404, detail="Course not found")
    return JSONResponse(result)


@router.get("/api/courses/{slug}/operations/{operation_id}", response_class=JSONResponse)
async def operation_status(request: Request, slug: str, operation_id: str) -> JSONResponse:
    try:
        slug = canonical_slug(slug)
        operation_id = canonical_uuid(operation_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Operation not found") from error
    result = public_mapping(await _call(request, "operation_status", slug, operation_id))
    response = JSONResponse(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/courses/{slug}/history", response_class=HTMLResponse, name="history")
async def history(request: Request, slug: str, page: int = 1) -> Any:
    if not await _provider_ready(request):
        return _setup_redirect(request)
    try:
        slug = canonical_slug(slug)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Course not found") from error
    snapshot = public_mapping(await _call(request, "history", slug, page=max(1, min(page, 100))))
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(snapshot)
    return _templates(request).TemplateResponse(
        request,
        "history.html",
        _context(request, history=snapshot, course_slug=slug, page_title="Session history"),
    )
