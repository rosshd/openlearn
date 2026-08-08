"""FastAPI application factory for the local openlearn web interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .routes import router
from .security import BrowserSecurity, LocalSecurityMiddleware


@runtime_checkable
class WebServices(Protocol):
    """Interface-neutral application operations consumed by HTTP routes.

    Implementations may expose synchronous functions or async coroutines.
    Application results should be dataclasses, Pydantic models, or mappings.
    """

    def provider_status(self) -> Any: ...

    def configure_provider(self, request: Any) -> Any: ...

    def dashboard(self) -> Any: ...

    def course_templates(self) -> Any: ...

    def create_course(self, request: Any) -> Any: ...

    def course_initialization(self, slug: str, operation_id: str) -> Any: ...

    def retry_course_initialization(self, slug: str, operation_id: str) -> Any: ...

    def focus(self, slug: str) -> Any: ...

    def submit_turn(self, slug: str, request: Any) -> Any: ...

    def operation_status(self, slug: str, operation_id: str) -> Any: ...

    def history(self, slug: str, *, page: int) -> Any: ...


@dataclass
class PlaceholderServices:
    """Safe empty adapter used before application services are wired."""

    reason: str = "Provider setup is required before teaching can begin."
    _courses: list[dict[str, Any]] = field(default_factory=list)

    def provider_status(self) -> dict[str, Any]:
        return {"ready": False, "managed": False, "providers": [], "reason": self.reason}

    def configure_provider(self, request: Any) -> dict[str, Any]:
        return {"ok": False, "error": self.reason}

    def dashboard(self) -> dict[str, Any]:
        return {"courses": self._courses, "active_course": None, "due_reviews": 0}

    def course_templates(self) -> list[dict[str, Any]]:
        return []

    def create_course(self, request: Any) -> dict[str, Any]:
        return {"ok": False, "error": "Course services are not available yet.", "fields": request}

    def course_initialization(self, slug: str, operation_id: str) -> dict[str, Any]:
        return {"slug": slug, "operation_id": operation_id, "missing": True}

    def retry_course_initialization(self, slug: str, operation_id: str) -> dict[str, Any]:
        return {
            "slug": slug,
            "operation_id": operation_id,
            "state": "retryable_error",
            "error": "Course services are not available yet.",
        }

    def focus(self, slug: str) -> dict[str, Any]:
        return {"slug": slug, "missing": True}

    def submit_turn(self, slug: str, request: Any) -> dict[str, Any]:
        return {"ok": False, "error": "Tutor services are not available yet."}

    def operation_status(self, slug: str, operation_id: str) -> dict[str, Any]:
        return {"state": "retryable_error", "error": "Tutor services are not available yet."}

    def history(self, slug: str, *, page: int) -> dict[str, Any]:
        return {"items": [], "page": page, "has_more": False}


def create_app(
    services: WebServices | None = None,
    *,
    testing: bool = False,
    security: BrowserSecurity | None = None,
) -> FastAPI:
    """Build the loopback web application with injected application services."""

    package_dir = Path(__file__).resolve().parent
    template_environment = Environment(
        loader=FileSystemLoader(str(package_dir / "templates")),
        autoescape=select_autoescape(("html", "xml"), default_for_string=True),
    )
    templates = Jinja2Templates(env=template_environment)
    app = FastAPI(
        title="openlearn",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    if services is None:
        from .services import OpenLearnWebServices

        services = OpenLearnWebServices()
    app.state.services = services
    app.state.templates = templates
    app.state.security = security or BrowserSecurity.local(
        allow_test_client=testing,
        trust_test_client=testing,
    )
    app.mount("/static", StaticFiles(directory=str(package_dir / "static")), name="static")
    app.include_router(router)
    app.add_middleware(LocalSecurityMiddleware, security=app.state.security)
    return app
